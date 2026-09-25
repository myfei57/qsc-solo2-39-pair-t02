"""The magnet alarm an operator has to acknowledge before the belt restarts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..clock import stamp
from ..errors import InvalidRequest, StateConflict
from ..safety.interlock import Check
from ..store.documents import DocumentStore


@dataclass(frozen=True)
class AlarmState:
    """Whether an alarm is standing and whether somebody owns it."""

    name: str
    active: bool
    reason: str
    raised_at: str
    raised_by: str
    acknowledged_at: str
    acknowledged_by: str
    acknowledgement_reason: str
    occurrences: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "active": self.active,
            "reason": self.reason,
            "raised_at": self.raised_at,
            "raised_by": self.raised_by,
            "acknowledged_at": self.acknowledged_at,
            "acknowledged_by": self.acknowledged_by,
            "acknowledgement_reason": self.acknowledgement_reason,
            "occurrences": self.occurrences,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any], name: str) -> "AlarmState":
        return cls(
            name=str(raw.get("name", name)),
            active=bool(raw.get("active", False)),
            reason=str(raw.get("reason", "")),
            raised_at=str(raw.get("raised_at", "")),
            raised_by=str(raw.get("raised_by", "")),
            acknowledged_at=str(raw.get("acknowledged_at", "")),
            acknowledged_by=str(raw.get("acknowledged_by", "")),
            acknowledgement_reason=str(raw.get("acknowledgement_reason", "")),
            occurrences=int(raw.get("occurrences", 0)),
        )

    def describe(self) -> str:
        if not self.active:
            return "no alarm standing"
        return f"alarm since {self.raised_at}: {self.reason}"


class MagnetAlarm:
    """Raises and acknowledges the magnet alarm; only an operator can clear it."""

    def __init__(self, name: str, store: DocumentStore) -> None:
        self.name = name
        self._store = store
        self.doc_id = f"safety.{name}.alarm"

    def state(self) -> AlarmState:
        document = self._store.try_load(self.doc_id)
        if document is None:
            return AlarmState(
                name=self.name,
                active=False,
                reason="",
                raised_at="",
                raised_by="",
                acknowledged_at="",
                acknowledged_by="",
                acknowledgement_reason="",
                occurrences=0,
            )
        return AlarmState.from_dict(document.payload, self.name)

    def active(self) -> bool:
        return self.state().active

    def check(self) -> Check:
        state = self.state()
        return Check(name=self.name, ok=not state.active, detail=state.describe())

    def raise_alarm(self, reason: str, moment: datetime, actor: str) -> AlarmState:
        if not reason.strip():
            raise InvalidRequest("an alarm needs a reason", alarm=self.name)
        previous = self.state()
        state = AlarmState(
            name=self.name,
            active=True,
            reason=reason.strip(),
            raised_at=stamp(moment),
            raised_by=actor.strip(),
            acknowledged_at="",
            acknowledged_by="",
            acknowledgement_reason="",
            occurrences=previous.occurrences + 1,
        )
        self._store.save(self.doc_id, state.as_dict(), moment)
        return state

    def acknowledge(self, moment: datetime, actor: str, reason: str) -> AlarmState:
        """Take ownership of a standing alarm; an unowned alarm stays active."""

        state = self.state()
        if not state.active:
            raise StateConflict("there is no alarm to acknowledge", alarm=self.name)
        if not reason.strip():
            raise InvalidRequest("acknowledging an alarm needs a reason", alarm=self.name)
        acknowledged = AlarmState(
            **{
                **state.as_dict(),
                "active": False,
                "acknowledged_at": stamp(moment),
                "acknowledged_by": actor.strip(),
                "acknowledgement_reason": reason.strip(),
            }
        )
        self._store.save(self.doc_id, acknowledged.as_dict(), moment)
        return acknowledged
