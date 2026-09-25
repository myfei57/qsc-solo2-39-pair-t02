"""Contract two: generations, confirmation slips and expiring baselines."""

from __future__ import annotations

from pathlib import Path

import pytest

from crushplant.errors import InvalidRequest, NameConflict, StaleRecord
from crushplant.store.documents import DocumentStore
from crushplant.store.generations import BaselineStore, ConfirmationRegister, GenerationRegistry
from tests.support import DEFAULT_UNIT, START, bring_up, calibrate, clock_of, manual_runtime, ready_to_feed


def test_a_generation_change_needs_a_reason_and_moves_the_counter(tmp_path: Path) -> None:
    registry = GenerationRegistry(DocumentStore(tmp_path / "state"))
    assert registry.generation() == 1
    with pytest.raises(InvalidRequest):
        registry.bump(START, "engineer", "   ")

    state = registry.bump(START, "engineer", "liner changed")

    assert state.generation == 2
    assert registry.history()[-1]["generation"] == 1


def test_a_feed_confirmation_is_required_before_the_line_may_be_fed(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = runtime.line(DEFAULT_UNIT)
    calibrate(runtime)
    line.sample_level(64.0, runtime.clock.now(), "test")
    moment = runtime.clock.now()
    line.begin(moment, "test")
    line.persist_jaw_state(moment, "test")
    line.ready_magnet(moment, "test")
    line.start_belt(moment, "test")
    line.start_screen(moment, "test")
    line.start_cone(moment, "test")

    with pytest.raises(StaleRecord) as failure:
        line.feed(600.0, moment, "test")

    assert "no confirmation was issued" in failure.value.reason


def test_a_confirmation_issued_for_one_generation_is_refused_after_a_bump(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = ready_to_feed(runtime)

    runtime.set_generation("setpoint curve re-tuned", "engineer")
    assert line.parts.gate.preview()["ok"] is True

    with pytest.raises(StaleRecord) as failure:
        line.feed(600.0, runtime.clock.now(), "test")

    assert "generation" in failure.value.reason
    assert runtime.line(DEFAULT_UNIT).parts.feeder.state().running is False


def test_an_expired_confirmation_is_refused(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = ready_to_feed(runtime)
    clock_of(runtime).advance(runtime.config.confirmation_ttl_s + 1.0)

    with pytest.raises(StaleRecord) as failure:
        line.feed(600.0, clock_of(runtime).now(), "test")

    assert "expired" in failure.value.reason


def test_a_confirmation_is_single_use(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    register = runtime.confirmations
    moment = runtime.clock.now()
    slip = register.issue(f"{DEFAULT_UNIT}.feed", runtime.generations.generation(), 900.0, moment, "test")

    register.redeem(slip.slip_id, moment, "test")
    with pytest.raises(StaleRecord) as failure:
        register.require(f"{DEFAULT_UNIT}.feed", runtime.generations.generation(), moment)

    assert "already redeemed" in failure.value.reason


def test_bumping_the_generation_retires_every_live_confirmation(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    ready_to_feed(runtime)
    assert runtime.confirmations.pending(runtime.clock.now())

    result = runtime.set_generation("crusher gap re-set", "engineer")

    assert result["invalidated"] == [f"{DEFAULT_UNIT}.feed-1-1"]
    assert runtime.confirmations.pending(runtime.clock.now()) == []


def test_the_same_slip_identifier_is_refused_twice(tmp_path: Path) -> None:
    register = ConfirmationRegister(DocumentStore(tmp_path / "state"))
    register.issue("CP-1.feed", 1, 600.0, START, "test", slip_id="fixed")
    with pytest.raises(NameConflict):
        register.issue("CP-1.feed", 1, 600.0, START, "test", slip_id="fixed")


def test_a_belt_scale_reading_is_refused_once_the_calibration_expires(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = runtime.line(DEFAULT_UNIT)
    calibrate(runtime)
    clock_of(runtime).advance(runtime.config.baseline_ttl_s + 1.0)

    with pytest.raises(StaleRecord) as failure:
        line.parts.scale.read(320.0, clock_of(runtime).now(), "test")

    assert "expired" in failure.value.reason


def test_a_belt_scale_reading_is_refused_after_the_generation_moves(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    calibrate(runtime)
    runtime.set_generation("belt re-tensioned", "engineer")

    with pytest.raises(StaleRecord) as failure:
        runtime.line(DEFAULT_UNIT).parts.scale.read(320.0, runtime.clock.now(), "test")

    assert "generation" in failure.value.reason


def test_a_cone_current_line_is_refused_after_the_generation_moves(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    calibrate(runtime)
    runtime.set_generation("liner changed", "engineer")

    with pytest.raises(StaleRecord):
        runtime.line(DEFAULT_UNIT).parts.cone.start(runtime.clock.now(), "test")


def test_a_baseline_needs_a_positive_lifetime_and_a_sample(tmp_path: Path) -> None:
    store = BaselineStore(DocumentStore(tmp_path / "state"))
    with pytest.raises(InvalidRequest):
        store.record("CP-1.belt-scale", 0.1, 1, START, 0.0)
    with pytest.raises(InvalidRequest):
        store.record("CP-1.belt-scale", 0.1, 1, START, 60.0, samples=0)


def test_a_calibrated_line_keeps_reading_until_its_own_deadline(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    calibrate(runtime)
    clock_of(runtime).advance(runtime.config.baseline_ttl_s - 10.0)

    reading = runtime.line(DEFAULT_UNIT).parts.scale.read(520.0, clock_of(runtime).now(), "test")

    assert reading.kg_per_m == 74.0
    assert reading.tonnes_per_hour > 0
    assert reading.generation == runtime.generations.generation()


def test_a_running_line_still_reports_its_verdicts_after_a_generation_bump(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    line.parts.jaw.sample_amps(200.0, runtime.clock.now(), "test")

    runtime.set_generation("product target moved", "engineer")

    assert runtime.verdicts.current()[f"{DEFAULT_UNIT}.jaw"].state == "ok"
    assert len(runtime.verdicts.history()) >= 1
