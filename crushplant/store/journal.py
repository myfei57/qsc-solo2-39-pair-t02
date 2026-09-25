"""Append-only record journal.

The journal is the only artefact that is expected to survive a crash in full:
records are appended and flushed before the caller continues, and nothing in
this module can rewrite or delete an existing line.  A record is voided by
appending a later record of kind ``void`` that points at it, which keeps the
original bytes available for inspection.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..clock import parse_stamp, stamp
from ..errors import InvalidRequest, RecordNotFound, StoreError

VOID_KIND = "void"


@dataclass(frozen=True)
class JournalRecord:
    """One immutable entry of the record stream."""

    sequence: int
    kind: str
    at: str
    unit: str = ""
    actor: str = ""
    subject: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    ref: int | None = None

    def as_dict(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "sequence": self.sequence,
            "kind": self.kind,
            "at": self.at,
            "unit": self.unit,
            "actor": self.actor,
            "subject": self.subject,
            "payload": self.payload,
        }
        if self.ref is not None:
            record["ref"] = self.ref
        return record

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "JournalRecord":
        payload = raw.get("payload")
        ref = raw.get("ref")
        return cls(
            sequence=int(raw.get("sequence", 0)),
            kind=str(raw.get("kind", "")),
            at=str(raw.get("at", "")),
            unit=str(raw.get("unit", "")),
            actor=str(raw.get("actor", "")),
            subject=str(raw.get("subject", "")),
            payload=dict(payload) if isinstance(payload, dict) else {},
            ref=None if ref is None else int(ref),
        )

    @property
    def is_void(self) -> bool:
        return self.kind == VOID_KIND

    def moment(self) -> datetime:
        return parse_stamp(self.at)


class RecordJournal:
    """Newline-delimited JSON records for one stream.

    The parsed records are held in memory once they have been read, because the
    file itself only ever grows: nothing outside this class touches it, so the
    cached copy cannot go stale inside a running process.
    """

    def __init__(self, path: Path | str, *, fsync: bool = True) -> None:
        self._path = Path(path)
        self._fsync = fsync
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._records: list[JournalRecord] | None = None
        self._sequence = 0

    @property
    def path(self) -> Path:
        return self._path

    @property
    def sequence(self) -> int:
        self._load()
        return self._sequence

    def append(
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
        """Append one record; the file itself is never rewritten."""

        label = kind.strip()
        if not label:
            raise InvalidRequest("journal record needs a kind")
        if label == VOID_KIND and ref is None:
            raise InvalidRequest("a void record must point at a sequence")
        if label != VOID_KIND and ref is not None:
            raise InvalidRequest("only a void record may carry a reference", kind=label)
        record = JournalRecord(
            sequence=self.sequence + 1,
            kind=label,
            at=stamp(moment),
            unit=unit.strip(),
            actor=actor.strip(),
            subject=subject.strip(),
            payload=dict(payload or {}),
            ref=ref,
        )
        line = json.dumps(record.as_dict(), ensure_ascii=False, sort_keys=True)
        try:
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                if self._fsync:
                    os.fsync(handle.fileno())
        except OSError as exc:
            raise StoreError("journal record could not be appended", path=str(self._path)) from exc
        self._load().append(record)
        self._sequence = record.sequence
        return record

    def records(self) -> list[JournalRecord]:
        return list(self._load())

    def lines(self) -> int:
        """Number of physical lines, used to prove the file only grows."""

        if not self._path.is_file():
            return 0
        return len([line for line in self._path.read_text(encoding="utf-8").splitlines() if line.strip()])

    def find(self, sequence: int) -> JournalRecord | None:
        for record in self.records():
            if record.sequence == sequence:
                return record
        return None

    def require(self, sequence: int) -> JournalRecord:
        record = self.find(sequence)
        if record is None:
            raise RecordNotFound("no record with that sequence", sequence=sequence)
        return record

    def after(self, sequence: int) -> list[JournalRecord]:
        return [record for record in self.records() if record.sequence > sequence]

    def upto(self, sequence: int) -> list[JournalRecord]:
        return [record for record in self.records() if record.sequence <= sequence]

    def tail(self, limit: int = 20) -> list[JournalRecord]:
        if limit <= 0:
            return []
        return self.records()[-limit:]

    def voids(self) -> dict[int, str]:
        """Map each voided sequence onto the reason recorded for it."""

        voids: dict[int, str] = {}
        for record in self.records():
            if record.is_void and record.ref is not None:
                reason = record.payload.get("reason")
                voids[record.ref] = str(reason) if reason is not None else ""
        return voids

    def count(self, kind: str | None = None) -> int:
        records = self.records()
        if kind is None:
            return len(records)
        return sum(1 for record in records if record.kind == kind)

    def kinds(self) -> list[str]:
        seen: list[str] = []
        for record in self.records():
            if record.kind not in seen:
                seen.append(record.kind)
        return seen

    def for_unit(self, unit: str) -> list[JournalRecord]:
        return [record for record in self.records() if record.unit == unit]

    def _load(self) -> list[JournalRecord]:
        if self._records is not None:
            return self._records
        if not self._path.is_file():
            self._records = []
            return self._records
        found: list[JournalRecord] = []
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise StoreError("journal line is not valid JSON", path=str(self._path)) from exc
                if isinstance(raw, dict):
                    found.append(JournalRecord.from_dict(raw))
        except OSError as exc:
            raise StoreError("journal could not be read", path=str(self._path)) from exc
        self._records = found
        self._sequence = 0 if not found else found[-1].sequence
        return self._records
