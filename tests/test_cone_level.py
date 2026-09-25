"""The secondary crusher's current line, the level instrument and the bin."""

from __future__ import annotations

from pathlib import Path

import pytest

from crushplant.cone.calibrate import CurrentPoint, fit_current
from crushplant.errors import InvalidRequest, StaleRecord, StateConflict
from tests.support import (
    CONE_POINTS,
    DEFAULT_UNIT,
    advance,
    bring_up,
    clock_of,
    manual_runtime,
)


def test_a_current_fit_reports_the_idle_draw_and_the_gain() -> None:
    idle, gain = fit_current(CONE_POINTS)

    assert (idle, gain) == (67.2, 0.24)
    assert fit_current((CurrentPoint(0.0, 40.0), CurrentPoint(500.0, 160.0))) == (40.0, 0.24)


def test_a_current_fit_needs_two_points_and_a_positive_gain() -> None:
    with pytest.raises(InvalidRequest):
        fit_current((CONE_POINTS[0],))
    with pytest.raises(InvalidRequest):
        fit_current((CurrentPoint(120.0, 96.0), CurrentPoint(120.0, 168.0)))
    with pytest.raises(InvalidRequest) as failure:
        fit_current((CurrentPoint(100.0, 200.0), CurrentPoint(300.0, 100.0)))

    assert "gain must be positive" in str(failure.value)


def test_the_cone_needs_a_fresh_calibration_before_it_starts(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = runtime.line(DEFAULT_UNIT)

    with pytest.raises(StaleRecord) as failure:
        line.parts.cone.start(runtime.clock.now(), "test")

    assert "no baseline was captured" in failure.value.reason
    assert line.parts.cone.running() is False


def test_the_cone_cannot_be_started_twice(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)

    with pytest.raises(StateConflict):
        line.parts.cone.start(runtime.clock.now(), "test")

    stopped = line.parts.cone.stop(runtime.clock.now(), "test")

    assert stopped.running is False
    with pytest.raises(StateConflict):
        line.parts.cone.stop(runtime.clock.now(), "test")


def test_a_stall_stops_the_cone_and_is_written_down(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    ceiling = line.spec.stall_amps()

    calm = line.sample_amps("cone", 180.0, runtime.clock.now(), "test")
    stalled = line.sample_amps("cone", ceiling + 8.0, runtime.clock.now(), "test")

    assert calm["cone"]["stall"]["state"] == "ok"
    assert stalled["cone"]["stall"]["state"] == "high"
    assert stalled["cone"]["state"]["stalled"] is True
    assert stalled["cone"]["state"]["running"] is False
    assert runtime.ledger.entries(action="cone.stall", outcome="tripped")
    recorded = runtime.verdicts.current()[f"{DEFAULT_UNIT}.cone"]
    assert recorded.name == "cone.stall"
    assert recorded.detail["stall_amps"] == ceiling


def test_the_expected_draw_follows_the_calibrated_line(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)

    expected = line.parts.cone.expected_amps(420.0, runtime.clock.now())
    summary = line.parts.calibration.summary()

    assert expected == 168.0
    assert summary["gain_amps_per_tph"] == 0.24
    assert summary["idle_amps"] == 67.2
    with pytest.raises(InvalidRequest):
        line.sample_amps("cone", -1.0, runtime.clock.now(), "test")
    with pytest.raises(InvalidRequest):
        line.sample_amps("screen", 100.0, runtime.clock.now(), "test")


def test_a_level_reading_that_is_too_old_is_refused(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = runtime.line(DEFAULT_UNIT)
    line.sample_level(64.0, runtime.clock.now(), "test")
    advance(runtime, runtime.config.stock.level_max_age_s + 5.0)

    with pytest.raises(StaleRecord) as failure:
        line.parts.gauge.require_fresh(runtime.clock.now(), "feed plan")

    assert "budget is" in failure.value.reason
    assert line.parts.gauge.state(runtime.clock.now())["stale"] is True


def test_the_feed_plan_chases_the_level_inside_the_band(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = runtime.line(DEFAULT_UNIT)
    line.sample_level(64.0, runtime.clock.now(), "test")

    high = line.plan_feed(runtime.clock.now(), "test")
    assert high["target_level_pct"] == 55.0
    assert high["deviation_pct"] == 9.0
    assert high["setpoint_tph"] == 701.0

    line.sample_level(2.0, runtime.clock.now(), "test")
    empty = line.plan_feed(runtime.clock.now(), "test")

    assert empty["setpoint_tph"] == 180.0
    assert line.parts.planner.last_setpoint() == 180.0
    assert line.parts.planner.check(runtime.clock.now())["ok"] is True


def test_a_feed_plan_cannot_be_made_from_a_stale_level(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = runtime.line(DEFAULT_UNIT)
    clock_of(runtime).advance(runtime.config.stock.level_max_age_s + 1.0)

    assert line.parts.planner.check(clock_of(runtime).now())["ok"] is False
    with pytest.raises(StaleRecord):
        line.plan_feed(clock_of(runtime).now(), "test")


def test_the_bin_reports_low_ok_and_high_against_its_band(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = runtime.line(DEFAULT_UNIT)

    loaded = line.load_bin(64.0, runtime.clock.now(), "test")
    assert loaded["state"] == "ok"
    assert loaded["volume_m3"] == 204.8
    assert loaded["tonnes"] == 378.88
    assert loaded["capacity_t"] == 592.0

    low = line.draw_bin(360.0, runtime.clock.now(), "test")
    assert low["state"] == "low"
    assert line.parts.bin.check().ok is False

    high = line.fill_bin(500.0, runtime.clock.now(), "test")
    assert high["state"] == "high"
    with pytest.raises(InvalidRequest):
        line.draw_bin(10_000.0, runtime.clock.now(), "test")
