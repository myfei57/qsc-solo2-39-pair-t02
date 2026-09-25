"""Durable storage: atomic documents, append-only records and versioned state."""

from __future__ import annotations

from .documents import Document, DocumentStore, safe_doc_id
from .generations import (
    BaselineRecord,
    BaselineStore,
    ConfirmationRegister,
    ConfirmationSlip,
    GenerationRegistry,
)
from .journal import JournalRecord, RecordJournal
from .records import RecordStream, ReplayOutcome
from .snapshot import Snapshot, SnapshotStore
from .watermark import CommitWatermark, Watermark

__all__ = [
    "BaselineRecord",
    "BaselineStore",
    "CommitWatermark",
    "ConfirmationRegister",
    "ConfirmationSlip",
    "Document",
    "DocumentStore",
    "GenerationRegistry",
    "JournalRecord",
    "RecordJournal",
    "RecordStream",
    "ReplayOutcome",
    "Snapshot",
    "SnapshotStore",
    "Watermark",
    "safe_doc_id",
]
