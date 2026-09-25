"""The protective latch: set condition, hold, and the release that has to be explained."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from crushplant.errors import LatchActive, StateConflict
from crushplant.safety.latch import Latch
from crushplant.store.documents import DocumentStore
from tests.support import DEFAULT_UNIT, advance, bring_up, clock_of, manual_runtime


def _block_the_chute(runtime) -> object:
    """Hold the chute above its block threshold long enough to confirm a blockage."""

    line = runtime.line(DEFAULT_UNIT)
    for _ in range(3):
        line.survey_chute(84.0, runtime.clock.now(), "test")
        advance(runtime, 25.0, step=25.0)
    return line


def test_a_confirmed_blockage_sets_the_latch(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    bring_up(runtime)
    line = _block_the_chute(runtime)

    state = line.parts.latch.state()

    assert state.latched is True
    assert "chute level 84.0%" in state.reason
    assert line.parts.gate.preview()["ok"] is False


def test_a_latch_cannot_be_released_while_its_hold_runs(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    bring_up(runtime)
    line = _block_the_chute(runtime)

    with pytest.raises(LatchActive) as failure:
        line.parts.latch.release(runtime.clock.now(), "operator", "chute looks better")

    assert "hold runs until" in failure.value.reason


def test_an_operator_may_force_the_latch_off_with_a_reason(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    bring_up(runtime)
    line = _block_the_chute(runtime)

    released = line.parts.latch.release(
        runtime.clock.now(),
        "operator",
        "blockage cleared by hand",
        force=True,
    )

    assert released.latched is False
    assert released.released_reason == "blockage cleared by hand"
    assert line.parts.gate.preview()["ok"] is True


def test_clearing_the_chute_empties_the_window_and_releases_the_latch(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    bring_up(runtime)
    line = _block_the_chute(runtime)
    clock_of(runtime).advance(runtime.config.safety.latch_hold_s + 1.0)

    cleared = line.clear_chute(clock_of(runtime).now(), "test", "chute emptied", level_pct=41.0)

    assert cleared["latch"]["latched"] is False
    assert cleared["blockage"]["state"] == "clear"
    assert line.parts.latch.state().released_by == "test"


def test_the_chute_cannot_be_called_clear_while_it_is_still_loaded(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    bring_up(runtime)
    _block_the_chute(runtime)

    with pytest.raises(StateConflict) as failure:
        runtime.line(DEFAULT_UNIT).clear_chute(runtime.clock.now(), "test", "looks fine", level_pct=70.0)

    assert failure.value.details["clear_pct"] == 62.0


def test_a_latch_that_is_not_set_cannot_be_released(tmp_path: Path) -> None:
    latch = Latch("CP-9.feed", DocumentStore(tmp_path / "state"), hold_seconds=60.0)
    moment = datetime(2026, 4, 6, 5, 30, tzinfo=timezone.utc)

    with pytest.raises(LatchActive) as failure:
        latch.release(moment, "operator", "why not")

    assert "not set" in failure.value.reason


def test_evaluating_a_latch_releases_it_once_the_condition_recovers(tmp_path: Path) -> None:
    latch = Latch("CP-9.feed", DocumentStore(tmp_path / "state"), hold_seconds=30.0)
    moment = datetime(2026, 4, 6, 5, 30, tzinfo=timezone.utc)
    latch.trip("chute loaded", moment, "test")

    still_set = latch.evaluate(moment, condition_clear=False)
    holding = latch.evaluate(moment, condition_clear=True, actor="control")
    released = latch.evaluate(
        datetime(2026, 4, 6, 5, 31, tzinfo=timezone.utc),
        condition_clear=True,
        actor="control",
    )

    assert still_set.latched is True
    assert holding.latched is True
    assert released.latched is False
    assert released.released_reason == "trip condition recovered"
    assert released.trips == 1
