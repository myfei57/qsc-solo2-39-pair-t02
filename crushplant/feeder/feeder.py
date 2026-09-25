"""The apron feeder: its setpoint band and the tonnage it has moved."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..audit.ledger import OUTCOME_OK, AuditLedger
from ..clock import parse_stamp, stamp
from ..config import LineSpec
from ..errors import InvalidRequest, LimitExceeded, StateConflict
from ..store.documents import DocumentStore
from ..units import rate_per_hour, round_to
from ..verdict.log import VerdictLog
from ..verdict.threshold import Limit, judge


@dataclass(frozen=True)
class FeederState:
    """What the feeder drive is doing right now."""

    unit: str
    running: bool
    setpoint_tph: float
    measured_tph: float
    started_at: str
    stopped_at: str
    stopped_reason: str
    cycles: int
    tonnage: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit,
            "running": self.running,
            "setpoint_tph": self.setpoint_tph,
            "measured_tph": self.measured_tph,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "stopped_reason": self.stopped_reason,
            "cycles": self.cycles,
            "tonnage": self.tonnage,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any], unit: str) -> "FeederState":
        return cls(
            unit=str(raw.get("unit", unit)),
            running=bool(raw.get("running", False)),
            setpoint_tph=float(raw.get("setpoint_tph", 0.0)),
            measured_tph=float(raw.get("measured_tph", 0.0)),
            started_at=str(raw.get("started_at", "")),
            stopped_at=str(raw.get("stopped_at", "")),
            stopped_reason=str(raw.get("stopped_reason", "")),
            cycles=int(raw.get("cycles", 0)),
            tonnage=float(raw.get("tonnage", 0.0)),
        )


class Feeder:
    """Runs the feeder within its configured band and accumulates tonnage."""

    def __init__(
        self,
        unit: str,
        spec: LineSpec,
        store: DocumentStore,
        ledger: AuditLedger,
        verdicts: VerdictLog,
    ) -> None:
        self.unit = unit
        self._spec = spec
        self._store = store
        self._ledger = ledger
        self._verdicts = verdicts
        self.doc_id = f"feeder.{unit}.drive"
        self.limit = Limit("feeder.setpoint", spec.feeder_min_tph, spec.feeder_max_tph)

    def _payload(self) -> dict[str, Any]:
        document = self._store.try_load(self.doc_id)
        return {} if document is None else document.payload

    def state(self) -> FeederState:
        return FeederState.from_dict(self._payload(), self.unit)

    def running(self) -> bool:
        return self.state().running

    def total_tonnes(self) -> float:
        return self.state().tonnage

    def average_rate(self, moment: datetime) -> float:
        """The tonnage moved so far, spread over the time the drive has run."""

        state = self.state()
        if not state.started_at:
            return 0.0
        started = parse_stamp(state.started_at)
        elapsed = max(0.0, (moment - started).total_seconds())
        if elapsed <= 0:
            return 0.0
        return rate_per_hour(state.tonnage, elapsed)

    def validate_setpoint(self, target_tph: float) -> float:
        """Reject a setpoint outside the feeder band before anything moves."""

        if not self.limit.contains(target_tph):
            raise LimitExceeded(
                "feeder setpoint is outside the configured band",
                unit=self.unit,
                requested_tph=target_tph,
                low=self.limit.low,
                high=self.limit.high,
            )
        return round_to(float(target_tph), 4)

    def start(self, target_tph: float, moment: datetime, actor: str) -> FeederState:
        state = self.state()
        if state.running:
            raise StateConflict("the feeder is already running", unit=self.unit, since=state.started_at)
        setpoint = self.validate_setpoint(target_tph)
        updated = FeederState(
            unit=self.unit,
            running=True,
            setpoint_tph=setpoint,
            measured_tph=0.0,
            started_at=stamp(moment),
            stopped_at=state.stopped_at,
            stopped_reason="",
            cycles=state.cycles + 1,
            tonnage=state.tonnage,
        )
        self._write(updated, moment)
        self._ledger.record(
            self.unit,
            "feeder.start",
            OUTCOME_OK,
            actor,
            moment,
            subject=self.unit,
            setpoint_tph=setpoint,
            cycle=updated.cycles,
        )
        return updated

    def set_speed(self, target_tph: float, moment: datetime, actor: str) -> FeederState:
        state = self.state()
        if not state.running:
            raise StateConflict("the feeder is not running", unit=self.unit)
        setpoint = self.validate_setpoint(target_tph)
        updated = FeederState(**{**state.as_dict(), "setpoint_tph": setpoint})
        self._write(updated, moment)
        self._ledger.record(
            self.unit,
            "feeder.setpoint",
            OUTCOME_OK,
            actor,
            moment,
            subject=self.unit,
            setpoint_tph=setpoint,
        )
        return updated

    def measure(self, measured_tph: float, moment: datetime, actor: str) -> dict[str, Any]:
        """Record a measured rate and judge it against the feeder band."""

        if measured_tph < 0:
            raise InvalidRequest("a measured rate must not be negative", unit=self.unit)
        state = self.state()
        verdict = judge(f"{self.unit}.feeder", float(measured_tph), self.limit, moment)
        updated = FeederState(**{**state.as_dict(), "measured_tph": round_to(float(measured_tph), 4)})
        self._write(updated, moment)
        self._verdicts.record_threshold(self.unit, verdict, moment, actor, setpoint_tph=state.setpoint_tph)
        return {"state": updated.as_dict(), "verdict": verdict.as_dict()}

    def carry(self, seconds: float, moment: datetime) -> FeederState:
        """Add the tonnage the drive moved over one control cycle."""

        if seconds < 0:
            raise InvalidRequest("a control cycle must not be negative")
        state = self.state()
        if not state.running:
            return state
        moved = state.measured_tph * seconds / 3600.0
        updated = FeederState(**{**state.as_dict(), "tonnage": round_to(state.tonnage + moved, 4)})
        self._write(updated, moment)
        return updated

    def stop(self, moment: datetime, actor: str, reason: str = "operator stop") -> FeederState:
        state = self.state()
        if not state.running:
            raise StateConflict("the feeder is not running", unit=self.unit)
        updated = FeederState(
            **{
                **state.as_dict(),
                "running": False,
                "setpoint_tph": 0.0,
                "measured_tph": 0.0,
                "stopped_at": stamp(moment),
                "stopped_reason": reason.strip(),
            }
        )
        self._write(updated, moment)
        self._ledger.record(
            self.unit,
            "feeder.stop",
            OUTCOME_OK,
            actor,
            moment,
            subject=self.unit,
            reason=reason.strip(),
            tonnage=updated.tonnage,
        )
        return updated

    def _write(self, state: FeederState, moment: datetime) -> None:
        self._store.save(self.doc_id, state.as_dict(), moment)
