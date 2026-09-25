"""The vibrating screen the feed step has to wait for."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..audit.ledger import OUTCOME_OK, AuditLedger
from ..clock import stamp
from ..config import LineSpec, QualitySpec
from ..errors import InvalidRequest, StateConflict
from ..store.documents import DocumentStore
from ..units import passing_fraction
from ..verdict.log import VerdictLog
from ..verdict.threshold import Limit, judge
from .deck import DeckMonitor


@dataclass(frozen=True)
class ScreenState:
    """What the screen is doing right now."""

    unit: str
    deck: str
    running: bool
    amplitude_mm: float
    throughput_tph: float
    started_at: str
    stopped_at: str
    cycles: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit,
            "deck": self.deck,
            "running": self.running,
            "amplitude_mm": self.amplitude_mm,
            "throughput_tph": self.throughput_tph,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "cycles": self.cycles,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any], unit: str, deck: str) -> "ScreenState":
        return cls(
            unit=str(raw.get("unit", unit)),
            deck=str(raw.get("deck", deck)),
            running=bool(raw.get("running", False)),
            amplitude_mm=float(raw.get("amplitude_mm", 0.0)),
            throughput_tph=float(raw.get("throughput_tph", 0.0)),
            started_at=str(raw.get("started_at", "")),
            stopped_at=str(raw.get("stopped_at", "")),
            cycles=int(raw.get("cycles", 0)),
        )


class VibratingScreen:
    """Runs the deck and judges the product that comes off it."""

    def __init__(
        self,
        unit: str,
        spec: LineSpec,
        quality: QualitySpec,
        store: DocumentStore,
        ledger: AuditLedger,
        decks: DeckMonitor,
        verdicts: VerdictLog,
    ) -> None:
        self.unit = unit
        self._spec = spec
        self._quality = quality
        self._store = store
        self._ledger = ledger
        self._decks = decks
        self._verdicts = verdicts
        self.deck = spec.screen_deck_id
        self.doc_id = f"screen.{unit}.drive"
        self.undersize_limit = Limit(
            "screen.undersize",
            quality.undersize_min_pct,
            100.0,
        )
        self.oversize_limit = Limit(
            "screen.oversize",
            0.0,
            quality.oversize_max_pct,
        )

    def _payload(self) -> dict[str, Any]:
        document = self._store.try_load(self.doc_id)
        return {} if document is None else document.payload

    def state(self) -> ScreenState:
        return ScreenState.from_dict(self._payload(), self.unit, self.deck)

    def running(self) -> bool:
        return self.state().running

    def start(self, moment: datetime, actor: str) -> ScreenState:
        state = self.state()
        if state.running:
            raise StateConflict("the screen is already running", unit=self.unit, since=state.started_at)
        updated = ScreenState(
            unit=self.unit,
            deck=self.deck,
            running=True,
            amplitude_mm=state.amplitude_mm or round(self._spec.screen_aperture_mm / 2.0, 3),
            throughput_tph=0.0,
            started_at=stamp(moment),
            stopped_at=state.stopped_at,
            cycles=state.cycles + 1,
        )
        self._write(updated, moment)
        self._ledger.record(
            self.unit,
            "screen.start",
            OUTCOME_OK,
            actor,
            moment,
            subject=f"{self.unit}.screen",
            deck=self.deck,
            cycle=updated.cycles,
        )
        return updated

    def stop(self, moment: datetime, actor: str, reason: str = "operator stop") -> ScreenState:
        state = self.state()
        if not state.running:
            raise StateConflict("the screen is not running", unit=self.unit)
        updated = ScreenState(
            **{
                **state.as_dict(),
                "running": False,
                "throughput_tph": 0.0,
                "stopped_at": stamp(moment),
            }
        )
        self._write(updated, moment)
        self._ledger.record(
            self.unit,
            "screen.stop",
            OUTCOME_OK,
            actor,
            moment,
            subject=f"{self.unit}.screen",
            reason=reason.strip(),
        )
        return updated

    def throughput(self, tonnes_per_hour: float, moment: datetime, actor: str) -> dict[str, Any]:
        """Push one throughput reading through the deck monitor."""

        if not self.running():
            raise StateConflict("the screen is not running", unit=self.unit, deck=self.deck)
        result = self._decks.duty(tonnes_per_hour, moment, actor)
        state = self.state()
        updated = ScreenState(**{**state.as_dict(), "throughput_tph": result["load"]["tonnes_per_hour"]})
        self._write(updated, moment)
        return result

    def grade(self, undersize_t: float, total_t: float, moment: datetime, actor: str) -> dict[str, Any]:
        """Judge a product sample against the size limits for this generation."""

        passing = passing_fraction(undersize_t, total_t)
        if passing > 100.0:
            raise InvalidRequest("a sample cannot pass more mass than it holds", passing_pct=passing)
        undersize = judge(f"{self.unit}.product", passing, self.undersize_limit, moment)
        oversize = judge(f"{self.unit}.product", round(100.0 - passing, 3), self.oversize_limit, moment)
        self._verdicts.record_threshold(
            self.unit,
            undersize,
            moment,
            actor,
            sample_t=round(float(total_t), 4),
        )
        self._verdicts.record_threshold(
            self.unit,
            oversize,
            moment,
            actor,
            sample_t=round(float(total_t), 4),
        )
        return {"undersize": undersize.as_dict(), "oversize": oversize.as_dict()}

    def _write(self, state: ScreenState, moment: datetime) -> None:
        self._store.save(self.doc_id, state.as_dict(), moment)
