"""Feed gate and the apron feeder it protects."""

from __future__ import annotations

from .feeder import Feeder, FeederState
from .gate import FeedGate

__all__ = ["FeedGate", "Feeder", "FeederState"]
