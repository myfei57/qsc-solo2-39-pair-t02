"""The feeder drive, its band and the belt scale that measures what it moved."""

from __future__ import annotations

from pathlib import Path

import pytest

from crushplant.belt.scale import ScalePoint, fit_scale
from crushplant.errors import InvalidRequest, LimitExceeded, StateConflict
from crushplant.verdict.threshold import STATE_HIGH, STATE_OK
from tests.support import (
    DEFAULT_UNIT,
    SCALE_POINTS,
    advance,
    bring_up,
    calibrate,
    confirm_feed,
    manual_runtime,
    ready_to_feed,
)


def test_the_feeder_refuses_a_setpoint_outside_its_band(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = ready_to_feed(runtime)

    with pytest.raises(LimitExceeded) as failure:
        line.feed(900.0, runtime.clock.now(), "test")

    assert failure.value.details["high"] == 750.0
    assert line.parts.feeder.state().running is False


def test_the_feeder_refuses_a_setpoint_below_its_band(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = ready_to_feed(runtime)

    with pytest.raises(LimitExceeded):
        line.feed(40.0, runtime.clock.now(), "test")


def test_the_feeder_cannot_be_started_twice(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)

    with pytest.raises(StateConflict):
        line.parts.feeder.start(600.0, runtime.clock.now(), "test")

    stopped = line.parts.feeder.stop(runtime.clock.now(), "test")

    assert stopped.running is False
    with pytest.raises(StateConflict):
        line.parts.feeder.stop(runtime.clock.now(), "test")


def test_a_speed_change_needs_a_running_feeder(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = runtime.line(DEFAULT_UNIT)
    calibrate(runtime)
    confirm_feed(runtime)

    with pytest.raises(StateConflict):
        line.parts.feeder.set_speed(500.0, runtime.clock.now(), "test")

    ready_to_feed(runtime)
    line.feed(600.0, runtime.clock.now(), "test")
    changed = line.parts.feeder.set_speed(520.0, runtime.clock.now(), "test")

    assert changed.setpoint_tph == 520.0
    assert changed.cycles == 1


def test_a_measured_rate_is_judged_against_the_band(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)

    inside = line.measure_feed(612.0, runtime.clock.now(), "test")
    outside = line.measure_feed(820.0, runtime.clock.now(), "test")

    assert inside["verdict"]["state"] == STATE_OK
    assert outside["verdict"]["state"] == STATE_HIGH
    assert outside["verdict"]["margin"] == -70.0
    with pytest.raises(InvalidRequest):
        line.measure_feed(-1.0, runtime.clock.now(), "test")


def test_the_feeder_reports_the_average_rate_it_has_run_at(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    line.measure_feed(600.0, runtime.clock.now(), "test")
    advance(runtime, 300.0)

    average = line.parts.feeder.average_rate(runtime.clock.now())

    assert average == pytest.approx(600.0, abs=0.01)
    assert line.parts.feeder.total_tonnes() == pytest.approx(50.0, abs=0.05)


def test_the_belt_scale_turns_a_raw_signal_into_throughput(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = runtime.line(DEFAULT_UNIT)
    calibrate(runtime)

    reading = line.parts.scale.read(520.0, runtime.clock.now(), "test")

    assert reading.kg_per_m == 74.0
    assert reading.tonnes_per_hour == 479.52
    assert reading.zero_kg_per_m == pytest.approx(1.2, abs=1e-6)
    assert line.parts.scale.mapping()["generation"] == 1


def test_a_scale_that_was_never_calibrated_refuses_to_weigh(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    with pytest.raises(Exception) as failure:
        runtime.line(DEFAULT_UNIT).parts.scale.read(520.0, runtime.clock.now(), "test")

    assert "no baseline was captured" in str(failure.value)
    assert runtime.verdicts.current()[f"{DEFAULT_UNIT}.belt-scale"].state == "stale"


def test_a_scale_fit_needs_two_points_with_a_spread() -> None:
    zero, span = fit_scale(SCALE_POINTS)
    assert (zero, span) == (1.2, 0.14)
    with pytest.raises(InvalidRequest):
        fit_scale((SCALE_POINTS[0],))
    with pytest.raises(InvalidRequest):
        fit_scale((ScalePoint(120.0, 18.0), ScalePoint(120.0, 74.0)))


def test_the_belt_accumulates_the_tonnage_it_carried(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    reading = line.parts.scale.read(520.0, runtime.clock.now(), "test")

    moved = line.parts.belt.carry(reading, 60.0, runtime.clock.now(), "test")

    assert moved.tonnes == pytest.approx(479.52 / 60.0, abs=0.01)
    assert moved.load_kg_per_m == 74.0
    assert line.parts.belt.state().tonnes == moved.tonnes


def test_the_belt_targets_the_load_that_carries_a_throughput(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = runtime.line(DEFAULT_UNIT)

    assert line.parts.belt.target_load(479.52) == 74.0
