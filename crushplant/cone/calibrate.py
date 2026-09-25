"""Current calibration for the secondary crusher.

The draw of a cone is close to a straight line in throughput, so a handful of
paired readings fixes both an idle current and a gain.  A stall judgement that
was taken against an old line is worthless, so the fit is stored as a baseline
with a generation and a deadline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from ..audit.ledger import OUTCOME_OK, AuditLedger
from ..errors import InvalidRequest
from ..store.documents import DocumentStore
from ..store.generations import BaselineRecord, BaselineStore, GenerationRegistry


@dataclass(frozen=True)
class CurrentPoint:
    """One paired reading: a throughput and the draw it produced."""

    tonnes_per_hour: float
    amps: float

    def as_dict(self) -> dict[str, Any]:
        return {"tonnes_per_hour": self.tonnes_per_hour, "amps": self.amps}


def fit_current(points: Sequence[CurrentPoint]) -> tuple[float, float]:
    """Least squares fit of ``amps = idle + gain * tonnes_per_hour``."""

    if len(points) < 2:
        raise InvalidRequest("a current calibration needs at least two points")
    count = float(len(points))
    mean_load = sum(point.tonnes_per_hour for point in points) / count
    mean_amps = sum(point.amps for point in points) / count
    covariance = sum((point.tonnes_per_hour - mean_load) * (point.amps - mean_amps) for point in points)
    variance = sum((point.tonnes_per_hour - mean_load) ** 2 for point in points)
    if variance == 0:
        raise InvalidRequest("calibration points do not span a range of loads")
    gain = covariance / variance
    idle = mean_amps - gain * mean_load
    if gain <= 0:
        raise InvalidRequest("the fitted gain must be positive", gain=round(gain, 6))
    return (round(idle, 6), round(gain, 6))


class CurrentCalibration:
    """Keeps the live current line and refuses one that has gone stale."""

    def __init__(
        self,
        unit: str,
        store: DocumentStore,
        ledger: AuditLedger,
        baselines: BaselineStore,
        generations: GenerationRegistry,
    ) -> None:
        self.unit = unit
        self._store = store
        self._ledger = ledger
        self._baselines = baselines
        self._generations = generations
        self.subject = f"{unit}.cone-current"
        self.doc_id = f"cone.{unit}.idle"

    def calibrate(
        self,
        points: Sequence[CurrentPoint],
        moment: datetime,
        actor: str,
        ttl_seconds: float,
    ) -> BaselineRecord:
        idle, gain = fit_current(points)
        record = self._baselines.record(
            self.subject,
            gain,
            self._generations.generation(),
            moment,
            ttl_seconds,
            samples=len(points),
            actor=actor,
        )
        self._store.save(
            self.doc_id,
            {"unit": self.unit, "idle_amps": idle, "gain_amps_per_tph": gain, "at": record.captured_at},
            moment,
        )
        self._ledger.record(
            self.unit,
            "cone.calibrate",
            OUTCOME_OK,
            actor,
            moment,
            subject=self.subject,
            idle_amps=idle,
            gain_amps_per_tph=gain,
            points=len(points),
            generation=record.generation,
        )
        return record

    def require(self, moment: datetime) -> BaselineRecord:
        return self._baselines.require_fresh(self.subject, self._generations.generation(), moment)

    def idle_amps(self) -> float:
        document = self._store.try_load(self.doc_id)
        return 0.0 if document is None else float(document.payload.get("idle_amps", 0.0))

    def expected_amps(self, tonnes_per_hour: float, moment: datetime) -> float:
        """What the line says the draw should be at this throughput."""

        record = self.require(moment)
        return round(self.idle_amps() + record.value * float(tonnes_per_hour), 3)

    def summary(self) -> dict[str, Any]:
        record = self._baselines.latest(self.subject)
        return {
            "subject": self.subject,
            "idle_amps": self.idle_amps(),
            "gain_amps_per_tph": None if record is None else record.value,
            "generation": None if record is None else record.generation,
            "captured_at": None if record is None else record.captured_at,
            "expires_at": None if record is None else record.expires_at,
        }
