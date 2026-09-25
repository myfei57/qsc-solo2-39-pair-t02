"""Judgement helpers: threshold windows and the verdict trail."""

from __future__ import annotations

from .log import VERDICT_KIND, VerdictEntry, VerdictLog
from .threshold import STATE_HIGH, STATE_LOW, STATE_OK, Limit, Verdict, judge
from .window import (
    WINDOW_CLEAR,
    WINDOW_HELD,
    WINDOW_HOLDING,
    WindowJudge,
    WindowVerdict,
)

__all__ = [
    "STATE_HIGH",
    "STATE_LOW",
    "STATE_OK",
    "VERDICT_KIND",
    "WINDOW_CLEAR",
    "WINDOW_HELD",
    "WINDOW_HOLDING",
    "Limit",
    "Verdict",
    "VerdictEntry",
    "VerdictLog",
    "WindowJudge",
    "WindowVerdict",
    "judge",
]
