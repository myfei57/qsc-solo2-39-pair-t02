"""Sustained condition detection over a sliding window of samples."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..clock import parse_stamp, stamp
from ..errors import InvalidRequest
from ..store.documents import DocumentStore

WINDOW_CLEAR = "clear"
WINDOW_HOLDING = "holding"
WINDOW_HELD = "held"

ABOVE = "above"
BELOW = "below"


@dataclass(frozen=True)
class WindowVerdict:
    """Whether a condition has been true for the whole window."""

    subject: str
    state: str
    threshold: float
    direction: str
    samples: int
    since: str
    span_seconds: float
    latest: float

    def held(self) -> bool:
        return self.state == WINDOW_HELD

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "state": self.state,
            "threshold": self.threshold,
            "direction": self.direction,
            "samples": self.samples,
            "since": self.since,
            "span_seconds": self.span_seconds,
            "latest": self.latest,
        }


class WindowJudge:
    """Keeps a sliding window and reports when a condition has been sustained.

    A single sample never raises a condition: the judge needs at least
    ``min_samples`` readings inside ``hold_seconds`` and every one of them has to
    sit on the same side of the threshold.  That is what turns a level spike
    into a blockage alarm.
    """

    def __init__(
        self,
        subject: str,
        store: DocumentStore,
        *,
        threshold: float,
        hold_seconds: float,
        min_samples: int = 2,
        direction: str = ABOVE,
    ) -> None:
        if hold_seconds <= 0:
            raise InvalidRequest("a window needs a positive length", subject=subject)
        if min_samples < 1:
            raise InvalidRequest("a window needs at least one sample", subject=subject)
        if direction not in (ABOVE, BELOW):
            raise InvalidRequest("window direction is unknown", direction=direction)
        self.subject = subject
        self._store = store
        self.threshold = float(threshold)
        self.hold_seconds = float(hold_seconds)
        self.min_samples = int(min_samples)
        self.direction = direction
        self.doc_id = f"verdict.{subject}.window"

    def _samples(self) -> list[dict[str, Any]]:
        document = self._store.try_load(self.doc_id)
        if document is None:
            return []
        raw = document.payload.get("samples", [])
        return [dict(item) for item in raw if isinstance(item, dict)]

    def crosses(self, value: float) -> bool:
        """Whether one reading is on the tripping side of the threshold."""

        return value >= self.threshold if self.direction == ABOVE else value <= self.threshold

    def observe(self, value: float, moment: datetime) -> WindowVerdict:
        """Add one reading and report the window state it produces."""

        samples = self._samples()
        if not self.crosses(value):
            samples = []
        else:
            samples.append({"at": stamp(moment), "value": float(value)})
        samples = self._within(samples, moment)
        self._store.save(self.doc_id, {"subject": self.subject, "samples": samples}, moment)
        return self._verdict(samples, float(value), moment)

    def state(self, moment: datetime) -> WindowVerdict:
        """The window state without adding a reading."""

        samples = self._within(self._samples(), moment)
        latest = float(samples[-1]["value"]) if samples else 0.0
        return self._verdict(samples, latest, moment)

    def clear(self, moment: datetime, value: float = 0.0) -> WindowVerdict:
        """Drop the window, as an operator does once a blockage has been cleared."""

        self._store.save(self.doc_id, {"subject": self.subject, "samples": []}, moment)
        return self._verdict([], float(value), moment)

    def _within(self, samples: list[dict[str, Any]], moment: datetime) -> list[dict[str, Any]]:
        if not samples:
            return []
        # The buffer covers twice the hold so that a condition which has been
        # true for the whole hold window is still measurable once the oldest
        # sample of the window reaches the age limit.
        cutoff = moment.timestamp() - self.hold_seconds * 2.0
        kept = [item for item in samples if parse_stamp(str(item["at"])).timestamp() >= cutoff]
        return kept[-(self.min_samples * 8) :]

    def _verdict(self, samples: list[dict[str, Any]], value: float, moment: datetime) -> WindowVerdict:
        if not samples:
            return WindowVerdict(
                subject=self.subject,
                state=WINDOW_CLEAR,
                threshold=self.threshold,
                direction=self.direction,
                samples=0,
                since="",
                span_seconds=0.0,
                latest=value,
            )
        first = parse_stamp(str(samples[0]["at"]))
        span = max(0.0, (moment - first).total_seconds())
        complete = len(samples) >= self.min_samples and span >= self.hold_seconds
        return WindowVerdict(
            subject=self.subject,
            state=WINDOW_HELD if complete else WINDOW_HOLDING,
            threshold=self.threshold,
            direction=self.direction,
            samples=len(samples),
            since=stamp(first),
            span_seconds=round(span, 3),
            latest=float(samples[-1]["value"]),
        )
