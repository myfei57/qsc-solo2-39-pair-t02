"""Clock sources so every sequence runs against manual or wall time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol

DEFAULT_EPOCH = datetime(2026, 4, 6, 5, 30, 0, tzinfo=timezone.utc)


class Clock(Protocol):
    """Minimal clock surface used by the control components."""

    def now(self) -> datetime:
        """Current instant in UTC."""

    def advance(self, seconds: float) -> None:
        """Move the clock forward; the wall clock refuses."""


class WallClock:
    """Real time source used by the running service."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def advance(self, seconds: float) -> None:
        raise RuntimeError("the wall clock cannot be advanced")


class ManualClock:
    """Deterministic clock used by tests and the scripted runs."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or DEFAULT_EPOCH

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("a manual clock only moves forward")
        self._now = self._now + timedelta(seconds=seconds)

    def set(self, moment: datetime) -> None:
        if moment < self._now:
            raise ValueError("a manual clock only moves forward")
        self._now = moment


def stamp(moment: datetime) -> str:
    """Serialise an instant for the store, the journal and the ledger."""

    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_stamp(text: str) -> datetime:
    """Read back a timestamp produced by :func:`stamp`."""

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)
