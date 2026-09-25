"""Deterministic scripted runs over the control core."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .belt.scale import ScalePoint
from .clock import ManualClock
from .config import PlantConfig
from .cone.calibrate import CurrentPoint
from .defaults import DEFAULT_UNIT
from .errors import CrushError
from .line.plan import START_STEPS, STOP_STEPS, TRIP_STEPS
from .report import site_report
from .runtime import Runtime, build_runtime

SCENARIOS = (
    "cold-start",
    "blockage-recovery",
    "stale-calibration",
    "emergency-stop",
    "stop-and-settle",
)


def scenario_names() -> list[str]:
    return list(SCENARIOS)


def scenario_run_dir(data_dir: Path | str, name: str) -> Path:
    """A directory no earlier run has used, so a rerun starts from nothing.

    The name carries a serial number rather than a timestamp, which keeps a
    rerun reproducible and independent of the clock.
    """

    if name not in SCENARIOS:
        raise KeyError(f"unknown scenario {name!r}: {', '.join(SCENARIOS)}")
    base = Path(data_dir) / "scenarios"
    used = {path.name for path in base.glob(f"{name}-*")} if base.is_dir() else set()
    serial = 1
    while f"{name}-{serial}" in used:
        serial += 1
    return base / f"{name}-{serial}"


def plan_scenarios() -> dict[str, list[str]]:
    """The sequence names every scenario is written around."""

    return {"start": list(START_STEPS), "stop": list(STOP_STEPS), "trip": list(TRIP_STEPS)}


def _advance(runtime: Runtime, seconds: float, step: float = 30.0) -> None:
    remaining = float(seconds)
    while remaining > 1e-9:
        chunk = min(step, remaining)
        runtime.clock.advance(chunk)
        runtime.tick(chunk, "scenario")
        remaining -= chunk


def _calibrate(runtime: Runtime, unit: str, actor: str) -> dict[str, Any]:
    """Give the line a fresh belt scale and current line to work against."""

    line = runtime.line(unit)
    moment = runtime.clock.now()
    ttl = runtime.config.baseline_ttl_s
    scale = line.calibrate_scale(
        [ScalePoint(raw_counts=120.0, kg_per_m=18.0), ScalePoint(raw_counts=520.0, kg_per_m=74.0)],
        moment,
        actor,
        ttl,
    )
    cone = line.calibrate_cone(
        [CurrentPoint(tonnes_per_hour=120.0, amps=96.0), CurrentPoint(tonnes_per_hour=420.0, amps=168.0)],
        moment,
        actor,
        ttl,
    )
    return {"scale": scale, "cone": cone}


def _bring_up(runtime: Runtime, unit: str, target_tph: float, actor: str) -> dict[str, Any]:
    """Calibrate, load the bin and walk the whole bring-up order."""

    line = runtime.line(unit)
    moment = runtime.clock.now()
    calibration = _calibrate(runtime, unit, actor)
    line.sample_level(64.0, moment, actor)
    line.confirm(target_tph, runtime.config.confirmation_ttl_s, moment, actor)
    started = line.start(target_tph, moment, actor)
    line.measure_feed(target_tph * 0.98, moment, actor)
    return {"calibration": calibration, "start": started}


def _cold_start(runtime: Runtime, actor: str) -> dict[str, Any]:
    steps: list[Any] = [_bring_up(runtime, DEFAULT_UNIT, 600.0, actor)]
    line = runtime.line(DEFAULT_UNIT)
    _advance(runtime, 300.0)
    steps.append({"level": line.sample_level(61.0, runtime.clock.now(), actor)})
    steps.append({"plan": line.plan_feed(runtime.clock.now(), actor)})
    steps.append({"weigh": line.weigh(430.0, runtime.clock.now(), actor, seconds=60.0)})
    steps.append({"product": line.sample_product(228.0, 250.0, runtime.clock.now(), actor)})
    steps.append({"feed": line.parts.feeder.state().as_dict()})
    return {"steps": steps}


def _blockage_recovery(runtime: Runtime, actor: str) -> dict[str, Any]:
    steps: list[Any] = [_bring_up(runtime, DEFAULT_UNIT, 520.0, actor)]
    line = runtime.line(DEFAULT_UNIT)
    for _ in range(3):
        _advance(runtime, 30.0)
        steps.append({"survey": line.survey_chute(84.0, runtime.clock.now(), actor)})
    steps.append({"latched": line.parts.latch.state().as_dict()})
    steps.append({"gate": line.parts.gate.preview()})
    _advance(runtime, runtime.config.safety.latch_hold_s + 5.0)
    steps.append({"cleared": line.clear_chute(runtime.clock.now(), actor, "chute emptied", level_pct=41.0)})
    steps.append({"gate": line.parts.gate.preview()})
    return {"steps": steps}


def _stale_calibration(runtime: Runtime, actor: str) -> dict[str, Any]:
    steps: list[Any] = [_bring_up(runtime, DEFAULT_UNIT, 480.0, actor)]
    line = runtime.line(DEFAULT_UNIT)
    steps.append({"generation": runtime.set_generation("cone liner changed", actor)})
    steps.append({"scale": line.parts.scale.mapping()})
    steps.append({"cone_start": line.parts.cone.state().as_dict()})
    return {"steps": steps}


def _emergency_stop(runtime: Runtime, actor: str) -> dict[str, Any]:
    steps: list[Any] = [_bring_up(runtime, DEFAULT_UNIT, 560.0, actor)]
    line = runtime.line(DEFAULT_UNIT)
    _advance(runtime, 120.0)
    steps.append({"trip": line.emergency_stop("screen bearing temperature", runtime.clock.now(), actor)})
    steps.append({"progress": line.progress("trip")})
    runtime.clock.advance(runtime.config.safety.latch_hold_s + 1.0)
    steps.append({"latch": line.clear_latch(runtime.clock.now(), actor, "bearing replaced")})
    steps.append({"recover": line.recover(runtime.clock.now(), actor)})
    return {"steps": steps}


def _stop_and_settle(runtime: Runtime, actor: str) -> dict[str, Any]:
    steps: list[Any] = [_bring_up(runtime, DEFAULT_UNIT, 500.0, actor)]
    line = runtime.line(DEFAULT_UNIT)
    _advance(runtime, 900.0)
    steps.append({"stop": line.stop(runtime.clock.now(), actor)})
    steps.append({"feeder": line.parts.feeder.state().as_dict()})
    steps.append({"report": site_report(runtime)["totals"]})
    return {"steps": steps}


RUNNERS: dict[str, Callable[[Runtime, str], dict[str, Any]]] = {
    "cold-start": _cold_start,
    "blockage-recovery": _blockage_recovery,
    "stale-calibration": _stale_calibration,
    "emergency-stop": _emergency_stop,
    "stop-and-settle": _stop_and_settle,
}


def run_scenario(
    config: PlantConfig,
    data_dir: Path | str,
    name: str,
    start: datetime | None = None,
    actor: str = "scenario",
) -> dict[str, Any]:
    """Run one scripted scenario against a fresh data directory."""

    runner = RUNNERS.get(name)
    if runner is None:
        raise KeyError(f"unknown scenario {name!r}: {', '.join(SCENARIOS)}")
    runtime = build_runtime(config, data_dir, ManualClock(start))
    try:
        outcome = runner(runtime, actor)
    except CrushError as error:
        return {
            "scenario": name,
            "ok": False,
            "error": error.as_payload(),
            "report": site_report(runtime),
            "audit_tail": [entry.as_dict() for entry in runtime.ledger.tail(10)],
        }
    return {
        "scenario": name,
        "ok": True,
        "steps": outcome["steps"],
        "report": site_report(runtime),
        "audit_tail": [entry.as_dict() for entry in runtime.ledger.tail(10)],
        "records": runtime.stream.summary(),
    }
