"""Contract three, part two: the preconditions one component puts on another."""

from __future__ import annotations

from pathlib import Path

import pytest

from crushplant.errors import InterlockBlocked
from crushplant.safety.interlock import Check, guard, summarise, unmet
from tests.support import DEFAULT_UNIT, advance, bring_up, calibrate, confirm_feed, manual_runtime


def test_the_belt_cannot_start_while_the_magnet_alarm_stands(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = runtime.line(DEFAULT_UNIT)
    calibrate(runtime)
    confirm_feed(runtime)
    moment = runtime.clock.now()
    line.begin(moment, "test")
    line.persist_jaw_state(moment, "test")
    line.ready_magnet(moment, "test")
    line.trip_magnet("overbelt detector fault", moment, "test")

    with pytest.raises(InterlockBlocked) as failure:
        line.start_belt(moment, "test")

    assert any("overbelt detector fault" in item for item in failure.value.unmet)
    assert line.parts.belt.state().running is False


def test_a_refused_step_is_written_down_as_blocked(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = runtime.line(DEFAULT_UNIT)
    moment = runtime.clock.now()
    line.begin(moment, "test")
    line.persist_jaw_state(moment, "test")
    line.ready_magnet(moment, "test")
    line.trip_magnet("overbelt detector fault", moment, "test")
    with pytest.raises(InterlockBlocked):
        line.start_belt(moment, "test")

    blocked = runtime.ledger.entries(action="belt.start", outcome="blocked")

    assert len(blocked) == 1
    assert any("overbelt detector fault" in item for item in blocked[0].detail["unmet"])


def test_the_feed_gate_reports_a_stalled_secondary_crusher(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    ceiling = line.spec.stall_amps()

    line.sample_amps("cone", ceiling + 10.0, runtime.clock.now(), "test")

    assert line.parts.cone.state().running is False
    assert line.parts.cone.state().stalled is True
    preview = line.parts.gate.preview()
    assert preview["ok"] is False
    assert any("secondary crusher" in item for item in preview["unmet"])
    with pytest.raises(InterlockBlocked):
        line.parts.gate.require(runtime.clock.now(), "test")


def test_the_feed_gate_reports_an_empty_bin(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    line.draw_bin(line.parts.bin.state().tonnes - 10.0, runtime.clock.now(), "test")

    preview = line.parts.gate.preview()

    assert line.parts.bin.state().state == "low"
    assert preview["ok"] is False
    assert any(item.startswith("bin low") for item in preview["unmet"])


def test_the_gate_reports_every_unmet_precondition_at_once(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    moment = runtime.clock.now()
    line.trip_magnet("detector fault", moment, "test")
    line.draw_bin(line.parts.bin.state().tonnes - 5.0, moment, "test")

    with pytest.raises(InterlockBlocked) as failure:
        line.parts.gate.require(moment, "test")

    assert len(failure.value.unmet) == 2
    assert failure.value.details["action"] == "feed"


def test_guard_reports_every_check_that_does_not_hold() -> None:
    checks = [
        Check("jaw-state", True, "crusher state revision 4 on disk"),
        Check("screen", False, "screen running=False"),
        Check("latch", False, "latched since 2026-04-06T05:30:00Z: chute loaded"),
    ]

    assert unmet(checks) == ["screen running=False", "latched since 2026-04-06T05:30:00Z: chute loaded"]
    assert summarise(checks)["ok"] is False
    with pytest.raises(InterlockBlocked) as failure:
        guard("feed", checks)
    assert len(failure.value.unmet) == 2


def test_a_stalled_crusher_does_not_take_the_running_line_down_by_itself(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    advance(runtime, 60.0)

    line.sample_amps("cone", line.spec.stall_amps() + 5.0, runtime.clock.now(), "test")

    assert line.parts.feeder.state().running is True
    assert line.state() == "running"
    assert line.parts.latch.is_set() is False
