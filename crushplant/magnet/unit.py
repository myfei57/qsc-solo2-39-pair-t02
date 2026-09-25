"""The overbelt magnet: readiness, trips and the reset that restores it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..audit.ledger import OUTCOME_OK, AuditLedger
from ..clock import stamp
from ..errors import StateConflict
from ..safety.interlock import Check
from ..store.documents import DocumentStore
from .alarm import MagnetAlarm


@dataclass(frozen=True)
class MagnetState:
    """Whether the magnet is live and willing to let the belt run."""

    unit: str
    energised: bool
    ready: bool
    current_a: float
    trips: int
    last_trip_reason: str
    started_at: str
    stopped_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit,
            "energised": self.energised,
            "ready": self.ready,
            "current_a": self.current_a,
            "trips": self.trips,
            "last_trip_reason": self.last_trip_reason,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any], unit: str) -> "MagnetState":
        return cls(
            unit=str(raw.get("unit", unit)),
            energised=bool(raw.get("energised", False)),
            ready=bool(raw.get("ready", False)),
            current_a=float(raw.get("current_a", 0.0)),
            trips=int(raw.get("trips", 0)),
            last_trip_reason=str(raw.get("last_trip_reason", "")),
            started_at=str(raw.get("started_at", "")),
            stopped_at=str(raw.get("stopped_at", "")),
        )

    def describe(self) -> str:
        if self.ready:
            return f"ready at {self.current_a} A"
        if self.energised:
            return "energised but not ready"
        return "not energised"


class MagnetSeparator:
    """Declares itself ready for the belt and drops out when it trips."""

    def __init__(
        self,
        unit: str,
        store: DocumentStore,
        ledger: AuditLedger,
        alarm: MagnetAlarm,
        *,
        hold_amps: float,
    ) -> None:
        self.unit = unit
        self._store = store
        self._ledger = ledger
        self._alarm = alarm
        self._hold_amps = float(hold_amps)
        self.doc_id = f"magnet.{unit}.unit"

    def _payload(self) -> dict[str, Any]:
        document = self._store.try_load(self.doc_id)
        return {} if document is None else document.payload

    def state(self) -> MagnetState:
        return MagnetState.from_dict(self._payload(), self.unit)

    def ready(self) -> bool:
        return self.state().ready

    def check(self) -> Check:
        """The precondition the belt start step depends on."""

        state = self.state()
        return Check(
            name=f"{self.unit}.magnet",
            ok=state.ready and not self._alarm.active(),
            detail=state.describe() if not self._alarm.active() else self._alarm.state().describe(),
        )

    def ready_up(self, moment: datetime, actor: str) -> MagnetState:
        """Energise the magnet; a standing alarm keeps it unavailable."""

        if self._alarm.active():
            raise StateConflict(
                "the magnet alarm has to be acknowledged first",
                unit=self.unit,
                alarm=self._alarm.state().reason,
            )
        state = self.state()
        updated = MagnetState(
            **{
                **state.as_dict(),
                "energised": True,
                "ready": True,
                "current_a": self._hold_amps,
                "started_at": stamp(moment),
            }
        )
        self._write(updated, moment)
        self._ledger.record(
            self.unit,
            "magnet.ready",
            OUTCOME_OK,
            actor,
            moment,
            subject=f"{self.unit}.magnet",
            current_a=updated.current_a,
        )
        return updated

    def trip(self, reason: str, moment: datetime, actor: str) -> MagnetState:
        """Lose readiness and raise the alarm in one step."""

        state = self.state()
        updated = MagnetState(
            **{
                **state.as_dict(),
                "energised": False,
                "ready": False,
                "current_a": 0.0,
                "trips": state.trips + 1,
                "last_trip_reason": reason.strip(),
                "stopped_at": stamp(moment),
            }
        )
        self._write(updated, moment)
        self._alarm.raise_alarm(reason, moment, actor)
        self._ledger.record(
            self.unit,
            "magnet.trip",
            OUTCOME_OK,
            actor,
            moment,
            subject=f"{self.unit}.magnet",
            reason=reason.strip(),
            trips=updated.trips,
        )
        return updated

    def reset(self, moment: datetime, actor: str, reason: str) -> MagnetState:
        """Acknowledge the alarm and bring the magnet back up."""

        self._alarm.acknowledge(moment, actor, reason)
        state = self.state()
        updated = MagnetState(
            **{
                **state.as_dict(),
                "energised": True,
                "ready": True,
                "current_a": self._hold_amps,
                "started_at": stamp(moment),
                "last_trip_reason": "",
            }
        )
        self._write(updated, moment)
        self._ledger.record(
            self.unit,
            "magnet.reset",
            OUTCOME_OK,
            actor,
            moment,
            subject=f"{self.unit}.magnet",
            reason=reason.strip(),
        )
        return updated

    def _write(self, state: MagnetState, moment: datetime) -> None:
        self._store.save(self.doc_id, state.as_dict(), moment)
