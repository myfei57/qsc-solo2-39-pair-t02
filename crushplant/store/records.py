"""The record stream: staged writes, a commit watermark, replay and tombstones."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from ..errors import InvalidRequest, OrderingViolation
from .journal import JournalRecord, RecordJournal
from .watermark import CommitWatermark, Watermark


@dataclass(frozen=True)
class ReplayOutcome:
    """What a restart replay did with the records staged above the watermark."""

    stream: str
    applied: int
    sequence: int
    voided: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "stream": self.stream,
            "applied": self.applied,
            "sequence": self.sequence,
            "voided": self.voided,
        }


class RecordStream:
    """Composes the append-only journal with the commit watermark.

    Writers stage records; readers only ever see the ones at or below the
    watermark.  A restart replays everything above the watermark through the
    projection function and commits it, so the observable stream after a
    restart is the same as before the crash.
    """

    def __init__(self, journal: RecordJournal, watermark: CommitWatermark) -> None:
        self._journal = journal
        self._watermark = watermark

    @property
    def journal(self) -> RecordJournal:
        return self._journal

    @property
    def stream(self) -> str:
        return self._watermark.stream

    # ------------------------------------------------------------------ writes
    def stage(
        self,
        kind: str,
        moment: datetime,
        *,
        unit: str = "",
        actor: str = "",
        subject: str = "",
        payload: dict[str, Any] | None = None,
        ref: int | None = None,
    ) -> JournalRecord:
        """Append a record that no reader can see yet."""

        return self._journal.append(
            kind,
            moment,
            unit=unit,
            actor=actor,
            subject=subject,
            payload=payload,
            ref=ref,
        )

    def commit(self, moment: datetime, actor: str) -> Watermark:
        """Publish every record staged so far."""

        return self._watermark.commit(self._journal.sequence, moment, actor)

    def commit_through(self, sequence: int, moment: datetime, actor: str) -> Watermark:
        if sequence > self._journal.sequence:
            raise OrderingViolation(
                "cannot commit a record that was never staged",
                staged=self._journal.sequence,
                requested=sequence,
            )
        return self._watermark.commit(sequence, moment, actor)

    def tombstone(self, sequence: int, moment: datetime, actor: str, reason: str) -> JournalRecord:
        """Void a committed record by appending a reference to it."""

        if not reason.strip():
            raise InvalidRequest("a tombstone needs a reason", sequence=sequence)
        target = self._journal.require(sequence)
        if target.is_void:
            raise InvalidRequest("a void record cannot itself be voided", sequence=sequence)
        if sequence > self._watermark.sequence():
            raise OrderingViolation(
                "only a committed record can be tombstoned",
                sequence=sequence,
                watermark=self._watermark.sequence(),
            )
        marker = self.stage(
            "void",
            moment,
            unit=target.unit,
            actor=actor,
            subject=target.subject,
            payload={"reason": reason.strip(), "kind": target.kind},
            ref=sequence,
        )
        self.commit(moment, actor)
        return marker

    def rollback_to(self, sequence: int, moment: datetime, actor: str, reason: str) -> Watermark:
        """Pull the watermark back; the records above it stop being observable."""

        if sequence > self._journal.sequence:
            raise OrderingViolation(
                "cannot roll back past the end of the journal",
                staged=self._journal.sequence,
                requested=sequence,
            )
        return self._watermark.rollback(sequence, moment, actor, reason)

    # ------------------------------------------------------------------- reads
    def committed(self) -> list[JournalRecord]:
        """Committed records including the void markers themselves."""

        return self._journal.upto(self._watermark.sequence())

    def pending(self) -> list[JournalRecord]:
        """Records staged above the watermark, invisible to readers."""

        return self._journal.after(self._watermark.sequence())

    def visible(self) -> list[JournalRecord]:
        """The records a reader may observe: committed and not voided."""

        voided = self.voided_sequences()
        return [
            record
            for record in self.committed()
            if not record.is_void and record.sequence not in voided
        ]

    def voided_sequences(self) -> dict[int, str]:
        """Voided sequences, counting only markers the watermark has published."""

        published = self._watermark.sequence()
        return {
            sequence: reason
            for sequence, reason in self._journal.voids().items()
            if sequence <= published
        }

    def history(self) -> list[dict[str, Any]]:
        """Every committed record with its tombstone annotation."""

        voided = self.voided_sequences()
        entries: list[dict[str, Any]] = []
        for record in self.committed():
            entry = record.as_dict()
            entry["voided"] = record.sequence in voided
            if entry["voided"]:
                entry["void_reason"] = voided[record.sequence]
            entries.append(entry)
        return entries

    def tail(self, limit: int = 20) -> list[JournalRecord]:
        if limit <= 0:
            return []
        return self.visible()[-limit:]

    # -------------------------------------------------------------- restarting
    def replay(
        self,
        apply_record: Callable[[JournalRecord], Any],
        moment: datetime,
        actor: str,
    ) -> ReplayOutcome:
        """Apply the records above the watermark and publish them."""

        outstanding = self.pending()
        applied = 0
        for record in outstanding:
            apply_record(record)
            applied += 1
        # The watermark only moves once every projection has been applied, so a
        # replay that fails leaves the stream exactly where the crash left it.
        if outstanding:
            self._watermark.commit(outstanding[-1].sequence, moment, actor)
        return ReplayOutcome(
            stream=self.stream,
            applied=applied,
            sequence=self._watermark.sequence(),
            voided=len(self.voided_sequences()),
        )

    def summary(self) -> dict[str, Any]:
        watermark = self._watermark.current()
        return {
            "stream": self.stream,
            "journal_lines": self._journal.lines(),
            "last_sequence": self._journal.sequence,
            "watermark": watermark.as_dict(),
            "committed": len(self.committed()),
            "visible": len(self.visible()),
            "pending": len(self.pending()),
            "voided": len(self.voided_sequences()),
            "kinds": self._journal.kinds(),
        }

    def watermark_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """The commits and rollbacks this stream has seen, newest last."""

        entries = self._watermark.history()
        if limit <= 0:
            return []
        return entries[-limit:]
