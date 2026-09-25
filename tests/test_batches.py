"""Production batches: one code per batch, and the registry that keeps it unique."""

from __future__ import annotations

from pathlib import Path

import pytest

from crushplant.audit.batches import BATCH_CLOSED, BATCH_OPEN, BatchRegistry
from crushplant.audit.ledger import AuditLedger
from crushplant.errors import InvalidRequest, NameConflict, RecordNotFound, StateConflict
from crushplant.ns.ids import IdIssuer
from crushplant.store.documents import DocumentStore
from crushplant.store.journal import RecordJournal
from crushplant.store.records import RecordStream
from crushplant.store.watermark import CommitWatermark
from tests.support import DEFAULT_UNIT, START, manual_runtime


def make_registry(tmp_path: Path) -> tuple[BatchRegistry, AuditLedger]:
    store = DocumentStore(tmp_path / "state")
    stream = RecordStream(
        RecordJournal(tmp_path / "records" / "operations.jsonl", fsync=False),
        CommitWatermark(store, "operations"),
    )
    ledger = AuditLedger(stream)
    return BatchRegistry(store, ledger), ledger


def test_a_batch_code_is_only_ever_used_once(tmp_path: Path) -> None:
    registry, _ = make_registry(tmp_path)
    registry.open("northpit-btch-20260406-0001", DEFAULT_UNIT, "production", START, "test")

    with pytest.raises(NameConflict) as failure:
        registry.open("northpit-btch-20260406-0001", "CP-2", "production", START, "test")

    assert failure.value.details["existing_unit"] == DEFAULT_UNIT
    assert registry.codes() == ["NORTHPIT-BTCH-20260406-0001"]


def test_a_batch_is_opened_and_closed_with_its_tonnage(tmp_path: Path) -> None:
    registry, _ = make_registry(tmp_path)
    opened = registry.open("B-1", DEFAULT_UNIT, "production", START, "test")
    closed = registry.close("B-1", START, "test", tonnes=812.5)

    assert opened.state == BATCH_OPEN
    assert closed.state == BATCH_CLOSED
    assert closed.tonnes == 812.5
    assert closed.closed_by == "test"
    assert registry.get("B-1").is_open() is False


def test_a_closed_batch_cannot_be_closed_again(tmp_path: Path) -> None:
    registry, _ = make_registry(tmp_path)
    registry.open("B-1", DEFAULT_UNIT, "production", START, "test")
    registry.close("B-1", START, "test")

    with pytest.raises(StateConflict):
        registry.close("B-1", START, "test")
    with pytest.raises(RecordNotFound):
        registry.get("B-9")


def test_a_batch_needs_a_code_and_a_kind(tmp_path: Path) -> None:
    registry, _ = make_registry(tmp_path)
    with pytest.raises(InvalidRequest):
        registry.open("   ", DEFAULT_UNIT, "production", START, "test")
    with pytest.raises(InvalidRequest):
        registry.open("B-2", DEFAULT_UNIT, "  ", START, "test")


def test_only_open_batches_are_listed_and_counted(tmp_path: Path) -> None:
    registry, _ = make_registry(tmp_path)
    registry.open("B-1", DEFAULT_UNIT, "production", START, "test")
    registry.open("B-2", "CP-2", "production", START, "test")
    registry.close("B-2", START, "test", tonnes=100.0)

    assert [record.code for record in registry.open_batches()] == ["B-1"]
    assert [record.code for record in registry.open_batches(DEFAULT_UNIT)] == ["B-1"]
    summary = registry.summary()
    assert summary["count"] == 2
    assert summary["open"] == 1
    assert summary["open_codes"] == ["B-1"]
    assert summary["unsettled"] == ["B-2"]
    assert summary["settled"] == 0
    assert summary["codes"] == ["B-1", "B-2"]


def test_opening_and_closing_a_batch_is_written_to_the_ledger(tmp_path: Path) -> None:
    registry, ledger = make_registry(tmp_path)
    registry.open("B-1", DEFAULT_UNIT, "production", START, "test", notes="shift A")
    registry.close("B-1", START, "test", tonnes=250.0)

    entries = [entry.action for entry in ledger.for_unit(DEFAULT_UNIT)]

    assert entries == ["batch.open", "batch.close"]
    assert ledger.entries(action="batch.close")[0].detail["tonnes"] == 250.0


def test_the_issuer_hands_out_sequential_codes_and_refuses_other_kinds() -> None:
    issuer = IdIssuer("north pit")
    first = issuer.issue("batch", START)
    second = issuer.issue("batch", START)
    calibration = issuer.issue("calibration", START)

    assert first == "NORTHPIT-BTCH-20260406-0001"
    assert second.endswith("0002")
    assert calibration == "NORTHPIT-CALB-20260406-0003"
    assert issuer.issued("calibration") == [calibration]
    assert issuer.counts() == {"BTCH": 2, "CALB": 1}
    with pytest.raises(InvalidRequest):
        issuer.issue("isolation", START)


def test_a_code_from_the_issuer_is_accepted_by_the_registry(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    moment = runtime.clock.now()
    code = runtime.issuer.issue("batch", moment)
    opened = runtime.batches.open(code, DEFAULT_UNIT, "production", moment, "test")

    assert opened.code == code
    assert runtime.issuer.register("NORTHPIT-CALB-20260406-0009") == "NORTHPIT-CALB-20260406-0009"
    with pytest.raises(NameConflict):
        runtime.issuer.register(code)
    with pytest.raises(InvalidRequest):
        runtime.issuer.register("OTHER-CALB-20260406-0009")
