"""Belt scale calibration and the mapping the tonnage figures depend on."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from ..audit.ledger import OUTCOME_OK, AuditLedger
from ..errors import InvalidRequest, StaleRecord
from ..store.documents import DocumentStore
from ..store.generations import BaselineRecord, BaselineStore, GenerationRegistry
from ..units import apply_scale, belt_tonnes_per_hour
from ..verdict.log import VerdictLog


@dataclass(frozen=True)
class ScalePoint:
    """One calibration point: what the load cell read against a known load."""

    raw_counts: float
    kg_per_m: float

    def as_dict(self) -> dict[str, Any]:
        return {"raw_counts": self.raw_counts, "kg_per_m": self.kg_per_m}


def fit_scale(points: Sequence[ScalePoint]) -> tuple[float, float]:
    """Least squares fit of ``kg_per_m = zero + span * raw``.

    Two distinct calibration points are enough to fix the line, and a set that
    has no spread at all is refused rather than silently returning a slope of
    zero.
    """

    if len(points) < 2:
        raise InvalidRequest("a belt scale needs at least two calibration points")
    count = float(len(points))
    mean_raw = sum(point.raw_counts for point in points) / count
    mean_load = sum(point.kg_per_m for point in points) / count
    covariance = sum((point.raw_counts - mean_raw) * (point.kg_per_m - mean_load) for point in points)
    variance = sum((point.raw_counts - mean_raw) ** 2 for point in points)
    if variance == 0:
        raise InvalidRequest("calibration points do not span a range of readings")
    span = covariance / variance
    zero = mean_load - span * mean_raw
    return (round(zero, 6), round(span, 6))


@dataclass(frozen=True)
class ScaleReading:
    """One belt load reading mapped through the live calibration."""

    unit: str
    raw_counts: float
    kg_per_m: float
    tonnes_per_hour: float
    zero_kg_per_m: float
    span_kg_per_m: float
    generation: int
    captured_at: str
    baseline_at: str
    age_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit,
            "raw_counts": self.raw_counts,
            "kg_per_m": self.kg_per_m,
            "tonnes_per_hour": self.tonnes_per_hour,
            "zero_kg_per_m": self.zero_kg_per_m,
            "span_kg_per_m": self.span_kg_per_m,
            "generation": self.generation,
            "captured_at": self.captured_at,
            "baseline_at": self.baseline_at,
            "age_seconds": self.age_seconds,
        }


class BeltScale:
    """Keeps the belt scale mapping and refuses to weigh against a stale one."""

    def __init__(
        self,
        unit: str,
        belt_speed_mps: float,
        store: DocumentStore,
        ledger: AuditLedger,
        baselines: BaselineStore,
        generations: GenerationRegistry,
        verdicts: VerdictLog,
    ) -> None:
        self.unit = unit
        self.speed_mps = float(belt_speed_mps)
        self._store = store
        self._ledger = ledger
        self._baselines = baselines
        self._generations = generations
        self._verdicts = verdicts
        self.subject = f"{unit}.belt-scale"

    def calibrate(
        self,
        points: Sequence[ScalePoint],
        moment: datetime,
        actor: str,
        ttl_seconds: float,
    ) -> BaselineRecord:
        """Fit the mapping and store it as a baseline for this generation."""

        zero, span = fit_scale(points)
        record = self._baselines.record(
            self.subject,
            span,
            self._generations.generation(),
            moment,
            ttl_seconds,
            samples=len(points),
            actor=actor,
        )
        self._store.save(
            f"{self.unit}.belt.scale-zero",
            {"zero_kg_per_m": zero, "span_kg_per_m": span, "at": record.captured_at},
            moment,
        )
        self._ledger.record(
            self.unit,
            "belt.calibrate",
            OUTCOME_OK,
            actor,
            moment,
            subject=self.subject,
            zero_kg_per_m=zero,
            span_kg_per_m=span,
            points=len(points),
            generation=record.generation,
        )
        return record

    def mapping(self) -> dict[str, Any]:
        """The stored calibration, without checking whether it is still usable."""

        zero_document = self._store.try_load(f"{self.unit}.belt.scale-zero")
        zero = 0.0 if zero_document is None else float(zero_document.payload.get("zero_kg_per_m", 0.0))
        latest = self._baselines.latest(self.subject)
        return {
            "subject": self.subject,
            "zero_kg_per_m": zero,
            "span_kg_per_m": 0.0 if latest is None else latest.value,
            "generation": None if latest is None else latest.generation,
            "captured_at": None if latest is None else latest.captured_at,
            "expires_at": None if latest is None else latest.expires_at,
        }

    def require_map(self, moment: datetime) -> BaselineRecord:
        return self._baselines.require_fresh(self.subject, self._generations.generation(), moment)

    def read(self, raw_counts: float, moment: datetime, actor: str) -> ScaleReading:
        """Map one load cell reading, refusing a calibration that expired."""

        try:
            record = self.require_map(moment)
        except StaleRecord as error:
            self._verdicts.record(
                self.unit,
                self.subject,
                "belt.scale",
                "stale",
                float(raw_counts),
                moment,
                actor,
                reason=error.reason,
            )
            raise
        zero_document = self._store.try_load(f"{self.unit}.belt.scale-zero")
        zero = 0.0 if zero_document is None else float(zero_document.payload.get("zero_kg_per_m", 0.0))
        kg_per_m = apply_scale(float(raw_counts), zero, record.value)
        reading = ScaleReading(
            unit=self.unit,
            raw_counts=round(float(raw_counts), 6),
            kg_per_m=kg_per_m,
            tonnes_per_hour=belt_tonnes_per_hour(kg_per_m, self.speed_mps),
            zero_kg_per_m=zero,
            span_kg_per_m=record.value,
            generation=record.generation,
            captured_at=record.captured_at,
            baseline_at=record.captured_at,
            age_seconds=record.age_seconds(moment),
        )
        return reading
