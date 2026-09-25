"""Atomic JSON documents with revisions and compare-and-set writes."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..clock import stamp
from ..errors import Conflict, InvalidRequest, RecordNotFound, StoreError

STATE_DIR = "state"
SCHEMA_VERSION = 1
_FORBIDDEN = set('\\/:*?"<>|')


def safe_doc_id(doc_id: str) -> str:
    """Reject document ids that could escape the state directory."""

    if not isinstance(doc_id, str) or not doc_id.strip():
        raise InvalidRequest("document id must be a non-empty string")
    text = doc_id.strip()
    if any(character in _FORBIDDEN for character in text):
        raise InvalidRequest("document id must not contain path characters", document=doc_id)
    if text in (".", "..") or len(text) > 96:
        raise InvalidRequest("document id is not a plain token", document=doc_id)
    return text


@dataclass(frozen=True)
class Document:
    """One versioned state document."""

    id: str
    revision: int
    saved_at: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "revision": self.revision,
            "saved_at": self.saved_at,
            "payload": self.payload,
        }


class DocumentStore:
    """Stores one JSON document per component under a single directory.

    Every write lands through a temporary file that is renamed into place, so a
    reader either sees the previous revision or the next one and never a torn
    mixture of the two.
    """

    def __init__(self, root: Path | str, *, fsync: bool = False) -> None:
        self._root = Path(root)
        self._fsync = fsync
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, doc_id: str) -> Path:
        return self._root / f"{safe_doc_id(doc_id)}.json"

    def exists(self, doc_id: str) -> bool:
        return self.path_for(doc_id).is_file()

    def names(self) -> list[str]:
        return sorted(path.name for path in self._root.glob("*.json"))

    def load(self, doc_id: str) -> Document:
        path = self.path_for(doc_id)
        if not path.is_file():
            raise RecordNotFound("state document is missing", document=doc_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise StoreError("state document is unreadable", document=doc_id, path=str(path)) from exc
        payload = raw.get("payload")
        if not isinstance(payload, dict):
            raise StoreError("state document payload must be an object", document=doc_id)
        return Document(
            id=str(raw.get("id", doc_id)),
            revision=int(raw.get("revision", 0)),
            saved_at=str(raw.get("saved_at", "")),
            payload=dict(payload),
        )

    def try_load(self, doc_id: str) -> Document | None:
        if not self.exists(doc_id):
            return None
        return self.load(doc_id)

    def revision(self, doc_id: str) -> int:
        document = self.try_load(doc_id)
        return 0 if document is None else document.revision

    def save(self, doc_id: str, payload: Mapping[str, Any], moment) -> Document:
        return self._write(doc_id, self.revision(doc_id) + 1, payload, moment)

    def save_if(self, doc_id: str, expected_revision: int, payload: Mapping[str, Any], moment) -> Document:
        """Compare-and-set: the write lands only when the revision matches."""

        current = self.revision(doc_id)
        if current != int(expected_revision):
            raise Conflict(
                "the document moved on since it was read",
                document=doc_id,
                expected_revision=int(expected_revision),
                actual_revision=current,
            )
        return self._write(doc_id, current + 1, payload, moment)

    def delete(self, doc_id: str) -> bool:
        path = self.path_for(doc_id)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def stats(self) -> dict[str, Any]:
        documents = self.names()
        return {
            "root": str(self._root),
            "documents": len(documents),
            "document_ids": documents,
        }

    def _write(self, doc_id: str, revision: int, payload: Mapping[str, Any], moment) -> Document:
        if not isinstance(payload, Mapping):
            raise InvalidRequest("state payload must be a mapping", document=doc_id)
        target = self.path_for(doc_id)
        record = {
            "schema": SCHEMA_VERSION,
            "id": safe_doc_id(doc_id),
            "revision": int(revision),
            "saved_at": stamp(moment),
            "payload": dict(payload),
        }
        temporary = target.with_suffix(".json.tmp")
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
                handle.flush()
                if self._fsync:
                    os.fsync(handle.fileno())
            os.replace(temporary, target)
        except OSError as exc:
            raise StoreError("state document could not be written", document=doc_id) from exc
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)
        return Document(
            id=record["id"],
            revision=record["revision"],
            saved_at=record["saved_at"],
            payload=dict(payload),
        )
