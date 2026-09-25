"""Operator facing summaries built from the component state."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from .line.plant import CrushingLine
from .units import percent_of, round_to


def unit_report(line: CrushingLine, moment: datetime) -> dict[str, Any]:
    """A compact view of one line, the way a panel would show it."""

    status = line.status(moment)
    machine = status["machine"]
    feeder = status["feeder"]
    belt = status["belt"]
    cone = status["cone"]
    bin_state = status["bin"]
    gate = status["gate"]
    calibration = status["calibration"]
    load_share = None
    if calib_gain := calibration["gain_amps_per_tph"]:
        expected = calibration["idle_amps"] + calib_gain * feeder["setpoint_tph"]
        load_share = percent_of(cone["amps"], expected) if cone["amps"] else 0.0
    return {
        "unit": status["unit"],
        "state": machine["state"],
        "cycle": machine["cycle"],
        "feed_tph": feeder["setpoint_tph"],
        "measured_tph": feeder["measured_tph"],
        "tonnage_t": feeder["tonnage"],
        "belt_tph": belt["tonnes_per_hour"],
        "belt_load_kg_per_m": belt["load_kg_per_m"],
        "jaw_state": status["jaw"]["state"],
        "jaw_revision": status["jaw"]["revision"],
        "jaw_committed": status["jaw"]["committed_sequence"] > 0,
        "cone_running": cone["running"],
        "cone_stalled": cone["stalled"],
        "magnets_ready": status["magnet"]["ready"],
        "bin_level_pct": bin_state["level_pct"],
        "bin_tonnes": bin_state["tonnes"],
        "blocked": status["chute"]["blocked"],
        "latched": status["latch"]["latched"],
        "latch_reason": status["latch"]["reason"],
        "gate_ok": gate["ok"],
        "gate_unmet": gate["unmet"],
        "cone_load_share_pct": load_share,
        "stages_done": machine["stages"],
        "stages_outstanding": machine["start"]["outstanding"],
        "scale_generation": status["scale"]["generation"],
    }


def site_report(runtime: Any) -> dict[str, Any]:
    """Totals across the site plus the ledger and batch summaries."""

    moment = runtime.clock.now()
    units = [unit_report(line, moment) for _, line in sorted(runtime.lines.items())]
    return {
        "site": runtime.config.site,
        "generation": runtime.generations.generation(),
        "at": moment.isoformat(),
        "units": units,
        "totals": {
            "feed_tph": round_to(sum(unit["feed_tph"] for unit in units), 3),
            "tonnage_t": round_to(sum(unit["tonnage_t"] for unit in units), 3),
            "running": [unit["unit"] for unit in units if unit["state"] == "running"],
            "standby": [unit["unit"] for unit in units if unit["state"] == "standby"],
            "latched": [unit["unit"] for unit in units if unit["latched"]],
            "blocked": [unit["unit"] for unit in units if unit["blocked"]],
        },
        "batches": runtime.batches.summary(),
        "verdicts": runtime.verdicts.summary(),
        "audit_outcomes": runtime.ledger.outcomes(),
        "records": runtime.stream.summary(),
    }


def lines(report: Mapping[str, Any]) -> list[str]:
    """Render a report as the plain-text block the console script prints."""

    output: list[str] = [f"site {report.get('site', '?')} generation {report.get('generation', '?')}"]
    units = report.get("units")
    if isinstance(units, list):
        for unit in units:
            if not isinstance(unit, dict):
                continue
            output.append(
                " ".join(
                    [
                        f"{unit.get('unit', '?')}",
                        f"state={unit.get('state', '?')}",
                        f"feed={unit.get('feed_tph', 0.0)}t/h",
                        f"belt={unit.get('belt_tph', 0.0)}t/h",
                        f"jaw={unit.get('jaw_state', '?')}",
                        f"cone={unit.get('cone_running')}",
                        f"bin={unit.get('bin_level_pct', 0.0)}%",
                        f"gate={'ok' if unit.get('gate_ok') else 'blocked'}",
                        f"latched={unit.get('latched')}",
                    ]
                )
            )
    totals = report.get("totals")
    if isinstance(totals, dict):
        output.append(
            f"totals feed={totals.get('feed_tph', 0.0)}t/h "
            f"tonnage={totals.get('tonnage_t', 0.0)}t "
            f"running={len(totals.get('running', []))}"
        )
    verdicts = report.get("verdicts")
    if isinstance(verdicts, dict) and isinstance(verdicts.get("states"), dict):
        output.append("verdicts " + " ".join(f"{key}={value}" for key, value in sorted(verdicts["states"].items())))
    outcomes = report.get("audit_outcomes")
    if isinstance(outcomes, dict) and outcomes:
        output.append("audit " + " ".join(f"{key}={value}" for key, value in sorted(outcomes.items())))
    return output
