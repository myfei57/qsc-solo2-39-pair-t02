"""Contract three, part one: the order each stage may run in."""

from __future__ import annotations

from pathlib import Path

import pytest

from crushplant.errors import OrderingViolation, StateConflict
from crushplant.line.plan import START_STEPS, STOP_STEPS, TRIP_STEPS
from tests.support import DEFAULT_UNIT, advance, bring_up, manual_runtime


def test_the_bring_up_chain_runs_every_stage_in_the_mandated_order(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)

    assert line.parts.machine.stages() == list(START_STEPS)
    assert line.state() == "running"
    assert line.progress("start")["outstanding"] == []
    entries = runtime.ledger.for_unit(DEFAULT_UNIT)
    actions = [entry.action for entry in entries]
    assert actions.index("stage.jaw-state") < actions.index("stage.feed")


def test_feeding_before_the_screen_step_is_refused_as_out_of_order(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = runtime.line(DEFAULT_UNIT)
    moment = runtime.clock.now()
    line.begin(moment, "test")

    with pytest.raises(OrderingViolation) as failure:
        line.feed(600.0, moment, "test")

    assert failure.value.details["expected"] == "jaw-state"
    assert line.parts.feeder.state().running is False


def test_a_start_step_cannot_run_before_its_predecessor(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = runtime.line(DEFAULT_UNIT)
    moment = runtime.clock.now()
    line.begin(moment, "test")

    with pytest.raises(OrderingViolation) as failure:
        line.start_screen(moment, "test")

    assert failure.value.details["expected"] == "jaw-state"
    assert failure.value.details["step"] == "screen-start"


def test_the_same_stage_cannot_run_twice_in_one_cycle(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = runtime.line(DEFAULT_UNIT)
    moment = runtime.clock.now()
    line.begin(moment, "test")
    line.persist_jaw_state(moment, "test")

    with pytest.raises(OrderingViolation) as failure:
        line.persist_jaw_state(moment, "test")

    assert "already run" in str(failure.value)


def test_a_shutdown_step_out_of_order_is_refused(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    moment = runtime.clock.now()
    line.parts.machine.begin_stop(moment, "test")

    with pytest.raises(OrderingViolation) as failure:
        line.stop_jaw(moment, "test")

    assert failure.value.details["expected"] == "feed-stop"
    assert failure.value.details["sequence"] == "stop"


def test_the_shutdown_chain_runs_every_stage_in_the_mandated_order(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    advance(runtime, 300.0)

    result = line.stop(runtime.clock.now(), "test")

    assert result["steps"] == list(STOP_STEPS)
    assert result["state"] == "standby"
    assert line.parts.feeder.state().running is False
    assert line.parts.jaw.state().state == "stopped"
    assert line.parts.belt.state().running is False


def test_a_line_cannot_be_started_twice_without_standing_by(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    with pytest.raises(StateConflict):
        line.begin(runtime.clock.now(), "test")
    with pytest.raises(StateConflict):
        line.stop_feed(runtime.clock.now(), "test")


def test_the_emergency_order_is_not_the_normal_shutdown_order(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)

    trip = line.emergency_stop("screen bearing temperature", runtime.clock.now(), "test")

    assert trip["steps"] == list(TRIP_STEPS)
    assert trip["steps"] != list(STOP_STEPS)
    assert line.state() == "tripped"
    assert line.parts.latch.is_set() is True


def test_a_stopped_line_runs_the_whole_chain_again_in_a_new_cycle(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    advance(runtime, 240.0)
    line.stop(runtime.clock.now(), "test")

    bring_up(runtime, target_tph=520.0)

    assert line.parts.machine.cycle() == 2
    assert line.state() == "running"
    assert line.parts.feeder.state().setpoint_tph == 520.0
    assert [record.cycle for record in line.parts.machine.cycle_stages()] == [2] * len(START_STEPS)
