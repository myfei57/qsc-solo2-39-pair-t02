"""The registers the line keeps: judgement trail, batch settlement, codes."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from crushplant.config import ConfigError, validate
from crushplant.console import ControlApp, Request
from crushplant.defaults import DEFAULT_UNIT, default_config
from crushplant.errors import NameConflict, StateConflict, catalog
from tests.support import advance, bring_up, clock_of, manual_runtime


def test_a_judgement_trail_can_be_read_as_of_an_earlier_instant(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    first = clock_of(runtime).now()
    line.parts.jaw.sample_amps(210.0, first, "test")

    advance(runtime, 120.0)
    later = clock_of(runtime).now()
    line.parts.jaw.sample_amps(390.0, later, "test")

    assert runtime.verdicts.current()[f"{DEFAULT_UNIT}.jaw"].value == 390.0
    assert runtime.verdicts.as_of(first)[f"{DEFAULT_UNIT}.jaw"].value == 210.0
    assert runtime.verdicts.as_of(first) != runtime.verdicts.current()


def test_a_judgement_trail_can_be_read_by_the_basis_it_was_taken_on(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    line.parts.jaw.sample_amps(210.0, runtime.clock.now(), "test")
    line.parts.cone.sample_amps(180.0, runtime.clock.now(), "test")

    by_jaw = runtime.verdicts.by_basis("jaw.current")

    assert [entry.subject for entry in by_jaw] == [f"{DEFAULT_UNIT}.jaw"]
    assert runtime.verdicts.summary()["bases"] == ["cone.current", "cone.stall", "jaw.current"]
    assert runtime.verdicts.generations() == [1]


def test_a_batch_is_settled_after_it_is_closed(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    moment = runtime.clock.now()
    code = runtime.issuer.issue("batch", moment)
    runtime.batches.open(code, DEFAULT_UNIT, "production", moment, "test")
    runtime.batches.close(code, moment, "test", tonnes=812.5)

    settled = runtime.batches.settle(code, moment, "test")

    assert settled.settled_tonnes == 812.5
    assert settled.describe().endswith("812.5 t")
    assert runtime.batches.summary()["settled"] == 1
    assert runtime.batches.unsettled() == []
    with pytest.raises(StateConflict):
        runtime.batches.settle(code, moment, "test")


def test_a_line_only_works_against_one_open_batch(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    moment = runtime.clock.now()
    runtime.batches.open("B-1", DEFAULT_UNIT, "production", moment, "test")

    with pytest.raises(StateConflict) as failure:
        runtime.batches.open("B-2", DEFAULT_UNIT, "production", moment, "test")

    assert failure.value.details["code"] == "B-1"
    assert runtime.batches.open_for(DEFAULT_UNIT).code == "B-1"
    assert runtime.batches.open_for("CP-2") is None


def test_the_error_catalogue_is_one_list_for_every_face(tmp_path: Path) -> None:
    entries = catalog()
    app = ControlApp(manual_runtime(tmp_path))

    served = app.handle(Request(method="GET", path="/api/errors"))

    assert served.payload["count"] == len(entries) == 13
    assert {entry["code"] for entry in entries} >= {"interlock", "not-durable", "stale", "latched"}
    assert all(entry["status"] >= 400 and entry["summary"] for entry in entries)


def test_a_refused_request_is_written_down_with_the_operator(tmp_path: Path) -> None:
    app = ControlApp(manual_runtime(tmp_path))
    app.handle(Request(method="POST", path="/api/belt/start", body={"unit": DEFAULT_UNIT, "actor": "li-wei"}))

    blocked = app.runtime.ledger.entries(outcome="blocked")

    assert blocked and blocked[-1].actor == "li-wei"


def test_a_deck_cannot_be_shared_by_two_lines() -> None:
    config = default_config()
    shared = replace(config, lines=(config.lines[0], replace(config.lines[1], screen_deck_id="D1")))

    with pytest.raises(ConfigError) as failure:
        validate(shared)

    assert any("is shared by" in item for item in failure.value.details["problems"])


def test_the_register_refuses_a_code_it_already_holds(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    moment = runtime.clock.now()
    for _ in range(2):
        runtime.issuer.issue("batch", moment)

    assert runtime.issuer.codes_by_kind()["batch"] == runtime.issuer.all_codes()
    assert len(runtime.issuer.codes_by_kind()["calibration"]) == 0
    with pytest.raises(NameConflict):
        runtime.issuer.register(runtime.issuer.all_codes()[0])
