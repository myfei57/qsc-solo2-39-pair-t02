"""The verdict trail: what the current state says and what history recorded."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from ..clock import parse_stamp
from ..errors import InvalidRequest
from ..store.records import RecordStream

VERDICT_KIND = "verdict"


@dataclass(frozen=True)
class VerdictEntry:
    """One judgement as it was written to the record stream."""

    sequence: int
    at: str
    unit: str
    subject: str
    name: str
    state: str
    value: float
    actor: str
    generation: int
    basis: str
    source: str
    detail: dict[str, Any]

    def moment(self) -> datetime:
        return parse_stamp(self.at)

    def ok(self) -> bool:
        return self.state == "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "at": self.at,
            "unit": self.unit,
            "subject": self.subject,
            "name": self.name,
            "state": self.state,
            "value": self.value,
            "actor": self.actor,
            "generation": self.generation,
            "basis": self.basis,
            "source": self.source,
            "detail": self.detail,
        }


class VerdictLog:
    """Writes every judgement to the stream and reads the newest one back.

    ``current`` answers "what does the line say now", while ``history`` answers
    "what did it say along the way".  The two are deliberately different calls
    so a report cannot present an archived judgement as a live one.
    """

    def __init__(self, stream: RecordStream, generation_of: Callable[[], int] | None = None) -> None:
        self._stream = stream
        self._generation_of = generation_of or (lambda: 0)

    @property
    def stream(self) -> RecordStream:
        return self._stream

    def record(
        self,
        unit: str,
        subject: str,
        name: str,
        state: str,
        value: float,
        moment: datetime,
        actor: str,
        *,
        generation: int = 0,
        basis: str = "",
        source: str = "",
        **detail: Any,
    ) -> VerdictEntry:
        if not name.strip():
            raise InvalidRequest("a verdict needs a name")
        payload = {
            "name": name.strip(),
            "state": state,
            "value": float(value),
            "generation": int(generation) or int(self._generation_of()),
            "basis": basis.strip(),
            "source": source.strip(),
            "detail": dict(detail),
        }
        record = self._stream.stage(
            VERDICT_KIND,
            moment,
            unit=unit,
            actor=actor,
            subject=subject.strip(),
            payload=payload,
        )
        self._stream.commit_through(record.sequence, moment, actor)
        return self._entry(record)

    def record_threshold(
        self,
        unit: str,
        verdict: Any,
        moment: datetime,
        actor: str,
        **detail: Any,
    ) -> VerdictEntry:
        """Write a :class:`~crushplant.verdict.threshold.Verdict` down as it stands."""

        return self.record(
            unit,
            verdict.subject,
            verdict.name,
            verdict.state,
            verdict.value,
            moment,
            actor,
            basis=verdict.name,
            low=verdict.low,
            high=verdict.high,
            margin=verdict.margin,
            **detail,
        )

    def record_window(
        self,
        unit: str,
        verdict: Any,
        moment: datetime,
        actor: str,
        **detail: Any,
    ) -> VerdictEntry:
        """Write a :class:`~crushplant.verdict.window.WindowVerdict` down."""

        return self.record(
            unit,
            verdict.subject,
            f"{verdict.subject}.window",
            verdict.state,
            verdict.latest,
            moment,
            actor,
            basis=f"{verdict.subject}.window",
            threshold=verdict.threshold,
            samples=verdict.samples,
            span_seconds=verdict.span_seconds,
            **detail,
        )

    def as_of(self, moment: datetime, subject: str = "") -> dict[str, VerdictEntry]:
        """The newest judgement per subject at or before one instant.

        This is the historical view: it answers what the line said back then,
        while :meth:`current` answers what it says now.  The two only agree
        until the next judgement of the same subject is written down.
        """

        newest: dict[str, VerdictEntry] = {}
        for entry in self.entries(subject=subject):
            if entry.moment() > moment:
                continue
            newest[entry.subject] = entry
        return newest

    def by_basis(self, basis: str, limit: int | None = None) -> list[VerdictEntry]:
        """Every judgement taken against one basis, newest last."""

        wanted = basis.strip()
        if not wanted:
            raise InvalidRequest("a basis filter needs a name")
        selected = [entry for entry in self.entries() if entry.basis == wanted]
        if limit is None:
            return selected
        if limit < 0:
            raise InvalidRequest("a verdict limit must not be negative")
        return selected[-limit:] if limit else []

    def generations(self) -> list[int]:
        seen: list[int] = []
        for entry in self.entries():
            if entry.generation not in seen:
                seen.append(entry.generation)
        return sorted(seen)

    def entries(self, subject: str = "", unit: str = "", limit: int | None = None) -> list[VerdictEntry]:
        selected = [
            self._entry(record)
            for record in self._stream.visible()
            if record.kind == VERDICT_KIND
            and (not subject or record.subject == subject)
            and (not unit or record.unit == unit)
        ]
        if limit is None:
            return selected
        if limit < 0:
            raise InvalidRequest("a verdict limit must not be negative")
        return selected[-limit:] if limit else []

    def history(self, subject: str = "") -> list[VerdictEntry]:
        return self.entries(subject=subject)

    def current(self, subject: str = "") -> dict[str, VerdictEntry]:
        """The newest judgement per subject, which is what the line says now."""

        newest: dict[str, VerdictEntry] = {}
        for entry in self.entries(subject=subject):
            newest[entry.subject] = entry
        return newest

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries():
            counts[entry.state] = counts.get(entry.state, 0) + 1
        return counts

    def summary(self) -> dict[str, Any]:
        return {
            "recorded": len(self.entries()),
            "subjects": sorted(self.current()),
            "bases": sorted({entry.basis for entry in self.entries() if entry.basis}),
            "generations": self.generations(),
            "states": self.counts(),
        }

    @staticmethod
    def _entry(record: Any) -> VerdictEntry:
        payload = record.payload
        detail = payload.get("detail")
        return VerdictEntry(
            sequence=record.sequence,
            at=record.at,
            unit=record.unit,
            subject=record.subject,
            name=str(payload.get("name", "")),
            state=str(payload.get("state", "")),
            value=float(payload.get("value", 0.0)),
            actor=record.actor,
            generation=int(payload.get("generation", 0)),
            basis=str(payload.get("basis", "")),
            source=str(payload.get("source", "")),
            detail=dict(detail) if isinstance(detail, dict) else {},
        )
