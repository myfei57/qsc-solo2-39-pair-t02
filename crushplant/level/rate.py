"""Turning a bin level into a feed rate the feeder can hold."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..audit.ledger import OUTCOME_OK, AuditLedger
from ..config import LineSpec, StockSpec
from ..errors import InvalidRequest, StaleRecord
from ..store.documents import DocumentStore
from ..units import clamp, round_to
from ..verdict.log import VerdictLog
from .gauge import LevelGauge


class FeedRatePlanner:
    """Chases the bin level with the feeder setpoint and never leaves the band.

    A reading that has gone stale is refused rather than extrapolated: an old
    level is exactly the input that would let the feeder run the bin dry.
    """

    def __init__(
        self,
        unit: str,
        spec: LineSpec,
        stock: StockSpec,
        store: DocumentStore,
        ledger: AuditLedger,
        gauge: LevelGauge,
        verdicts: VerdictLog,
    ) -> None:
        if stock.feed_gain_tph_per_pct <= 0:
            raise InvalidRequest("the level gain must be positive", unit=unit)
        self.unit = unit
        self._spec = spec
        self._stock = stock
        self._store = store
        self._ledger = ledger
        self._gauge = gauge
        self._verdicts = verdicts
        self.doc_id = f"level.{unit}.plan"

    @property
    def subject(self) -> str:
        return f"{self.unit}.feed-plan"

    def recommend(self, level_pct: float) -> float:
        """The setpoint the level asks for, clamped to the feeder band."""

        target = self._stock.target_level_pct()
        wanted = self._spec.feeder_rated_tph + self._stock.feed_gain_tph_per_pct * (level_pct - target)
        low, high = self._spec.feeder_window()
        return round_to(clamp(wanted, low, high), 3)

    def plan(self, moment: datetime, actor: str) -> dict[str, Any]:
        """Read the level, work out a setpoint and write the decision down."""

        reading = self._gauge.require_fresh(moment, "feed plan")
        setpoint = self.recommend(reading.level_pct)
        target = self._stock.target_level_pct()
        payload = {
            "unit": self.unit,
            "level_pct": reading.level_pct,
            "target_level_pct": target,
            "setpoint_tph": setpoint,
            "deviation_pct": round(reading.level_pct - target, 3),
            "at": reading.at,
        }
        self._store.save(self.doc_id, payload, moment)
        self._verdicts.record(
            self.unit,
            self.subject,
            "level.plan",
            "ok",
            setpoint,
            moment,
            actor,
            level_pct=reading.level_pct,
            deviation_pct=payload["deviation_pct"],
        )
        self._ledger.record(
            self.unit,
            "level.plan",
            OUTCOME_OK,
            actor,
            moment,
            subject=self.subject,
            setpoint_tph=setpoint,
            level_pct=reading.level_pct,
        )
        return payload

    def state(self) -> dict[str, Any]:
        document = self._store.try_load(self.doc_id)
        return {} if document is None else dict(document.payload)

    def last_setpoint(self) -> float:
        return float(self.state().get("setpoint_tph", 0.0))

    def check(self, moment: datetime) -> dict[str, Any]:
        """A read-only view: what the plan would be, or why it cannot be made."""

        try:
            reading = self._gauge.require_fresh(moment, "feed plan")
        except StaleRecord as error:
            return {"ok": False, "reason": error.reason}
        return {"ok": True, "level": reading.as_dict(), "setpoint_tph": self.recommend(reading.level_pct)}
