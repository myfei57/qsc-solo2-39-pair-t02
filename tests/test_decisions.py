"""Contract four: threshold and window comparisons, current state against history."""

from __future__ import annotations

from pathlib import Path

import pytest

from crushplant.errors import InvalidRequest
from crushplant.store.documents import DocumentStore
from crushplant.verdict.threshold import STATE_HIGH, STATE_LOW, STATE_OK, Limit, judge
from crushplant.verdict.window import WINDOW_CLEAR, WINDOW_HELD, WINDOW_HOLDING, ABOVE, WindowJudge
from tests.support import DEFAULT_UNIT, START, bring_up, clock_of, manual_runtime


def test_a_reading_inside_its_window_reports_the_room_it_has_left() -> None:
    limit = Limit("feeder.setpoint", 180.0, 750.0)

    verdict = judge("CP-1.feeder", 620.0, limit, START)

    assert verdict.state == STATE_OK
    assert verdict.ok() is True
    assert verdict.margin == 130.0
    assert limit.width() == 570.0
    assert limit.contains(180.0) is True


def test_a_reading_over_the_ceiling_is_high_with_a_negative_margin() -> None:
    verdict = judge("CP-1.cone", 310.0, Limit("cone.current", 70.0, 300.0), START)

    assert verdict.state == STATE_HIGH
    assert verdict.margin == -10.0
    assert verdict.high == 300.0


def test_a_reading_under_the_floor_is_low_with_a_negative_margin() -> None:
    verdict = judge("CP-1.cone", 40.0, Limit("cone.current", 70.0, 300.0), START)

    assert verdict.state == STATE_LOW
    assert verdict.margin == -30.0


def test_an_inverted_window_is_refused() -> None:
    with pytest.raises(InvalidRequest):
        Limit("feeder.setpoint", 750.0, 180.0)


def test_a_single_spike_never_confirms_a_condition(tmp_path: Path) -> None:
    judge_window = WindowJudge(
        "CP-1.chute",
        DocumentStore(tmp_path / "state"),
        threshold=78.0,
        hold_seconds=45.0,
        min_samples=3,
    )

    first = judge_window.observe(84.0, START)
    second = judge_window.observe(84.0, START.replace(second=15))

    assert first.state == WINDOW_HOLDING
    assert second.state == WINDOW_HOLDING
    assert first.held() is False


def test_a_condition_held_for_the_whole_window_is_confirmed(tmp_path: Path) -> None:
    judge_window = WindowJudge(
        "CP-1.chute",
        DocumentStore(tmp_path / "state"),
        threshold=78.0,
        hold_seconds=45.0,
        min_samples=3,
    )
    judge_window.observe(84.0, START)
    judge_window.observe(84.0, START.replace(second=20))
    sustained = judge_window.observe(84.0, START.replace(second=50))

    assert sustained.state == WINDOW_HELD
    assert sustained.samples == 3
    assert sustained.span_seconds == 50.0


def test_a_reading_below_the_threshold_clears_the_window(tmp_path: Path) -> None:
    judge_window = WindowJudge(
        "CP-1.chute",
        DocumentStore(tmp_path / "state"),
        threshold=78.0,
        hold_seconds=45.0,
        min_samples=3,
    )
    judge_window.observe(84.0, START)
    cleared = judge_window.observe(41.0, START.replace(second=20))

    assert cleared.state == WINDOW_CLEAR
    assert cleared.samples == 0
    assert judge_window.crosses(84.0) is True
    assert judge_window.crosses(41.0) is False


def test_a_reversed_window_direction_watches_the_low_side(tmp_path: Path) -> None:
    judge_window = WindowJudge(
        "CP-1.bin",
        DocumentStore(tmp_path / "state"),
        threshold=25.0,
        hold_seconds=20.0,
        min_samples=2,
        direction="below",
    )
    judge_window.observe(18.0, START)
    held = judge_window.observe(12.0, START.replace(second=25))

    assert held.state == WINDOW_HELD
    assert held.direction == "below"
    with pytest.raises(InvalidRequest):
        WindowJudge("x", DocumentStore(tmp_path / "s2"), threshold=1.0, hold_seconds=0.0, direction=ABOVE)


def test_the_current_verdict_is_the_newest_one_while_history_keeps_them_all(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    moment = runtime.clock.now()
    line.parts.jaw.sample_amps(200.0, moment, "test")
    line.parts.jaw.sample_amps(390.0, moment, "test")
    line.parts.jaw.sample_amps(210.0, moment, "test")

    current = runtime.verdicts.current()[f"{DEFAULT_UNIT}.jaw"]

    assert current.value == 210.0
    assert current.state == STATE_OK
    history = runtime.verdicts.history(f"{DEFAULT_UNIT}.jaw")
    assert [entry.value for entry in history] == [200.0, 390.0, 210.0]
    assert history[1].state == STATE_HIGH
    assert runtime.verdicts.counts() == {STATE_OK: 2, STATE_HIGH: 1}


def test_verdicts_can_be_read_one_line_at_a_time(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    bring_up(runtime, DEFAULT_UNIT, 600.0)
    bring_up(runtime, "CP-2", 430.0)
    runtime.line("CP-2").parts.jaw.sample_amps(120.0, runtime.clock.now(), "test")

    only_two = runtime.verdicts.entries(unit="CP-2")

    assert {entry.unit for entry in only_two} == {"CP-2"}
    assert runtime.verdicts.entries(limit=1)[-1].unit == "CP-2"
    with pytest.raises(InvalidRequest):
        runtime.verdicts.entries(limit=-1)


def test_a_product_sample_is_judged_on_both_sides_of_the_size_limit(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)

    good = line.sample_product(228.0, 250.0, runtime.clock.now(), "test")
    poor = line.sample_product(200.0, 250.0, runtime.clock.now(), "test")

    assert good["undersize"]["state"] == STATE_OK
    assert good["oversize"]["state"] == STATE_OK
    assert poor["undersize"]["state"] == STATE_LOW
    assert poor["oversize"]["state"] == STATE_HIGH


def test_a_deck_that_is_over_its_rating_is_reported(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    over = line.parts.decks.duty(
        line.spec.screen_capacity_tph * 1.15,
        runtime.clock.now(),
        "test",
    )

    assert over["verdict"]["state"] == STATE_HIGH
    assert over["load"]["utilisation_pct"] == 115.0
    assert over["load"]["specific_tph_per_m2"] > 0


def test_the_verdict_trail_keeps_every_judgement_in_order(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    for amps in (200.0, 210.0, 220.0, 230.0):
        line.parts.jaw.sample_amps(amps, runtime.clock.now(), "test")

    assert len(runtime.verdicts.entries()) == 4
    assert [entry.value for entry in runtime.verdicts.entries(limit=2)] == [220.0, 230.0]
    assert runtime.verdicts.summary()["recorded"] == 4
    assert clock_of(runtime).now() == START
