"""Named snapshots of the observable record stream, bound to a generation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..clock import parse_stamp, stamp
from ..errors import InvalidRequest, NameConflict, RecordNotFound, StaleRecord
from .documents import DocumentStore

SNAPSHOT_DOC = "records.snapshots"
HISTORY_LIMIT = 20


@dataclass(frozen=True)
class Snapshot:
    """One point of the record stream that a restore may return to."""

    label: str
    at: str
    generation: int
    expires_at: str
    sequence: int
    records: int
    actor: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "at": self.at,
            "generation": self.generation,
            "expires_at": self.expires_at,
            "sequence": self.sequence,
            "records": self.records,
            "actor": self.actor,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Snapshot":
        return cls(
            label=str(raw.get("label", "")),
            at=str(raw.get("at", "")),
            generation=int(raw.get("generation", 0)),
            expires_at=str(raw.get("expires_at", "")),
            sequence=int(raw.get("sequence", 0)),
            records=int(raw.get("records", 0)),
            actor=str(raw.get("actor", "")),
        )

    def expiring(self, moment: datetime) -> bool:
        return moment >= parse_stamp(self.expires_at)

    def age_seconds(self, moment: datetime) -> float:
        return max(0.0, (moment - parse_stamp(self.at)).total_seconds())


class SnapshotStore:
    """Records snapshots of the visible stream and refuses stale restores."""

    def __init__(self, store: DocumentStore, *, history_limit: int = HISTORY_LIMIT) -> None:
        self._store = store
        self._history_limit = max(1, history_limit)
        self.doc_id = SNAPSHOT_DOC

    def _payload(self) -> dict[str, Any]:
        document = self._store.try_load(self.doc_id)
        return {} if document is None else document.payload

    def _all(self) -> dict[str, Snapshot]:
        raw = self._payload().get("snapshots", {})
        if not isinstance(raw, dict):
            return {}
        return {key: Snapshot.from_dict(value) for key, value in raw.items() if isinstance(value, dict)}

    def capture(
        self,
        label: str,
        moment: datetime,
        *,
        generation: int,
        sequence: int,
        records: int,
        ttl_seconds: float,
        actor: str = "",
    ) -> Snapshot:
        name = label.strip()
        if not name:
            raise InvalidRequest("a snapshot needs a label")
        if ttl_seconds <= 0:
            raise InvalidRequest("a snapshot needs a positive lifetime")
        snapshots = self._all()
        if name in snapshots:
            raise NameConflict("that snapshot label is already used", label=name)
        snapshot = Snapshot(
            label=name,
            at=stamp(moment),
            generation=int(generation),
            expires_at=stamp(moment + timedelta(seconds=float(ttl_seconds))),
            sequence=int(sequence),
            records=int(records),
            actor=actor.strip(),
        )
        snapshots[name] = snapshot
        self._save(snapshots, moment)
        return snapshot

    def get(self, label: str) -> Snapshot:
        snapshot = self._all().get(label)
        if snapshot is None:
            raise RecordNotFound("no such snapshot", label=label)
        return snapshot

    def require_fresh(self, label: str, moment: datetime, generation: int) -> Snapshot:
        snapshot = self.get(label)
        if snapshot.generation != int(generation):
            raise StaleRecord(
                f"snapshot {label}",
                f"snapshot is for generation {snapshot.generation}, live generation is {generation}",
            )
        if snapshot.expiring(moment):
            raise StaleRecord(f"snapshot {label}", f"snapshot expired at {snapshot.expires_at}")
        return snapshot

    def list(self) -> list[Snapshot]:
        return sorted(self._all().values(), key=lambda item: item.at)

    def history(self) -> list[dict[str, Any]]:
        entries = self._payload().get("retired", [])
        return [dict(entry) for entry in entries if isinstance(entry, dict)]

    def retire(self, label: str, moment: datetime) -> Snapshot:
        """Move a snapshot out of the live set so a restore can no longer use it."""

        snapshots = self._all()
        snapshot = snapshots.pop(label, None)
        if snapshot is None:
            raise RecordNotFound("no such snapshot", label=label)
        retired = list(self._payload().get("retired", []))
        retired.append(snapshot.as_dict())
        payload = {
            "snapshots": {key: item.as_dict() for key, item in snapshots.items()},
            "retired": retired[-self._history_limit :],
        }
        self._store.save(self.doc_id, payload, moment)
        return snapshot

    def _save(self, snapshots: dict[str, Snapshot], moment: datetime) -> None:
        retired = list(self._payload().get("retired", []))
        payload = {
            "snapshots": {key: item.as_dict() for key, item in snapshots.items()},
            "retired": retired[-self._history_limit :],
        }
        self._store.save(self.doc_id, payload, moment)
