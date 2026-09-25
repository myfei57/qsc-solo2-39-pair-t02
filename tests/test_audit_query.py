"""The filter conditions a caller may put on the operation ledger."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from crushplant.audit.ledger import OUTCOME_BLOCKED, OUTCOME_OK, AuditLedger
from crushplant.audit.query import AuditQuery
from crushplant.errors import InvalidRequest
from crushplant.store.documents import DocumentStore
from crushplant.store.journal import RecordJournal
from crushplant.store.records import RecordStream
from crushplant.store.watermark import CommitWatermark

START = datetime(2026, 4, 6, 5, 30, 0, tzinfo=timezone.utc)


def filled_ledger(tmp_path: Path) -> AuditLedger:
    store = DocumentStore(tmp_path / "state")
    stream = RecordStream(
        RecordJournal(tmp_path / "records" / "operations.jsonl", fsync=False),
        CommitWatermark(store, "operations"),
    )
    ledger = AuditLedger(stream)
    ledger.record("CP-1", "feeder.start", OUTCOME_OK, "operator", START, subject="feed")
    ledger.record("CP-1", "belt.start", OUTCOME_BLOCKED, "operator", START + timedelta(seconds=30), subject="belt")
    ledger.record("CP-2", "feeder.start", OUTCOME_OK, "operator", START + timedelta(seconds=90), subject="feed")
    ledger.record("CP-2", "chute.blocked", OUTCOME_BLOCKED, "control", START + timedelta(seconds=120), subject="chute")
    return ledger


def test_a_query_narrows_the_ledger_by_unit(tmp_path: Path) -> None:
    ledger = filled_ledger(tmp_path)

    only_one = ledger.entries(AuditQuery(unit="CP-1"))

    assert [entry.action for entry in only_one] == ["feeder.start", "belt.start"]
    assert ledger.units() == ["CP-1", "CP-2"]


def test_a_query_narrows_by_action_and_outcome(tmp_path: Path) -> None:
    ledger = filled_ledger(tmp_path)

    blocked = ledger.entries(AuditQuery(outcome=OUTCOME_BLOCKED))

    assert [entry.unit for entry in blocked] == ["CP-1", "CP-2"]
    assert [entry.action for entry in ledger.entries(AuditQuery(action="chute.blocked"))] == ["chute.blocked"]
    assert ledger.outcomes() == {OUTCOME_OK: 2, OUTCOME_BLOCKED: 2}


def test_a_query_narrows_by_subject(tmp_path: Path) -> None:
    ledger = filled_ledger(tmp_path)

    assert [entry.sequence for entry in ledger.entries(AuditQuery(subject="feed"))] == [1, 3]
    assert ledger.counts() == {"feeder.start": 2, "belt.start": 1, "chute.blocked": 1}


def test_a_query_respects_a_time_window(tmp_path: Path) -> None:
    ledger = filled_ledger(tmp_path)
    query = AuditQuery(since=START + timedelta(seconds=60), until=START + timedelta(seconds=100))

    selected = ledger.entries(query)

    assert [entry.unit for entry in selected] == ["CP-2"]
    assert selected[0].moment() == START + timedelta(seconds=90)


def test_a_limit_keeps_the_newest_entries_and_a_negative_one_is_refused(tmp_path: Path) -> None:
    ledger = filled_ledger(tmp_path)

    assert [entry.sequence for entry in ledger.entries(limit=2)] == [3, 4]
    assert ledger.entries(limit=0) == []
    with pytest.raises(InvalidRequest):
        ledger.entries(limit=-1)


def test_the_last_action_of_a_line_is_easy_to_read_back(tmp_path: Path) -> None:
    ledger = filled_ledger(tmp_path)

    assert ledger.last_action("CP-2") == "chute.blocked"
    assert ledger.last_action("CP-9") == ""
    assert ledger.tail(1)[0].sequence == 4
    assert ledger.tail(0) == []


def test_from_mapping_reads_console_parameters() -> None:
    query = AuditQuery.from_mapping(
        {
            "unit": "CP-1",
            "action": "feeder.start",
            "outcome": OUTCOME_OK,
            "subject": "feed",
            "since": "2026-04-06T05:30:00Z",
            "limit": "5",
        }
    )

    assert query.unit == "CP-1"
    assert query.since == START
    assert query.limit == 5
    assert query.describe()["action"] == "feeder.start"
    assert AuditQuery.from_mapping({}).limit is None


def test_from_mapping_refuses_a_limit_and_a_timestamp_it_cannot_read() -> None:
    with pytest.raises(InvalidRequest):
        AuditQuery.from_mapping({"limit": "soon"})
    with pytest.raises(InvalidRequest):
        AuditQuery.from_mapping({"until": "yesterday"})
