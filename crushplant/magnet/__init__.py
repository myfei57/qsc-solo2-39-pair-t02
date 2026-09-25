"""Overbelt magnet: its alarm, its readiness and the reset that clears it."""

from __future__ import annotations

from .alarm import MagnetAlarm, AlarmState
from .unit import MagnetSeparator, MagnetState

__all__ = ["AlarmState", "MagnetAlarm", "MagnetSeparator", "MagnetState"]
