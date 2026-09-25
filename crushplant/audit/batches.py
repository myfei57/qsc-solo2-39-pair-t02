"""Production batch registry with one code per unit and kind."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..clock import stamp
from ..errors import InvalidRequest, NameConflict, RecordNotFound, StateConflict
from ..store.documents import DocumentStore
from .ledger import OUTCOME_OK, AuditLedger

BATCH_DOC = "line.batches"
BATCH_OPEN = "open"
BATCH_CLOSED = "closed"


@dataclass(frozen=True)
class BatchRecord:
    """One production batch, unique across the site."""

    code: str
    unit: str
    kind: str
    state: str
    opened_at: str
    opened_by: str
    closed_at: str = ""
    closed_by: str = ""
    tonnes: float = 0.0
    settled_at: str = ""
    settled_by: str = ""
    settled_tonnes: float = 0.0
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "unit": self.unit,
            "kind": self.kind,
            "state": self.state,
            "opened_at": self.opened_at,
            "opened_by": self.opened_by,
            "closed_at": self.closed_at,
            "closed_by": self.closed_by,
            "tonnes": self.tonnes,
            "settled_at": self.settled_at,
            "settled_by": self.settled_by,
            "settled_tonnes": self.settled_tonnes,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BatchRecord":
        return cls(
            code=str(raw.get("code", "")),
            unit=str(raw.get("unit", "")),
            kind=str(raw.get("kind", "")),
            state=str(raw.get("state", BATCH_OPEN)),
            opened_at=str(raw.get("opened_at", "")),
            opened_by=str(raw.get("opened_by", "")),
            closed_at=str(raw.get("closed_at", "")),
            closed_by=str(raw.get("closed_by", "")),
            tonnes=float(raw.get("tonnes", 0.0)),
            settled_at=str(raw.get("settled_at", "")),
            settled_by=str(raw.get("settled_by", "")),
            settled_tonnes=float(raw.get("settled_tonnes", 0.0)),
            notes=str(raw.get("notes", "")),
        )

    def is_open(self) -> bool:
        return self.state == BATCH_OPEN

    def is_settled(self) -> bool:
        return bool(self.settled_at)

    def describe(self) -> str:
        if self.is_open():
            return f"{self.code} open on {self.unit}"
        if not self.is_settled():
            return f"{self.code} closed, not settled"
        return f"{self.code} settled at {self.settled_tonnes} t"


class BatchRegistry:
    """Opens and closes batches, refusing a code that is already in use."""

    def __init__(self, store: DocumentStore, ledger: AuditLedger) -> None:
        self._store = store
        self._ledger = ledger
        self.doc_id = BATCH_DOC

    def _batches(self) -> dict[str, BatchRecord]:
        document = self._store.try_load(self.doc_id)
        if document is None:
            return {}
        raw = document.payload.get("batches", {})
        if not isinstance(raw, dict):
            return {}
        return {key: BatchRecord.from_dict(value) for key, value in raw.items() if isinstance(value, dict)}

    def _save(self, batches: dict[str, BatchRecord], moment: datetime) -> None:
        self._store.save(
            self.doc_id,
            {"batches": {key: value.as_dict() for key, value in batches.items()}},
            moment,
        )

    def open(
        self,
        code: str,
        unit: str,
        kind: str,
        moment: datetime,
        actor: str,
        *,
        notes: str = "",
    ) -> BatchRecord:
        name = code.strip().upper()
        if not name:
            raise InvalidRequest("a batch needs a code")
        if not kind.strip():
            raise InvalidRequest("a batch needs a kind", code=name)
        batches = self._batches()
        if name in batches:
            existing = batches[name]
            raise NameConflict(
                "that batch code is already used",
                code=name,
                existing_unit=existing.unit,
                existing_state=existing.state,
            )
        for open_batch in self.open_batches(unit):
            raise StateConflict(
                "that line already has an open batch",
                unit=unit,
                code=open_batch.code,
                opened_at=open_batch.opened_at,
            )
        record = BatchRecord(
            code=name,
            unit=unit,
            kind=kind.strip(),
            state=BATCH_OPEN,
            opened_at=stamp(moment),
            opened_by=actor.strip(),
            notes=notes.strip(),
        )
        batches[name] = record
        self._save(batches, moment)
        self._ledger.record(
            unit,
            "batch.open",
            OUTCOME_OK,
            actor,
            moment,
            subject=name,
            kind=record.kind,
        )
        return record

    def close(
        self,
        code: str,
        moment: datetime,
        actor: str,
        *,
        tonnes: float | None = None,
    ) -> BatchRecord:
        name = code.strip().upper()
        batches = self._batches()
        record = batches.get(name)
        if record is None:
            raise RecordNotFound("no such batch", code=name)
        if not record.is_open():
            raise StateConflict("that batch is already closed", code=name, closed_at=record.closed_at)
        closed = BatchRecord(
            **{
                **record.as_dict(),
                "state": BATCH_CLOSED,
                "closed_at": stamp(moment),
                "closed_by": actor.strip(),
                "tonnes": float(record.tonnes if tonnes is None else tonnes),
            }
        )
        batches[name] = closed
        self._save(batches, moment)
        self._ledger.record(
            record.unit,
            "batch.close",
            OUTCOME_OK,
            actor,
            moment,
            subject=name,
            tonnes=closed.tonnes,
        )
        return closed

    def settle(
        self,
        code: str,
        moment: datetime,
        actor: str,
        *,
        tonnes: float | None = None,
    ) -> BatchRecord:
        """Settle a closed batch, which is the only step that books its tonnage."""

        name = code.strip().upper()
        batches = self._batches()
        record = batches.get(name)
        if record is None:
            raise RecordNotFound("no such batch", code=name)
        if record.is_open():
            raise StateConflict("a batch has to be closed before it is settled", code=name)
        if record.is_settled():
            raise StateConflict("that batch is already settled", code=name, settled_at=record.settled_at)
        booked = record.tonnes if tonnes is None else float(tonnes)
        if booked < 0:
            raise InvalidRequest("a settled tonnage must not be negative", code=name)
        settled = BatchRecord(
            **{
                **record.as_dict(),
                "settled_at": stamp(moment),
                "settled_by": actor.strip(),
                "settled_tonnes": booked,
            }
        )
        batches[name] = settled
        self._save(batches, moment)
        self._ledger.record(
            record.unit,
            "batch.settle",
            OUTCOME_OK,
            actor,
            moment,
            subject=name,
            tonnes=settled.settled_tonnes,
        )
        return settled

    def get(self, code: str) -> BatchRecord:
        record = self._batches().get(code.strip().upper())
        if record is None:
            raise RecordNotFound("no such batch", code=code)
        return record

    def open_batches(self, unit: str | None = None) -> list[BatchRecord]:
        return [
            record
            for record in self._batches().values()
            if record.is_open() and (unit is None or record.unit == unit)
        ]

    def codes(self) -> list[str]:
        return sorted(self._batches())

    def open_for(self, unit: str) -> BatchRecord | None:
        """The batch a line is currently working against, if there is one."""

        found = self.open_batches(unit)
        return found[0] if found else None

    def unsettled(self) -> list[BatchRecord]:
        return [
            record
            for record in self._batches().values()
            if not record.is_open() and not record.is_settled()
        ]

    def summary(self) -> dict[str, Any]:
        batches = self._batches()
        open_codes = sorted(record.code for record in batches.values() if record.is_open())
        return {
            "count": len(batches),
            "open": len(open_codes),
            "open_codes": open_codes,
            "settled": sum(1 for record in batches.values() if record.is_settled()),
            "unsettled": [record.code for record in self.unsettled()],
            "codes": sorted(batches),
        }
