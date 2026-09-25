"""The three ordered sequences a crushing line has to respect."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from ..errors import InvalidRequest, OrderingViolation

START_STEPS: tuple[str, ...] = (
    "jaw-state",
    "magnet-ready",
    "belt-start",
    "screen-start",
    "cone-start",
    "feed",
)

STOP_STEPS: tuple[str, ...] = (
    "feed-stop",
    "drain",
    "jaw-stop",
    "cone-stop",
    "belt-stop",
)

TRIP_STEPS: tuple[str, ...] = (
    "feed-stop",
    "belt-stop",
    "jaw-stop",
    "cone-stop",
    "audit",
)


@dataclass(frozen=True)
class SequencePlan:
    """Enforces one ordering over a named list of process steps.

    Every sequence is walked strictly front to back and a step that has already
    run cannot run a second time, which is what stops a caller from starting the
    feeder before the crusher state is on disk or from stopping the crusher
    before the feeder has come down.
    """

    unit: str
    label: str
    steps: tuple[str, ...]

    def outstanding(self, done: Sequence[str]) -> list[str]:
        completed = set(done)
        return [step for step in self.steps if step not in completed]

    def next_step(self, done: Sequence[str]) -> str:
        """The only step a caller may run next."""

        outstanding = self.outstanding(done)
        if not outstanding:
            raise OrderingViolation(
                f"the {self.label} sequence is already complete",
                unit=self.unit,
                sequence=self.label,
            )
        return outstanding[0]

    def require_step(self, done: Sequence[str], step: str) -> None:
        """Refuse a step that arrives before its predecessor has finished."""

        if step not in self.steps:
            raise InvalidRequest(
                "that step is not part of the sequence",
                step=step,
                unit=self.unit,
                sequence=self.label,
            )
        completed = set(done)
        if step in completed:
            raise OrderingViolation(
                "that step already ran",
                unit=self.unit,
                sequence=self.label,
                step=step,
            )
        expected = self.next_step(done)
        if step != expected:
            raise OrderingViolation(
                "that step is out of order",
                unit=self.unit,
                sequence=self.label,
                step=step,
                expected=expected,
                completed=[item for item in self.steps if item in completed],
            )

    def is_complete(self, done: Sequence[str]) -> bool:
        return not self.outstanding(done)

    def as_dict(self, done: Sequence[str]) -> dict[str, Any]:
        completed = set(done)
        return {
            "unit": self.unit,
            "sequence": self.label,
            "steps": list(self.steps),
            "completed": [step for step in self.steps if step in completed],
            "outstanding": self.outstanding(done),
        }


def start_plan(unit: str) -> SequencePlan:
    """Bring-up order: crusher state, magnet, belts, screen, then feed."""

    return SequencePlan(unit=unit, label="start", steps=START_STEPS)


def stop_plan(unit: str) -> SequencePlan:
    """Shutdown order: feed, drain, crushers, then the discharge belt."""

    return SequencePlan(unit=unit, label="stop", steps=STOP_STEPS)


def trip_plan(unit: str) -> SequencePlan:
    """Emergency order: cut the feed, shed the belts, then the machines."""

    return SequencePlan(unit=unit, label="trip", steps=TRIP_STEPS)
