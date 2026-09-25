"""Protective latch with an explicit set condition and release condition."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..clock import parse_stamp, stamp
from ..errors import InvalidRequest, LatchActive
from ..store.documents import DocumentStore

LATCH_SET = "set"
LATCH_CLEARED = "cleared"
EPOCH = "1970-01-01T00:00:00Z"


@dataclass(frozen=True)
class LatchState:
    """Whether a protective latch currently blocks its target."""

    name: str
    latched: bool
    reason: str
    set_at: str = ""
    set_by: str = ""
    hold_until: str = ""
    released_at: str = ""
    released_by: str = ""
    released_reason: str = ""
    trips: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "latched": self.latched,
            "reason": self.reason,
            "set_at": self.set_at,
            "set_by": self.set_by,
            "hold_until": self.hold_until,
            "released_at": self.released_at,
            "released_by": self.released_by,
            "released_reason": self.released_reason,
            "trips": self.trips,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LatchState":
        return cls(
            name=str(raw.get("name", "")),
            latched=bool(raw.get("latched", False)),
            reason=str(raw.get("reason", "")),
            set_at=str(raw.get("set_at", "")),
            set_by=str(raw.get("set_by", "")),
            hold_until=str(raw.get("hold_until", "")),
            released_at=str(raw.get("released_at", "")),
            released_by=str(raw.get("released_by", "")),
            released_reason=str(raw.get("released_reason", "")),
            trips=int(raw.get("trips", 0)),
        )

    def hold_deadline(self) -> datetime:
        return parse_stamp(self.hold_until) if self.hold_until else parse_stamp(EPOCH)

    def describe(self) -> str:
        if not self.latched:
            return "clear"
        return f"latched since {self.set_at}: {self.reason}"


class Latch:
    """A latch that is set by a trip condition and released by its recovery."""

    def __init__(
        self,
        name: str,
        store: DocumentStore,
        *,
        hold_seconds: float,
        doc_id: str | None = None,
    ) -> None:
        self.name = name
        self._store = store
        self._hold_seconds = float(hold_seconds)
        self.doc_id = doc_id or f"safety.{name}.latch"

    def state(self) -> LatchState:
        document = self._store.try_load(self.doc_id)
        if document is None:
            return LatchState(name=self.name, latched=False, reason="")
        return LatchState.from_dict(document.payload)

    def is_set(self) -> bool:
        return self.state().latched

    def trip(self, reason: str, moment: datetime, actor: str) -> LatchState:
        """Set the latch; the reason is the condition that tripped it."""

        if not reason.strip():
            raise InvalidRequest("a latch needs a trip reason", latch=self.name)
        previous = self.state()
        state = LatchState(
            name=self.name,
            latched=True,
            reason=reason.strip(),
            set_at=stamp(moment),
            set_by=actor.strip(),
            hold_until=stamp(moment + timedelta(seconds=self._hold_seconds)),
            trips=previous.trips + 1,
        )
        self._save(state, moment)
        return state

    def evaluate(
        self,
        moment: datetime,
        *,
        condition_clear: bool,
        actor: str = "control",
    ) -> LatchState:
        """Release the latch once the trip condition has recovered."""

        state = self.state()
        if not state.latched:
            return state
        if not condition_clear:
            return state
        if moment < state.hold_deadline():
            return state
        return self.release(moment, actor, "trip condition recovered")

    def release(self, moment: datetime, actor: str, reason: str, *, force: bool = False) -> LatchState:
        """Clear the latch, refusing a request that arrives while the hold runs.

        The hold is part of the protection.  An operator may override it, but
        only by setting ``force`` and stating a reason, and the override is kept
        with the latch state so the next shift can see it.
        """

        state = self.state()
        if not state.latched:
            raise LatchActive(self.name, "the latch is not set", requested="release")
        if not reason.strip():
            raise InvalidRequest("releasing a latch needs a reason", latch=self.name)
        if not force and moment < state.hold_deadline():
            raise LatchActive(
                self.name,
                f"the hold runs until {state.hold_until}",
                requested="release",
            )
        cleared = LatchState(
            name=self.name,
            latched=False,
            reason=state.reason,
            set_at=state.set_at,
            set_by=state.set_by,
            hold_until=state.hold_until,
            released_at=stamp(moment),
            released_by=actor.strip(),
            released_reason=reason.strip(),
            trips=state.trips,
        )
        self._save(cleared, moment)
        return cleared

    def require_clear(self, action: str) -> LatchState:
        """Refuse an action while the latch is set."""

        state = self.state()
        if state.latched:
            raise LatchActive(self.name, state.reason or "protective latch", action=action)
        return state

    def check(self) -> "LatchCheck":
        state = self.state()
        return LatchCheck(name=self.name, ok=not state.latched, detail=state.describe())

    def _save(self, state: LatchState, moment: datetime) -> None:
        self._store.save(self.doc_id, state.as_dict(), moment)


@dataclass(frozen=True)
class LatchCheck:
    """Adapter that lets a latch take part in a precondition list."""

    name: str
    ok: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}
