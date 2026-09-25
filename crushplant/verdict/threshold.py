"""Threshold comparison for readings that sit inside a two sided window."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..clock import stamp
from ..errors import InvalidRequest

STATE_OK = "ok"
STATE_LOW = "low"
STATE_HIGH = "high"


@dataclass(frozen=True)
class Limit:
    """An inclusive operating window for one measured quantity."""

    name: str
    low: float
    high: float

    def __post_init__(self) -> None:
        if self.low >= self.high:
            raise InvalidRequest("limit window is inverted", limit=self.name, low=self.low, high=self.high)

    def width(self) -> float:
        return round(self.high - self.low, 6)

    def contains(self, value: float) -> bool:
        return self.low <= value <= self.high

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "low": self.low, "high": self.high, "width": self.width()}


@dataclass(frozen=True)
class Verdict:
    """What one reading did against its window."""

    subject: str
    name: str
    state: str
    value: float
    low: float
    high: float
    margin: float
    at: str

    def ok(self) -> bool:
        return self.state == STATE_OK

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "name": self.name,
            "state": self.state,
            "value": self.value,
            "low": self.low,
            "high": self.high,
            "margin": self.margin,
            "at": self.at,
        }


def judge(subject: str, value: float, limit: Limit, moment: datetime) -> Verdict:
    """Compare one reading against its window and report how much room is left.

    The margin is the distance to the nearest bound while the reading sits
    inside the window, and the negative distance past the bound once it does not,
    so a caller never has to reason about which side was crossed.
    """

    if value > limit.high:
        state = STATE_HIGH
        headroom = limit.high - value
    elif value < limit.low:
        state = STATE_LOW
        headroom = value - limit.low
    else:
        state = STATE_OK
        headroom = min(value - limit.low, limit.high - value)
    return Verdict(
        subject=subject.strip(),
        name=limit.name,
        state=state,
        value=value,
        low=limit.low,
        high=limit.high,
        margin=round(headroom, 6),
        at=stamp(moment),
    )
