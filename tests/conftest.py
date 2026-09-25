"""Fixtures shared by the line control test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from crushplant.clock import ManualClock
from crushplant.config import PlantConfig
from crushplant.defaults import default_config
from crushplant.runtime import Runtime, build_runtime
from tests.support import START


@pytest.fixture()
def config() -> PlantConfig:
    return default_config()


@pytest.fixture()
def clock() -> ManualClock:
    return ManualClock(START)


@pytest.fixture()
def runtime(tmp_path: Path, config: PlantConfig, clock: ManualClock) -> Runtime:
    return build_runtime(config, tmp_path / "site", clock)
