"""The operator report and the scripted runs it is built from."""

from __future__ import annotations

from pathlib import Path

import pytest

from crushplant.line.plan import START_STEPS, STOP_STEPS, TRIP_STEPS
from crushplant.report import lines, site_report, unit_report
from crushplant.scenario import plan_scenarios, run_scenario, scenario_names
from tests.support import DEFAULT_UNIT, bring_up, manual_runtime


def test_the_unit_report_carries_the_fields_a_panel_shows(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    line.measure_feed(585.0, runtime.clock.now(), "test")

    report = unit_report(line, runtime.clock.now())

    assert report["unit"] == DEFAULT_UNIT
    assert report["state"] == "running"
    assert report["feed_tph"] == 600.0
    assert report["measured_tph"] == 585.0
    assert report["jaw_state"] == "running"
    assert report["jaw_committed"] is True
    assert report["cone_running"] is True
    assert report["magnets_ready"] is True
    assert report["gate_ok"] is True
    assert report["stages_done"] == list(START_STEPS)
    assert report["scale_generation"] == 1


def test_the_site_report_totals_every_line(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    bring_up(runtime, DEFAULT_UNIT, 600.0)
    bring_up(runtime, "CP-2", 430.0)

    report = site_report(runtime)

    assert report["site"] == "north-pit"
    assert report["generation"] == 1
    assert [unit["unit"] for unit in report["units"]] == ["CP-1", "CP-2"]
    assert report["totals"]["feed_tph"] == 1030.0
    assert report["totals"]["running"] == ["CP-1", "CP-2"]
    assert report["totals"]["latched"] == []
    assert report["records"]["pending"] == 0
    assert report["batches"]["count"] == 0


def test_the_plain_lines_render_the_totals(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    bring_up(runtime)

    rendered = lines(site_report(runtime))

    assert rendered[0] == "site north-pit generation 1"
    assert any(text.startswith("CP-1 state=running") for text in rendered)
    assert any(text.startswith("totals feed=") for text in rendered)
    assert any(text.startswith("audit ") for text in rendered)


def test_the_line_status_carries_every_component(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    status = runtime.line(DEFAULT_UNIT).status(runtime.clock.now())

    assert set(status) >= {
        "machine",
        "feeder",
        "jaw",
        "belt",
        "magnet",
        "alarm",
        "screen",
        "chute",
        "cone",
        "calibration",
        "scale",
        "level",
        "plan",
        "bin",
        "latch",
        "gate",
    }
    assert status["gate"]["ok"] is False
    assert status["machine"]["plans"]["start"] == list(START_STEPS)
    assert status["machine"]["plans"]["stop"] == list(STOP_STEPS)
    assert status["machine"]["plans"]["trip"] == list(TRIP_STEPS)


def test_every_scenario_runs_to_completion(tmp_path: Path) -> None:
    for name in scenario_names():
        result = run_scenario(manual_runtime(tmp_path).config, tmp_path / f"run-{name}", name)

        assert result["ok"] is True, result.get("error")
        assert result["scenario"] == name
        assert result["records"]["pending"] == 0
        assert result["report"]["site"] == "north-pit"
        assert result["audit_tail"]


def test_the_blockage_scenario_leaves_the_latch_released_again(tmp_path: Path) -> None:
    result = run_scenario(manual_runtime(tmp_path).config, tmp_path / "blockage", "blockage-recovery")

    cleared = result["steps"][-1]

    assert cleared["gate"]["ok"] is True
    assert result["report"]["totals"]["latched"] == []


def test_the_stale_calibration_scenario_reports_the_new_generation(tmp_path: Path) -> None:
    result = run_scenario(manual_runtime(tmp_path).config, tmp_path / "stale", "stale-calibration")

    assert result["ok"] is True
    assert result["report"]["generation"] == 2
    assert result["steps"][1]["generation"]["invalidated"] == [f"{DEFAULT_UNIT}.feed-1-1"]


def test_an_unknown_scenario_is_refused(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        run_scenario(manual_runtime(tmp_path).config, tmp_path / "nope", "no-such-run")


def test_the_scenario_plan_names_every_sequence() -> None:
    plan = plan_scenarios()

    assert plan == {
        "start": list(START_STEPS),
        "stop": list(STOP_STEPS),
        "trip": list(TRIP_STEPS),
    }
