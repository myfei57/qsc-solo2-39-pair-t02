"""The durability rule: a value that gates a step has to be on the journal."""

from __future__ import annotations

from pathlib import Path

import pytest

from crushplant.errors import DurabilityError, OrderingViolation
from crushplant.jaw.state import JawStateStore
from tests.support import DEFAULT_UNIT, calibrate, confirm_feed, manual_runtime, ready_to_feed, reload_runtime


def test_a_drafted_crusher_state_is_not_durable_until_it_is_persisted(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    store = JawStateStore(DEFAULT_UNIT, runtime.store, runtime.stream)
    moment = runtime.clock.now()
    store.draft(
        state="running",
        gap_mm=110.0,
        feed_mm=750.0,
        product_mm=180.0,
        reduction=4.167,
        moment=moment,
    )

    assert store.current().revision == 1
    assert store.committed() is False
    with pytest.raises(DurabilityError) as failure:
        store.require_committed("feed")

    assert failure.value.details["stage"] == "jaw-state"
    assert failure.value.details["committed_sequence"] == 0

    store.persist(moment, "test")

    assert store.committed() is True
    assert store.current().committed_sequence == 1


def test_a_rolled_back_watermark_makes_the_crusher_state_undurable(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = ready_to_feed(runtime)
    sequence = line.parts.jaw.state().committed_sequence
    runtime.stream.rollback_to(sequence - 1, runtime.clock.now(), "operator", "shift rolled back")

    with pytest.raises(DurabilityError) as failure:
        line.feed(600.0, runtime.clock.now(), "test")

    assert failure.value.details["stage"] == "jaw-state"
    assert line.parts.feeder.state().running is False


def test_a_tombstoned_crusher_state_record_stops_gating_the_feed(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = ready_to_feed(runtime)
    sequence = line.parts.jaw.state().committed_sequence
    runtime.stream.tombstone(sequence, runtime.clock.now(), "operator", "written against the spare line")

    assert line.parts.jaw.committed() is False
    with pytest.raises(DurabilityError):
        line.feed(600.0, runtime.clock.now(), "test")


def test_writing_the_crusher_state_again_restores_the_gate(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = runtime.line(DEFAULT_UNIT)
    calibrate(runtime)
    confirm_feed(runtime)
    moment = runtime.clock.now()
    line.begin(moment, "test")
    line.persist_jaw_state(moment, "test")
    sequence = line.parts.jaw.state().committed_sequence
    runtime.stream.rollback_to(sequence - 1, moment, "operator", "shift rolled back")
    assert line.parts.jaw.committed() is False

    line.parts.jaw.republish_state(moment, "test")

    assert line.parts.jaw.committed() is True
    assert line.parts.jaw.state().committed_sequence > sequence


def test_the_crusher_state_survives_a_restart(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = ready_to_feed(runtime)
    assert line.parts.jaw.committed() is True

    reloaded = reload_runtime(runtime)

    assert reloaded.line(DEFAULT_UNIT).parts.jaw.committed() is True
    assert reloaded.line(DEFAULT_UNIT).parts.jaw.state().revision == line.parts.jaw.state().revision


def test_ordering_is_checked_before_durability(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = runtime.line(DEFAULT_UNIT)
    moment = runtime.clock.now()
    line.begin(moment, "test")

    with pytest.raises(OrderingViolation) as failure:
        line.feed(600.0, moment, "test")

    assert failure.value.details["expected"] == "jaw-state"
    assert line.parts.jaw.state().revision == 0
    assert line.parts.machine.state() == "starting"
