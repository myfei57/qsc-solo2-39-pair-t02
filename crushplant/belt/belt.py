"""The discharge belt, its start interlock and the tonnage it carries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..audit.ledger import OUTCOME_BLOCKED, OUTCOME_OK, AuditLedger
from ..clock import stamp
from ..config import LineSpec
from ..errors import InvalidRequest, StateConflict
from ..safety.interlock import Check, guard
from ..store.documents import DocumentStore
from ..units import belt_load_kg_per_m, round_to
from ..verdict.log import VerdictLog
from .scale import ScaleReading


@dataclass(frozen=True)
class BeltState:
    """What the discharge belt is doing right now."""

    unit: str
    running: bool
    speed_mps: float
    load_kg_per_m: float
    tonnes_per_hour: float
    started_at: str
    stopped_at: str
    stopped_reason: str
    tonnes: float
    cycles: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit,
            "running": self.running,
            "speed_mps": self.speed_mps,
            "load_kg_per_m": self.load_kg_per_m,
            "tonnes_per_hour": self.tonnes_per_hour,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "stopped_reason": self.stopped_reason,
            "tonnes": self.tonnes,
            "cycles": self.cycles,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any], unit: str, speed_mps: float) -> "BeltState":
        return cls(
            unit=str(raw.get("unit", unit)),
            running=bool(raw.get("running", False)),
            speed_mps=float(raw.get("speed_mps", speed_mps)),
            load_kg_per_m=float(raw.get("load_kg_per_m", 0.0)),
            tonnes_per_hour=float(raw.get("tonnes_per_hour", 0.0)),
            started_at=str(raw.get("started_at", "")),
            stopped_at=str(raw.get("stopped_at", "")),
            stopped_reason=str(raw.get("stopped_reason", "")),
            tonnes=float(raw.get("tonnes", 0.0)),
            cycles=int(raw.get("cycles", 0)),
        )


class BeltConveyor:
    """Runs the belt, but only once the magnet has declared itself ready."""

    def __init__(
        self,
        unit: str,
        spec: LineSpec,
        store: DocumentStore,
        ledger: AuditLedger,
        magnet: Any,
        verdicts: VerdictLog,
    ) -> None:
        self.unit = unit
        self._spec = spec
        self._store = store
        self._ledger = ledger
        self._magnet = magnet
        self._verdicts = verdicts
        self.doc_id = f"belt.{unit}.drive"

    def _payload(self) -> dict[str, Any]:
        document = self._store.try_load(self.doc_id)
        return {} if document is None else document.payload

    def state(self) -> BeltState:
        return BeltState.from_dict(self._payload(), self.unit, self._spec.belt_speed_mps)

    def running(self) -> bool:
        return self.state().running

    def gates(self) -> list[Check]:
        """The belt may only start once the magnet is ready."""

        return [self._magnet.check()]

    def start(self, moment: datetime, actor: str) -> BeltState:
        state = self.state()
        if state.running:
            raise StateConflict("the belt is already running", unit=self.unit, since=state.started_at)
        checks = self.gates()
        failing = [check for check in checks if not check.ok]
        if failing:
            self._ledger.record(
                self.unit,
                "belt.start",
                OUTCOME_BLOCKED,
                actor,
                moment,
                subject=f"{self.unit}.belt",
                unmet=[check.detail for check in failing],
            )
            guard("belt start", checks)
        updated = BeltState(
            unit=self.unit,
            running=True,
            speed_mps=self._spec.belt_speed_mps,
            load_kg_per_m=state.load_kg_per_m,
            tonnes_per_hour=0.0,
            started_at=stamp(moment),
            stopped_at=state.stopped_at,
            stopped_reason="",
            tonnes=state.tonnes,
            cycles=state.cycles + 1,
        )
        self._write(updated, moment)
        self._ledger.record(
            self.unit,
            "belt.start",
            OUTCOME_OK,
            actor,
            moment,
            subject=f"{self.unit}.belt",
            speed_mps=updated.speed_mps,
            cycle=updated.cycles,
        )
        return updated

    def stop(self, moment: datetime, actor: str, reason: str = "operator stop") -> BeltState:
        state = self.state()
        if not state.running:
            raise StateConflict("the belt is not running", unit=self.unit)
        updated = BeltState(
            **{
                **state.as_dict(),
                "running": False,
                "tonnes_per_hour": 0.0,
                "stopped_at": stamp(moment),
                "stopped_reason": reason.strip(),
            }
        )
        self._write(updated, moment)
        self._ledger.record(
            self.unit,
            "belt.stop",
            OUTCOME_OK,
            actor,
            moment,
            subject=f"{self.unit}.belt",
            reason=reason.strip(),
            tonnes=updated.tonnes,
        )
        return updated

    def carry(self, reading: ScaleReading, seconds: float, moment: datetime, actor: str) -> BeltState:
        """Add the tonnage one weighing cycle put on the stockpile."""

        if seconds < 0:
            raise InvalidRequest("a control cycle must not be negative")
        state = self.state()
        if not state.running:
            return state
        moved = reading.tonnes_per_hour * seconds / 3600.0
        updated = BeltState(
            **{
                **state.as_dict(),
                "load_kg_per_m": reading.kg_per_m,
                "tonnes_per_hour": reading.tonnes_per_hour,
                "tonnes": round_to(state.tonnes + moved, 4),
            }
        )
        self._write(updated, moment)
        self._verdicts.record(
            self.unit,
            f"{self.unit}.belt",
            "belt.tonnage",
            "ok",
            reading.tonnes_per_hour,
            moment,
            actor,
            kg_per_m=reading.kg_per_m,
            generation=reading.generation,
        )
        return updated

    def target_load(self, tonnes_per_hour: float) -> float:
        """The belt load that would carry the given throughput."""

        return belt_load_kg_per_m(float(tonnes_per_hour), self._spec.belt_speed_mps)

    def _write(self, state: BeltState, moment: datetime) -> None:
        self._store.save(self.doc_id, state.as_dict(), moment)
