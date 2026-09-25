"""The configuration envelope the service refuses to run outside of."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from crushplant.config import (
    ConfigError,
    diff_configs,
    envelope_report,
    load_config,
    plant_config_from_dict,
    validate,
)
from crushplant.defaults import DEFAULT_UNIT, default_config


def test_the_default_configuration_passes_its_own_validation() -> None:
    config = default_config()

    assert config.unit_ids() == ["CP-1", "CP-2"]
    assert config.line(DEFAULT_UNIT).feeder_window() == (180.0, 750.0)
    assert config.line(DEFAULT_UNIT).stall_amps() == 211.2
    with pytest.raises(ConfigError):
        config.line("CP-9")


def test_the_envelope_reports_every_window_the_line_will_run_inside() -> None:
    envelope = envelope_report(default_config())

    assert envelope["lines"][DEFAULT_UNIT]["feed_tph"] == [180.0, 750.0]
    assert envelope["lines"][DEFAULT_UNIT]["chute_pct"] == [62.0, 78.0]
    assert envelope["quality"]["undersize_min_pct"] == 88.0
    assert envelope["lifetimes_s"]["baseline"] == 3600.0


def test_an_inverted_feeder_band_is_refused() -> None:
    config = default_config()
    broken = replace(config, lines=(replace(config.lines[0], feeder_min_tph=800.0),))

    with pytest.raises(ConfigError) as failure:
        validate(broken)

    assert any("feeder band is empty" in item for item in failure.value.details["problems"])


def test_a_line_that_is_configured_twice_is_refused() -> None:
    config = default_config()
    broken = replace(config, lines=(config.lines[0], config.lines[0]))

    with pytest.raises(ConfigError) as failure:
        validate(broken)

    assert any("configured twice" in item for item in failure.value.details["problems"])


def test_a_configuration_file_round_trips_through_json(tmp_path: Path) -> None:
    config = default_config()
    path = tmp_path / "plant.json"
    path.write_text(json.dumps(config.as_dict()), encoding="utf-8")

    loaded = load_config(path)

    assert loaded.as_dict() == config.as_dict()
    assert plant_config_from_dict(config.as_dict()).unit_ids() == ["CP-1", "CP-2"]


def test_a_missing_or_malformed_configuration_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.json")

    broken = tmp_path / "broken.json"
    broken.write_text("{oops", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(broken)

    listed = tmp_path / "listed.json"
    listed.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(listed)


def test_a_configuration_that_is_missing_a_field_is_reported() -> None:
    raw = default_config().as_dict()
    del raw["lines"][0]["chute_block_pct"]

    with pytest.raises(ConfigError) as failure:
        plant_config_from_dict(raw)

    assert failure.value.details["field"] == "chute_block_pct"


def test_the_change_preview_names_every_field_that_moved() -> None:
    current = default_config()
    candidate = replace(
        current,
        generation=2,
        lines=(replace(current.lines[0], feeder_max_tph=800.0), current.lines[1]),
    )

    diff = diff_configs(current, candidate)

    scopes = {change["scope"] for change in diff["changes"]}

    assert scopes == {"line.CP-1", "plant.generation"}
    assert diff["count"] == 2
    assert diff_configs(current, current)["count"] == 0
