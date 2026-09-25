"""Snapshots: capture, freshness, retirement and the restore they allow."""

from __future__ import annotations

from pathlib import Path

import pytest

from crushplant.errors import NameConflict, RecordNotFound, StaleRecord
from crushplant.store.documents import DocumentStore
from crushplant.store.snapshot import SnapshotStore
from tests.support import START, bring_up, clock_of, manual_runtime


def test_a_captured_snapshot_carries_its_generation_and_deadline(tmp_path: Path) -> None:
    store = SnapshotStore(DocumentStore(tmp_path / "state"))
    snapshot = store.capture("shift-a", START, generation=3, sequence=12, records=9, ttl_seconds=600.0)

    assert snapshot.generation == 3
    assert snapshot.sequence == 12
    assert [item.label for item in store.list()] == ["shift-a"]
    assert store.require_fresh("shift-a", START, 3).records == 9


def test_a_snapshot_label_can_only_be_used_once(tmp_path: Path) -> None:
    store = SnapshotStore(DocumentStore(tmp_path / "state"))
    store.capture("shift-a", START, generation=1, sequence=4, records=4, ttl_seconds=600.0)
    with pytest.raises(NameConflict):
        store.capture("shift-a", START, generation=1, sequence=5, records=5, ttl_seconds=600.0)


def test_an_expired_snapshot_cannot_be_restored(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    bring_up(runtime)
    runtime.snapshot("shift-a", ttl_seconds=300.0)
    clock_of(runtime).advance(301.0)

    with pytest.raises(StaleRecord) as failure:
        runtime.restore_snapshot("shift-a", "test", "rolling back")

    assert "expired" in failure.value.reason


def test_a_snapshot_from_an_older_generation_cannot_be_restored(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    bring_up(runtime)
    runtime.snapshot("shift-a")
    runtime.set_generation("screen deck replaced", "engineer")

    with pytest.raises(StaleRecord) as failure:
        runtime.restore_snapshot("shift-a", "test", "rolling back")

    assert "generation" in failure.value.reason


def test_retiring_a_snapshot_takes_it_out_of_the_live_set(tmp_path: Path) -> None:
    store = SnapshotStore(DocumentStore(tmp_path / "state"))
    store.capture("shift-a", START, generation=1, sequence=4, records=4, ttl_seconds=600.0)
    retired = store.retire("shift-a", START)

    assert retired.label == "shift-a"
    assert store.list() == []
    assert store.history()[-1]["label"] == "shift-a"
    with pytest.raises(RecordNotFound):
        store.get("shift-a")


def test_a_restore_pulls_the_watermark_back_to_the_snapshot(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    snapshot = runtime.snapshot("shift-a")
    line.parts.jaw.sample_amps(210.0, runtime.clock.now(), "test")
    assert runtime.stream.summary()["watermark"]["sequence"] > snapshot["sequence"]

    restored = runtime.restore_snapshot("shift-a", "operator", "bad reading")

    assert restored["watermark"]["sequence"] == snapshot["sequence"]
    assert restored["history"][-1]["event"] == "rollback"
    assert restored["history"][-1]["reason"] == "bad reading"
    assert runtime.stream.summary()["pending"] >= 1
