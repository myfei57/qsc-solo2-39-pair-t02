"""The crusher state record that has to reach the journal before feeding."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..clock import parse_stamp, stamp
from ..errors import DurabilityError, InvalidRequest
from ..store.documents import DocumentStore
from ..store.records import RecordStream

JAW_STATE_KIND = "jaw-state"
STOPPED = "stopped"
RUNNING = "running"


@dataclass(frozen=True)
class JawState:
    """One revision of the crusher state."""

    unit: str
    state: str
    gap_mm: float
    feed_mm: float
    product_mm: float
    reduction: float
    revision: int
    drafted_at: str
    committed_sequence: int

    def committed(self) -> bool:
        return self.committed_sequence > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit,
            "state": self.state,
            "gap_mm": self.gap_mm,
            "feed_mm": self.feed_mm,
            "product_mm": self.product_mm,
            "reduction": self.reduction,
            "revision": self.revision,
            "drafted_at": self.drafted_at,
            "committed_sequence": self.committed_sequence,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any], unit: str) -> "JawState":
        return cls(
            unit=str(raw.get("unit", unit)),
            state=str(raw.get("state", STOPPED)),
            gap_mm=float(raw.get("gap_mm", 0.0)),
            feed_mm=float(raw.get("feed_mm", 0.0)),
            product_mm=float(raw.get("product_mm", 0.0)),
            reduction=float(raw.get("reduction", 0.0)),
            revision=int(raw.get("revision", 0)),
            drafted_at=str(raw.get("drafted_at", "")),
            committed_sequence=int(raw.get("committed_sequence", 0)),
        )

    def describe(self) -> str:
        if not self.committed():
            return "the crusher state is still a draft"
        return f"{self.state}, revision {self.revision} committed as record {self.committed_sequence}"

    def moment(self) -> datetime:
        return parse_stamp(self.drafted_at)


class JawStateStore:
    """Drafts the crusher state and proves it reached the append-only journal.

    A draft lands in the state directory, which is enough for the running
    process but not enough for a restart.  ``persist`` writes the draft into the
    record stream and only then marks it committed, so a line that is asked to
    feed against a draft can say exactly what is missing.
    """

    def __init__(self, unit: str, store: DocumentStore, stream: RecordStream) -> None:
        self.unit = unit
        self._store = store
        self._stream = stream
        self.doc_id = f"jaw.{unit}.state"

    def _payload(self) -> dict[str, Any]:
        document = self._store.try_load(self.doc_id)
        return {} if document is None else document.payload

    def current(self) -> JawState:
        return JawState.from_dict(self._payload(), self.unit)

    def draft(
        self,
        *,
        state: str,
        gap_mm: float,
        feed_mm: float,
        product_mm: float,
        reduction: float,
        moment: datetime,
    ) -> JawState:
        """Write a new revision that no restart would replay yet."""

        if state not in (STOPPED, RUNNING):
            raise InvalidRequest("crusher state is unknown", state=state, unit=self.unit)
        previous = self.current()
        drafted = JawState(
            unit=self.unit,
            state=state,
            gap_mm=round(float(gap_mm), 3),
            feed_mm=round(float(feed_mm), 3),
            product_mm=round(float(product_mm), 3),
            reduction=round(float(reduction), 3),
            revision=previous.revision + 1,
            drafted_at=stamp(moment),
            committed_sequence=0,
        )
        self._store.save(self.doc_id, drafted.as_dict(), moment)
        return drafted

    def persist(self, moment: datetime, actor: str) -> JawState:
        """Publish the draft onto the journal and record its sequence."""

        drafted = self.current()
        if drafted.revision <= 0:
            raise InvalidRequest("the crusher state has never been written", unit=self.unit)
        record = self._stream.stage(
            JAW_STATE_KIND,
            moment,
            unit=self.unit,
            actor=actor,
            subject=f"{self.unit}.jaw",
            payload={
                "revision": drafted.revision,
                "state": drafted.state,
                "gap_mm": drafted.gap_mm,
                "feed_mm": drafted.feed_mm,
                "product_mm": drafted.product_mm,
                "reduction": drafted.reduction,
            },
        )
        self._stream.commit_through(record.sequence, moment, actor)
        committed = JawState(**{**drafted.as_dict(), "committed_sequence": record.sequence})
        self._store.save(self.doc_id, committed.as_dict(), moment)
        return committed

    def committed(self) -> bool:
        """Whether the current revision sits at or below the commit watermark."""

        state = self.current()
        if state.revision <= 0 or state.committed_sequence <= 0:
            return False
        return any(
            record.sequence == state.committed_sequence and record.kind == JAW_STATE_KIND
            for record in self._stream.visible()
        )

    def require_committed(self, action: str) -> JawState:
        """Refuse an action whose stale draft never reached the journal."""

        state = self.current()
        if self.committed():
            return state
        raise DurabilityError(
            f"{self.unit}.jaw-state",
            unit=self.unit,
            action=action,
            stage="jaw-state",
            revision=state.revision,
            committed_sequence=state.committed_sequence,
        )
