"""The secondary crusher and the stall judgement that stops it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..audit.ledger import OUTCOME_OK, OUTCOME_TRIPPED, AuditLedger
from ..clock import stamp
from ..config import LineSpec
from ..errors import InvalidRequest, StateConflict
from ..store.documents import DocumentStore
from ..units import crusher_load_pct, margin
from ..verdict.log import VerdictLog
from ..verdict.threshold import Limit, judge
from .calibrate import CurrentCalibration


@dataclass(frozen=True)
class ConeState:
    """What the secondary crusher is doing right now."""

    unit: str
    running: bool
    gap_mm: float
    amps: float
    load_pct: float
    stalled: bool
    started_at: str
    stopped_at: str
    stopped_reason: str
    cycles: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit,
            "running": self.running,
            "gap_mm": self.gap_mm,
            "amps": self.amps,
            "load_pct": self.load_pct,
            "stalled": self.stalled,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "stopped_reason": self.stopped_reason,
            "cycles": self.cycles,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any], unit: str) -> "ConeState":
        return cls(
            unit=str(raw.get("unit", unit)),
            running=bool(raw.get("running", False)),
            gap_mm=float(raw.get("gap_mm", 0.0)),
            amps=float(raw.get("amps", 0.0)),
            load_pct=float(raw.get("load_pct", 0.0)),
            stalled=bool(raw.get("stalled", False)),
            started_at=str(raw.get("started_at", "")),
            stopped_at=str(raw.get("stopped_at", "")),
            stopped_reason=str(raw.get("stopped_reason", "")),
            cycles=int(raw.get("cycles", 0)),
        )


class ConeCrusher:
    """Judges its own draw against a fresh calibration and stops when it stalls."""

    def __init__(
        self,
        unit: str,
        spec: LineSpec,
        store: DocumentStore,
        ledger: AuditLedger,
        calibration: CurrentCalibration,
        verdicts: VerdictLog,
    ) -> None:
        self.unit = unit
        self._spec = spec
        self._store = store
        self._ledger = ledger
        self._calibration = calibration
        self._verdicts = verdicts
        self.doc_id = f"cone.{unit}.machine"
        self.current_limit = Limit("cone.current", spec.cone_min_amps, spec.cone_max_amps)
        self.stall_limit = Limit("cone.stall", 0.0, spec.stall_amps())

    def _payload(self) -> dict[str, Any]:
        document = self._store.try_load(self.doc_id)
        return {} if document is None else document.payload

    def state(self) -> ConeState:
        return ConeState.from_dict(self._payload(), self.unit)

    def running(self) -> bool:
        return self.state().running

    def start(self, moment: datetime, actor: str) -> ConeState:
        """Start the machine; a stale calibration is refused before it spins."""

        state = self.state()
        if state.running:
            raise StateConflict("the cone is already running", unit=self.unit, since=state.started_at)
        self._calibration.require(moment)
        updated = ConeState(
            unit=self.unit,
            running=True,
            gap_mm=state.gap_mm or round(self._spec.jaw_product_mm * 0.6, 3),
            amps=0.0,
            load_pct=0.0,
            stalled=False,
            started_at=stamp(moment),
            stopped_at=state.stopped_at,
            stopped_reason="",
            cycles=state.cycles + 1,
        )
        self._write(updated, moment)
        self._ledger.record(
            self.unit,
            "cone.start",
            OUTCOME_OK,
            actor,
            moment,
            subject=f"{self.unit}.cone",
            cycle=updated.cycles,
        )
        return updated

    def stop(self, moment: datetime, actor: str, reason: str = "operator stop") -> ConeState:
        state = self.state()
        if not state.running:
            raise StateConflict("the cone is not running", unit=self.unit)
        updated = ConeState(
            **{
                **state.as_dict(),
                "running": False,
                "amps": 0.0,
                "load_pct": 0.0,
                "stalled": False,
                "stopped_at": stamp(moment),
                "stopped_reason": reason.strip(),
            }
        )
        self._write(updated, moment)
        self._ledger.record(
            self.unit,
            "cone.stop",
            OUTCOME_OK,
            actor,
            moment,
            subject=f"{self.unit}.cone",
            reason=reason.strip(),
        )
        return updated

    def sample_amps(self, amps: float, moment: datetime, actor: str) -> dict[str, Any]:
        """Judge one draw reading; a stall stops the machine on the spot."""

        if amps < 0:
            raise InvalidRequest("a current reading must not be negative", unit=self.unit)
        state = self.state()
        if not state.running:
            raise StateConflict("the cone is not running", unit=self.unit)
        self._calibration.require(moment)
        current = judge(f"{self.unit}.cone", float(amps), self.current_limit, moment)
        stall = judge(f"{self.unit}.cone", float(amps), self.stall_limit, moment)
        stalled = not stall.ok()
        load_pct = crusher_load_pct(amps, self._spec.cone_rated_amps)
        updated = ConeState(
            **{
                **state.as_dict(),
                "amps": round(float(amps), 3),
                "load_pct": load_pct,
                "stalled": stalled,
                "running": not stalled,
                "stopped_at": stamp(moment) if stalled else state.stopped_at,
                "stopped_reason": "stall" if stalled else state.stopped_reason,
            }
        )
        self._write(updated, moment)
        self._verdicts.record_threshold(self.unit, current, moment, actor, load_pct=load_pct)
        self._verdicts.record_threshold(
            self.unit,
            stall,
            moment,
            actor,
            load_pct=load_pct,
            stall_amps=self._spec.stall_amps(),
            headroom_amps=margin(amps, self._spec.stall_amps()),
        )
        if stalled:
            self._ledger.record(
                self.unit,
                "cone.stall",
                OUTCOME_TRIPPED,
                actor,
                moment,
                subject=f"{self.unit}.cone",
                amps=round(float(amps), 3),
                stall_amps=self._spec.stall_amps(),
            )
        return {"state": updated.as_dict(), "current": current.as_dict(), "stall": stall.as_dict()}

    def expected_amps(self, tonnes_per_hour: float, moment: datetime) -> float:
        """Ask the calibration what the draw should have been."""

        return self._calibration.expected_amps(tonnes_per_hour, moment)

    def _write(self, state: ConeState, moment: datetime) -> None:
        self._store.save(self.doc_id, state.as_dict(), moment)
