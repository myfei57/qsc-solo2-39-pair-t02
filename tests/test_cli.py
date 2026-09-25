"""The command line entry point and the commands it offers."""

from __future__ import annotations

import json
from pathlib import Path

from crushplant.cli import build_parser, main
from crushplant.line.plan import START_STEPS, STOP_STEPS, TRIP_STEPS
from crushplant.scenario import scenario_names


def run(tmp_path: Path, *argv: str) -> int:
    return main(["--data-dir", str(tmp_path / "site"), *argv])


def test_the_selftest_runs_a_whole_cycle_and_reports_the_stages(tmp_path: Path, capsys) -> None:
    assert run(tmp_path, "selftest") == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["state"] == "running"
    assert payload["stages"] == list(START_STEPS)
    assert payload["report"]["gate_ok"] is True


def test_the_status_report_and_records_commands_emit_json(tmp_path: Path, capsys) -> None:
    assert run(tmp_path, "status") == 0
    assert json.loads(capsys.readouterr().out)["site"] == "north-pit"

    assert run(tmp_path, "report", "--plain") == 0
    assert capsys.readouterr().out.startswith("site north-pit")

    assert run(tmp_path, "records", "--limit", "2") == 0
    assert "summary" in json.loads(capsys.readouterr().out)

    assert run(tmp_path, "sequences") == 0
    sequences = json.loads(capsys.readouterr().out)["sequences"]
    assert sequences["start"] == list(START_STEPS)
    assert sequences["stop"] == list(STOP_STEPS)
    assert sequences["trip"] == list(TRIP_STEPS)


def test_a_refused_action_exits_with_code_two_and_a_reason(tmp_path: Path, capsys) -> None:
    assert run(tmp_path, "unit", "feed", "--unit", "CP-1") == 2

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert payload["error"] == "ordering-violation"
    assert payload["details"]["expected"] == "jaw-state"


def test_the_scenario_command_writes_its_result_to_a_file(tmp_path: Path, capsys) -> None:
    out = tmp_path / "scenario.json"
    assert run(tmp_path, "scenario", "--name", "blockage-recovery", "--out", str(out)) == 0

    emitted = json.loads(capsys.readouterr().out)
    written = json.loads(out.read_text(encoding="utf-8"))
    assert emitted["ok"] is True is written["ok"]
    assert written["scenario"] == "blockage-recovery"
    assert "report" in written


def test_the_tombstone_command_voids_a_committed_record(tmp_path: Path, capsys) -> None:
    run(tmp_path, "selftest")
    capsys.readouterr()

    assert run(tmp_path, "tombstone", "--sequence", "2", "--reason", "entered against the spare line") == 0

    marker = json.loads(capsys.readouterr().out)
    assert marker["kind"] == "void"
    assert marker["ref"] == 2
    assert run(tmp_path, "records", "--limit", "1") == 0
    assert json.loads(capsys.readouterr().out)["summary"]["voided"] == 1


def test_the_generation_and_gate_commands_move_the_line(tmp_path: Path, capsys) -> None:
    assert run(tmp_path, "generation", "--reason", "liner changed", "--actor", "engineer") == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["generation"]["generation"] == 2
    assert payload["invalidated"] == []

    assert run(tmp_path, "baselines") == 0
    assert json.loads(capsys.readouterr().out)["subjects"] == []

    assert run(tmp_path, "namespace") == 0
    assert json.loads(capsys.readouterr().out)["summary"]["tags"] > 20


def test_the_parser_offers_every_sequence_command_and_scenario() -> None:
    parser = build_parser()
    args = parser.parse_args(["unit", "begin", "--unit", "CP-1"])

    assert args.action == "begin"
    assert args.unit == "CP-1"
    assert parser.parse_args(["scenario"]).name == "cold-start"
    assert scenario_names()[0] == "cold-start"
