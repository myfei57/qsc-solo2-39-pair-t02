"""Blockage detection: a level has to stay high before it counts.

The detector keeps two thresholds.  A blockage is confirmed once the level has
satched the block threshold for the whole window, and the chute only counts as
clear once the level has fallen back past the lower threshold, which is the
hysteresis that stops a sloshing chute from flapping the latch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..errors import InvalidRequest
from ..store.documents import DocumentStore
from ..units import margin
from ..verdict.window import ABOVE, WINDOW_HELD, WindowJudge


@dataclass(frozen=True)
class BlockageVerdict:
    """What the chute level says about the material flow."""

    subject: str
    blocked: bool
    state: str
    level_pct: float
    block_pct: float
    clear_pct: float
    samples: int
    span_seconds: float
    margin_pct: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "blocked": self.blocked,
            "state": self.state,
            "level_pct": self.level_pct,
            "block_pct": self.block_pct,
            "clear_pct": self.clear_pct,
            "samples": self.samples,
            "span_seconds": self.span_seconds,
            "margin_pct": self.margin_pct,
        }


class BlockageDetector:
    """Sustained level plus hysteresis, over the window the line is configured with."""

    def __init__(
        self,
        unit: str,
        store: DocumentStore,
        *,
        block_pct: float,
        clear_pct: float,
        hold_seconds: float,
        min_samples: int,
    ) -> None:
        if clear_pct >= block_pct:
            raise InvalidRequest("chute thresholds are inverted", unit=unit)
        self.unit = unit
        self.subject = f"{unit}.chute"
        self.block_pct = float(block_pct)
        self.clear_pct = float(clear_pct)
        self._window = WindowJudge(
            self.subject,
            store,
            threshold=self.block_pct,
            hold_seconds=hold_seconds,
            min_samples=min_samples,
            direction=ABOVE,
        )

    def observe(self, level_pct: float, moment: datetime) -> BlockageVerdict:
        """Add one level reading and report whether the chute is blocked."""

        level = float(level_pct)
        if level < 0 or level > 100:
            raise InvalidRequest("a chute level sits between 0 and 100 percent", level=level)
        if level <= self.clear_pct:
            self._window.clear(moment, level)
            return self._verdict(level, blocked=False, state="clear", samples=0, span=0.0)
        window = self._window.observe(level, moment)
        blocked = window.state == WINDOW_HELD and level > self.clear_pct
        return self._verdict(
            level,
            blocked=blocked,
            state="blocked" if blocked else "filling",
            samples=window.samples,
            span=window.span_seconds,
        )

    def state(self, moment: datetime) -> BlockageVerdict:
        """The detector state without adding a reading."""

        window = self._window.state(moment)
        blocked = window.state == WINDOW_HELD
        return self._verdict(
            window.latest,
            blocked=blocked,
            state="blocked" if blocked else ("filling" if window.samples else "clear"),
            samples=window.samples,
            span=window.span_seconds,
        )

    def clear(self, moment: datetime, level_pct: float = 0.0) -> BlockageVerdict:
        """Reset the window after an operator has cleared the chute."""

        self._window.clear(moment, level_pct)
        return self._verdict(float(level_pct), blocked=False, state="clear", samples=0, span=0.0)

    def is_clear(self, level_pct: float) -> bool:
        return float(level_pct) <= self.clear_pct

    def _verdict(
        self,
        level: float,
        *,
        blocked: bool,
        state: str,
        samples: int,
        span: float,
    ) -> BlockageVerdict:
        return BlockageVerdict(
            subject=self.subject,
            blocked=blocked,
            state=state,
            level_pct=round(level, 3),
            block_pct=self.block_pct,
            clear_pct=self.clear_pct,
            samples=samples,
            span_seconds=round(span, 3),
            margin_pct=margin(level, self.block_pct),
        )
