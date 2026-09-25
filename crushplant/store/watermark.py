"""Commit watermark for a record stream.

The watermark is the sequence a reader is allowed to observe.  Everything at or
below it is committed; everything above it was staged by a writer that has not
finished yet and stays invisible until it does.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..clock import stamp
from ..errors import InvalidRequest
from .documents import DocumentStore

WATERMARK_DOC = "records.watermark"
HISTORY_LIMIT = 40


@dataclass(frozen=True)
class Watermark:
    """The highest sequence a reader may observe for one stream."""

    stream: str
    sequence: int = 0
    committed_at: str = ""
    actor: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "stream": self.stream,
            "sequence": self.sequence,
            "committed_at": self.committed_at,
            "actor": self.actor,
        }


class CommitWatermark:
    """Persists the committed sequence plus the trail of commits and rollbacks."""

    def __init__(self, store: DocumentStore, stream: str) -> None:
        self._store = store
        self.stream = stream
        self.doc_id = WATERMARK_DOC

    def _payload(self) -> dict[str, Any]:
        document = self._store.try_load(self.doc_id)
        if document is None:
            return {}
        return document.payload

    def current(self) -> Watermark:
        payload = self._payload()
        return Watermark(
            stream=self.stream,
            sequence=int(payload.get("sequence", 0)),
            committed_at=str(payload.get("committed_at", "")),
            actor=str(payload.get("actor", "")),
        )

    def sequence(self) -> int:
        return self.current().sequence

    def commit(self, sequence: int, moment: datetime, actor: str) -> Watermark:
        """Move the watermark forward to ``sequence``."""

        if sequence < 0:
            raise InvalidRequest("a watermark cannot be negative", sequence=sequence)
        current = self.sequence()
        if sequence < current:
            raise InvalidRequest(
                "use the rollback path to move a watermark backwards",
                current=current,
                requested=sequence,
            )
        return self._write(sequence, moment, actor, event="commit")

    def rollback(self, sequence: int, moment: datetime, actor: str, reason: str) -> Watermark:
        """Pull the watermark back so later records stop being observable."""

        if not reason.strip():
            raise InvalidRequest("a rollback needs a reason")
        return self._write(max(0, int(sequence)), moment, actor, event="rollback", reason=reason)

    def history(self) -> list[dict[str, Any]]:
        entries = self._payload().get("history", [])
        return [dict(entry) for entry in entries if isinstance(entry, dict)]

    def _write(
        self,
        sequence: int,
        moment: datetime,
        actor: str,
        *,
        event: str,
        reason: str = "",
    ) -> Watermark:
        entry = {
            "event": event,
            "sequence": int(sequence),
            "at": stamp(moment),
            "actor": actor.strip(),
        }
        if reason:
            entry["reason"] = reason.strip()
        history = self.history()
        history.append(entry)
        watermark = Watermark(
            stream=self.stream,
            sequence=int(sequence),
            committed_at=entry["at"],
            actor=entry["actor"],
        )
        payload: dict[str, Any] = {
            "stream": self.stream,
            "sequence": watermark.sequence,
            "committed_at": watermark.committed_at,
            "actor": watermark.actor,
            "history": history[-HISTORY_LIMIT:],
        }
        self._store.save(self.doc_id, payload, moment)
        return watermark
