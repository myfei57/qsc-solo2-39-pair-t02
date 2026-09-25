"""Operation ledger written through the commit watermark."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator

from ..clock import parse_stamp
from ..errors import InvalidRequest
from ..store.records import RecordStream
from .query import AuditQuery

OUTCOME_OK = "ok"
OUTCOME_BLOCKED = "blocked"
OUTCOME_FAILED = "failed"
OUTCOME_TRIPPED = "tripped"
AUDIT_KIND = "audit"


@dataclass(frozen=True)
class AuditEntry:
    """One operator-visible step of a sequence."""

    sequence: int
    at: str
    unit: str
    action: str
    outcome: str
    actor: str
    subject: str
    detail: dict[str, Any]

    def moment(self) -> datetime:
        return parse_stamp(self.at)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "at": self.at,
            "unit": self.unit,
            "action": self.action,
            "outcome": self.outcome,
            "actor": self.actor,
            "subject": self.subject,
            "detail": self.detail,
        }


class AuditLedger:
    """Appends every step as a record and keeps the watermark in step.

    A step outside a batch is published as soon as it is written, so a restart
    never loses an operator action.  Inside a batch the steps are staged and the
    batch publishes exactly the records it wrote, which is what makes a
    half-finished sequence unobservable to a reader.
    """

    def __init__(self, stream: RecordStream) -> None:
        self._stream = stream
        self._depth = 0
        self._open_unit = ""
        self._last_sequence = 0
        self._last: AuditEntry | None = None

    @property
    def stream(self) -> RecordStream:
        return self._stream

    @property
    def open_unit(self) -> str:
        return self._open_unit

    def record(
        self,
        unit: str,
        action: str,
        outcome: str,
        actor: str,
        moment: datetime,
        *,
        subject: str = "",
        **detail: Any,
    ) -> AuditEntry:
        if not action.strip():
            raise InvalidRequest("an audit entry needs an action")
        record = self._stream.stage(
            AUDIT_KIND,
            moment,
            unit=unit,
            actor=actor,
            subject=subject,
            payload={"action": action.strip(), "outcome": outcome, "detail": dict(detail)},
        )
        entry = self._entry(record)
        self._last = entry
        self._last_sequence = record.sequence
        if self._depth == 0:
            self._publish(moment, actor)
        return entry

    def blocked(
        self,
        unit: str,
        action: str,
        actor: str,
        moment: datetime,
        error: BaseException,
        *,
        subject: str = "",
    ) -> AuditEntry:
        """Write down a step the interlocks refused, then re-raise it."""

        entry = self.record(
            unit,
            action,
            OUTCOME_BLOCKED,
            actor,
            moment,
            subject=subject,
            **self._reason(error),
        )
        return entry

    @contextmanager
    def batch(self, unit: str, actor: str) -> Iterator["AuditLedger"]:
        """Stage several steps and publish them as one committed unit."""

        self._depth += 1
        if self._depth == 1:
            self._open_unit = unit
        try:
            yield self
        finally:
            self._depth -= 1
            if self._depth == 0:
                self._open_unit = ""
                if self._stream.pending():
                    moment = self._last.moment() if self._last is not None else None
                    if moment is not None:
                        self._publish(moment, actor)

    def pending(self) -> int:
        """Steps staged inside an open batch that no reader can see yet."""

        return len(self._stream.pending())

    def entries(
        self,
        query: AuditQuery | None = None,
        *,
        unit: str = "",
        action: str = "",
        outcome: str = "",
        subject: str = "",
        limit: int | None = None,
    ) -> list[AuditEntry]:
        selected = query or AuditQuery(
            unit=unit,
            action=action,
            outcome=outcome,
            subject=subject,
            limit=limit,
        )
        return selected.apply(self._all())

    def tail(self, limit: int = 20) -> list[AuditEntry]:
        return self._all()[-limit:] if limit > 0 else []

    def for_unit(self, unit: str, limit: int | None = None) -> list[AuditEntry]:
        return self.entries(AuditQuery(unit=unit, limit=limit))

    def actions(self) -> list[str]:
        return self._distinct("action")

    def units(self) -> list[str]:
        return [unit for unit in self._distinct("unit") if unit]

    def counts(self) -> dict[str, int]:
        return self._tally("action")

    def outcomes(self) -> dict[str, int]:
        return self._tally("outcome")

    def last_action(self, unit: str) -> str:
        found = self.entries(AuditQuery(unit=unit, limit=1))
        return "" if not found else found[-1].action

    def summary(self) -> dict[str, Any]:
        return {
            "entries": len(self._all()),
            "open_batch": self._depth > 0,
            "open_unit": self._open_unit,
            "outcomes": self.outcomes(),
            "actions": len(self.counts()),
            "units": self.units(),
        }

    # ------------------------------------------------------------- internals
    def _publish(self, moment: datetime, actor: str) -> None:
        self._stream.commit_through(self._last_sequence, moment, actor)

    def _all(self) -> list[AuditEntry]:
        return [self._entry(record) for record in self._stream.visible() if record.kind == AUDIT_KIND]

    def _distinct(self, attribute: str) -> list[str]:
        seen: list[str] = []
        for entry in self._all():
            value = getattr(entry, attribute)
            if value and value not in seen:
                seen.append(value)
        return seen

    def _tally(self, attribute: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self._all():
            value = getattr(entry, attribute)
            counts[value] = counts.get(value, 0) + 1
        return counts

    @staticmethod
    def _reason(error: BaseException) -> dict[str, Any]:
        details = getattr(error, "details", {})
        return {
            "error": getattr(error, "code", type(error).__name__),
            "error_message": getattr(error, "message", str(error)),
            "refused": details,
        }

    @staticmethod
    def _entry(record: Any) -> AuditEntry:
        payload = record.payload
        detail = payload.get("detail")
        return AuditEntry(
            sequence=record.sequence,
            at=record.at,
            unit=record.unit,
            action=str(payload.get("action", "")),
            outcome=str(payload.get("outcome", "")),
            actor=record.actor,
            subject=record.subject,
            detail=dict(detail) if isinstance(detail, dict) else {},
        )
