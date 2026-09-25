"""Shared helpers: a site wired to a manual clock inside a temporary directory."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from crushplant.belt.scale import ScalePoint
from crushplant.clock import ManualClock
from crushplant.cone.calibrate import CurrentPoint
from crushplant.config import PlantConfig
from crushplant.defaults import DEFAULT_UNIT, default_config
from crushplant.line.plant import CrushingLine
from crushplant.runtime import Runtime, build_runtime

START = datetime(2026, 4, 6, 5, 30, 0, tzinfo=timezone.utc)
SCALE_POINTS = (ScalePoint(raw_counts=120.0, kg_per_m=18.0), ScalePoint(raw_counts=520.0, kg_per_m=74.0))
CONE_POINTS = (CurrentPoint(tonnes_per_hour=120.0, amps=96.0), CurrentPoint(tonnes_per_hour=420.0, amps=168.0))


def manual_runtime(tmp_path: Path, config: PlantConfig | None = None, name: str = "site") -> Runtime:
    """A site whose clock and data directory the test owns."""

    return build_runtime(config or default_config(), tmp_path / name, ManualClock(START))


def clock_of(runtime: Runtime) -> ManualClock:
    clock = runtime.clock
    assert isinstance(clock, ManualClock)
    return clock


def advance(runtime: Runtime, seconds: float, step: float = 30.0) -> None:
    """Move the manual clock forward one control cycle at a time."""

    remaining = float(seconds)
    while remaining > 1e-9:
        chunk = min(step, remaining)
        clock_of(runtime).advance(chunk)
        runtime.tick(chunk, "test")
        remaining -= chunk


def calibrate(runtime: Runtime, unit: str = DEFAULT_UNIT, actor: str = "test") -> dict:
    """Give one line a fresh belt scale and current line."""

    line = runtime.line(unit)
    moment = runtime.clock.now()
    ttl = runtime.config.baseline_ttl_s
    return {
        "scale": line.calibrate_scale(SCALE_POINTS, moment, actor, ttl),
        "cone": line.calibrate_cone(CONE_POINTS, moment, actor, ttl),
    }


def load_bin(runtime: Runtime, unit: str = DEFAULT_UNIT, level_pct: float = 64.0, actor: str = "test") -> dict:
    return runtime.line(unit).sample_level(level_pct, runtime.clock.now(), actor)


def confirm_feed(runtime: Runtime, unit: str = DEFAULT_UNIT, target_tph: float = 600.0, actor: str = "test") -> dict:
    return runtime.line(unit).confirm(target_tph, runtime.config.confirmation_ttl_s, runtime.clock.now(), actor)


def ready_to_feed(
    runtime: Runtime,
    unit: str = DEFAULT_UNIT,
    target_tph: float = 600.0,
    actor: str = "test",
) -> CrushingLine:
    """Walk every bring-up step except the feed itself."""

    line = runtime.line(unit)
    calibrate(runtime, unit, actor)
    load_bin(runtime, unit, 64.0, actor)
    confirm_feed(runtime, unit, target_tph, actor)
    moment = runtime.clock.now()
    line.begin(moment, actor)
    line.persist_jaw_state(moment, actor)
    line.ready_magnet(moment, actor)
    line.start_belt(moment, actor)
    line.start_screen(moment, actor)
    line.start_cone(moment, actor)
    return line


def bring_up(
    runtime: Runtime,
    unit: str = DEFAULT_UNIT,
    target_tph: float = 600.0,
    actor: str = "test",
) -> CrushingLine:
    """Walk the whole bring-up order on one line."""

    line = ready_to_feed(runtime, unit, target_tph, actor)
    line.feed(target_tph, runtime.clock.now(), actor)
    return line


def reload_runtime(runtime: Runtime) -> Runtime:
    """Rebuild the same site from the same data directory."""

    return build_runtime(runtime.config, runtime.data_dir, runtime.clock)
