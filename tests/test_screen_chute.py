"""The screen deck, the product check and the chute blockage detector."""

from __future__ import annotations

from pathlib import Path

import pytest

from crushplant.errors import InvalidRequest, StateConflict
from crushplant.verdict.threshold import STATE_HIGH, STATE_LOW, STATE_OK
from tests.support import DEFAULT_UNIT, bring_up, manual_runtime, ready_to_feed


def test_the_screen_refuses_a_throughput_reading_while_it_is_stopped(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = runtime.line(DEFAULT_UNIT)

    with pytest.raises(StateConflict):
        line.parts.screen.throughput(400.0, runtime.clock.now(), "test")
    with pytest.raises(StateConflict):
        line.parts.screen.stop(runtime.clock.now(), "test")


def test_deck_duty_expresses_the_share_of_the_rating_it_uses(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = ready_to_feed(runtime)

    half = line.parts.screen.throughput(350.0, runtime.clock.now(), "test")
    over = line.parts.screen.throughput(805.0, runtime.clock.now(), "test")

    assert half["load"]["utilisation_pct"] == 50.0
    assert half["verdict"]["state"] == STATE_OK
    assert over["verdict"]["state"] == STATE_HIGH
    assert line.parts.decks.state().utilisation_pct == 115.0
    assert line.parts.decks.capacity_tph == 700.0
    with pytest.raises(InvalidRequest):
        line.parts.decks.duty(-1.0, runtime.clock.now(), "test")


def test_a_product_sample_is_graded_on_both_size_limits(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)

    graded = line.sample_product(228.0, 250.0, runtime.clock.now(), "test")

    assert graded["undersize"]["state"] == STATE_OK
    assert graded["oversize"]["state"] == STATE_OK
    assert runtime.verdicts.current()[f"{DEFAULT_UNIT}.product"].name == "screen.oversize"
    with pytest.raises(InvalidRequest):
        line.sample_product(260.0, 250.0, runtime.clock.now(), "test")


def test_a_chute_survey_fills_before_it_confirms_a_blockage(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)

    first = line.survey_chute(84.0, runtime.clock.now(), "test")

    assert first["state"] == "filling"
    assert first["blocked"] is False
    assert first["margin_pct"] == -6.0
    assert line.parts.latch.is_set() is False


def test_a_sustained_chute_level_trips_the_feed_latch(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    from tests.support import advance

    for _ in range(3):
        line.survey_chute(84.0, runtime.clock.now(), "test")
        advance(runtime, 25.0, step=25.0)

    assert line.parts.chute.is_blocked(runtime.clock.now()) is True
    assert line.parts.latch.is_set() is True
    assert line.parts.gate.preview()["ok"] is False
    assert runtime.verdicts.current()[f"{DEFAULT_UNIT}.chute"].state == "blocked"


def test_a_chute_that_has_not_been_loaded_never_blocks(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)

    settled = line.survey_chute(41.0, runtime.clock.now(), "test")

    assert settled["state"] == "clear"
    assert settled["samples"] == 0
    assert line.parts.latch.is_set() is False
    with pytest.raises(InvalidRequest):
        line.survey_chute(140.0, runtime.clock.now(), "test")


def test_the_chute_reports_a_low_level_as_clear_margin(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)

    high = line.survey_chute(70.0, runtime.clock.now(), "test")
    low = line.survey_chute(30.0, runtime.clock.now(), "test")

    assert high["margin_pct"] == 8.0
    assert low["margin_pct"] == 48.0
    assert low["state"] == "clear"
    assert line.parts.detector.is_clear(30.0) is True
    assert STATE_LOW != low["state"]
