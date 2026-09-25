"""Contract one: staged writes, the commit watermark, replay, rollback, tombstones."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from crushplant.errors import InvalidRequest, OrderingViolation, RecordNotFound
from crushplant.store.documents import DocumentStore
from crushplant.store.journal import RecordJournal
from crushplant.store.records import RecordStream
from crushplant.store.watermark import CommitWatermark

START = datetime(2026, 4, 6, 5, 30, 0, tzinfo=timezone.utc)


def make_stream(tmp_path: Path, fsync: bool = False) -> RecordStream:
    store = DocumentStore(tmp_path / "state")
    journal = RecordJournal(tmp_path / "records" / "operations.jsonl", fsync=fsync)
    return RecordStream(journal, CommitWatermark(store, "operations"))


def later(moment: datetime, seconds: float) -> datetime:
    return moment + timedelta(seconds=seconds)


def test_a_staged_record_is_invisible_until_the_watermark_covers_it(tmp_path: Path) -> None:
    stream = make_stream(tmp_path)
    stream.stage("audit", START, unit="CP-1", payload={"action": "jaw.start"})

    assert stream.visible() == []
    assert stream.committed() == []
    assert len(stream.pending()) == 1

    stream.commit(START, "test")

    assert [record.payload["action"] for record in stream.visible()] == ["jaw.start"]
    assert stream.pending() == []


def test_the_journal_only_appends_and_never_rewrites(tmp_path: Path) -> None:
    stream = make_stream(tmp_path)
    for index in range(4):
        stream.stage("audit", START, unit="CP-1", payload={"action": f"step-{index}"})
        stream.commit(START, "test")

    assert stream.journal.lines() == 4
    assert [record.sequence for record in stream.journal.records()] == [1, 2, 3, 4]
    assert stream.journal.require(2).payload["action"] == "step-1"
    with pytest.raises(RecordNotFound):
        stream.journal.require(99)


def test_a_restart_replays_only_what_sits_above_the_watermark(tmp_path: Path) -> None:
    stream = make_stream(tmp_path)
    stream.stage("audit", START, unit="CP-1", payload={"action": "committed"})
    stream.commit(START, "test")
    stream.stage("audit", START, unit="CP-1", payload={"action": "interrupted"})

    reloaded = make_stream(tmp_path)
    assert [record.payload["action"] for record in reloaded.visible()] == ["committed"]
    assert [record.payload["action"] for record in reloaded.pending()] == ["interrupted"]

    applied: list[str] = []
    outcome = reloaded.replay(lambda record: applied.append(record.payload["action"]), START, "startup")

    assert applied == ["interrupted"]
    assert outcome.applied == 1
    assert [record.payload["action"] for record in reloaded.visible()] == ["committed", "interrupted"]


def test_a_replay_that_raises_leaves_the_watermark_where_it_was(tmp_path: Path) -> None:
    stream = make_stream(tmp_path)
    stream.stage("audit", START, unit="CP-1", payload={"action": "broken"})

    def refuse(record: object) -> None:
        raise RuntimeError("projection failed")

    with pytest.raises(RuntimeError):
        stream.replay(refuse, START, "startup")

    assert stream.committed() == []
    assert len(stream.pending()) == 1


def test_a_tombstone_hides_a_committed_record_without_erasing_it(tmp_path: Path) -> None:
    stream = make_stream(tmp_path)
    record = stream.stage("audit", START, unit="CP-1", payload={"action": "mistake"})
    stream.commit(START, "test")

    marker = stream.tombstone(record.sequence, START, "operator", "entered against the wrong line")

    assert marker.kind == "void"
    assert marker.ref == record.sequence
    assert stream.visible() == []
    assert len(stream.committed()) == 2
    assert stream.voided_sequences() == {record.sequence: "entered against the wrong line"}
    assert stream.journal.lines() == 2


def test_a_tombstone_needs_a_reason_and_a_committed_target(tmp_path: Path) -> None:
    stream = make_stream(tmp_path)
    staged = stream.stage("audit", START, unit="CP-1", payload={"action": "pending"})
    with pytest.raises(OrderingViolation):
        stream.tombstone(staged.sequence, START, "operator", "not committed yet")

    stream.commit(START, "test")
    with pytest.raises(InvalidRequest):
        stream.tombstone(staged.sequence, START, "operator", "   ")


def test_a_rollback_makes_the_later_records_invisible_again(tmp_path: Path) -> None:
    stream = make_stream(tmp_path)
    first = stream.stage("audit", START, unit="CP-1", payload={"action": "one"})
    stream.commit(START, "test")
    stream.stage("audit", later(START, 5), unit="CP-1", payload={"action": "two"})
    stream.commit(later(START, 5), "test")
    assert len(stream.visible()) == 2

    watermark = stream.rollback_to(first.sequence, later(START, 9), "operator", "bad cycle")

    assert watermark.sequence == first.sequence
    assert [record.payload["action"] for record in stream.visible()] == ["one"]
    assert len(stream.pending()) == 1
    assert stream.journal.lines() == 2


def test_the_watermark_refuses_to_move_backwards_without_a_rollback(tmp_path: Path) -> None:
    stream = make_stream(tmp_path)
    stream.stage("audit", START, unit="CP-1", payload={"action": "one"})
    stream.commit(START, "test")

    with pytest.raises(InvalidRequest):
        stream.commit_through(0, START, "test")
    with pytest.raises(OrderingViolation):
        stream.commit_through(9, START, "test")


def test_the_summary_reports_every_count_a_reader_can_see(tmp_path: Path) -> None:
    stream = make_stream(tmp_path)
    kept = stream.stage("audit", START, unit="CP-1", payload={"action": "one"})
    stream.stage("audit", START, unit="CP-1", payload={"action": "two"})
    stream.commit(START, "test")
    stream.tombstone(kept.sequence, START, "operator", "voided")
    stream.stage("audit", START, unit="CP-1", payload={"action": "staged"})

    summary = stream.summary()

    assert summary["journal_lines"] == 4
    assert summary["watermark"]["sequence"] == 3
    assert summary["visible"] == 1
    assert summary["pending"] == 1
    assert summary["voided"] == 1
    assert stream.watermark_history(2)[-1]["event"] == "commit"
