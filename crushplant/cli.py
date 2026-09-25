"""Command line entry point: serve the console or act on the line once."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from . import SERVICE_NAME, __version__
from .audit.query import AuditQuery
from .belt.scale import ScalePoint
from .clock import ManualClock
from .config import diff_configs, envelope_report, load_config
from .cone.calibrate import CurrentPoint
from .console import ControlApp, serve
from .defaults import default_config
from .errors import CrushError, catalog
from .line.plan import START_STEPS, STOP_STEPS
from .report import lines as report_lines
from .report import site_report, unit_report
from .runtime import DEFAULT_DATA_DIR, Runtime, build_runtime
from .scenario import run_scenario, scenario_names, scenario_run_dir

START_CHOICES = tuple(START_STEPS)
STOP_CHOICES = tuple(STOP_STEPS)
SAMPLE_KINDS = ("level", "feed", "amps", "product", "chute")
UNIT_ACTIONS = (
    "begin",
    "jaw",
    "magnet",
    "belt",
    "screen",
    "feed",
    "start",
    "stop",
    "drain",
    "jaw-stop",
    "cone-stop",
    "belt-stop",
    "trip",
    "release",
    "recover",
    "plan",
    "tick",
    "reset",
)

CONFIG_HELP = "JSON configuration file"
DATA_DIR_HELP = "directory holding the line state"


def _add_common(parser: argparse.ArgumentParser) -> None:
    """Options accepted before the command name."""

    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help=DATA_DIR_HELP)
    parser.add_argument("--config", default=None, help=CONFIG_HELP)


def _add_local_common(parser: argparse.ArgumentParser) -> None:
    """Repeat the common options on a subcommand without clobbering the root."""

    parser.add_argument("--data-dir", default=argparse.SUPPRESS, help=DATA_DIR_HELP)
    parser.add_argument("--config", default=argparse.SUPPRESS, help=CONFIG_HELP)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=SERVICE_NAME, description="line control service")
    parser.add_argument("--version", action="version", version=f"{SERVICE_NAME} {__version__}")
    _add_common(parser)
    commands = parser.add_subparsers(dest="command")
    created: list[argparse.ArgumentParser] = []

    def sub(name: str, **kwargs: Any) -> argparse.ArgumentParser:
        created_parser = commands.add_parser(name, **kwargs)
        created.append(created_parser)
        return created_parser

    serve_cmd = sub("serve", help="run the JSON console")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8080)
    serve_cmd.add_argument("--log-level", default="INFO")

    selftest = sub("selftest", help="run a scripted cycle against a throwaway directory")
    selftest.add_argument("--target-tph", type=float, default=600.0)

    sub("status", help="print the site status")
    report = sub("report", help="print the operator report")
    report.add_argument("--unit", default=None)
    report.add_argument("--plain", action="store_true", help="print operator lines instead of JSON")

    scenario = sub("scenario", help="run a deterministic scenario")
    scenario.add_argument("--name", default="cold-start", choices=scenario_names())
    scenario.add_argument("--out", default=None, help="write the result to this file")

    tick = sub("tick", help="advance the control loop")
    tick.add_argument("--seconds", type=float, default=30.0)
    tick.add_argument("--steps", type=int, default=1)

    unit = sub("unit", help="operate one line")
    unit.add_argument("action", choices=UNIT_ACTIONS)
    unit.add_argument("--unit", required=True)
    unit.add_argument("--target-tph", type=float, default=None)
    unit.add_argument("--ttl-s", type=float, default=None)
    unit.add_argument("--seconds", type=float, default=30.0)
    unit.add_argument("--reason", default="cli request")
    unit.add_argument("--actor", default="cli")
    unit.add_argument("--force", action="store_true", help="override a protective hold")

    stage = sub("stage", help="run one bring-up step")
    stage.add_argument("stage", choices=START_CHOICES)
    stage.add_argument("--unit", required=True)
    stage.add_argument("--target-tph", type=float, default=None)
    stage.add_argument("--actor", default="cli")

    stop_stage = sub("stop-stage", help="run one shutdown step")
    stop_stage.add_argument("stage", choices=STOP_CHOICES)
    stop_stage.add_argument("--unit", required=True)
    stop_stage.add_argument("--reason", default="cli request")
    stop_stage.add_argument("--actor", default="cli")

    sample = sub("sample", help="record one measurement")
    sample.add_argument("--unit", required=True)
    sample.add_argument("--kind", required=True, choices=SAMPLE_KINDS)
    sample.add_argument("--value", type=float, required=True)
    sample.add_argument("--second", type=float, default=None, help="the paired value of a two-number sample")
    sample.add_argument("--machine", default="jaw", choices=("jaw", "cone"))
    sample.add_argument("--force", action="store_true")
    sample.add_argument("--actor", default="cli")

    calibrate = sub("calibrate", help="fit a belt scale or a current line")
    calibrate.add_argument("kind", choices=("scale", "cone"))
    calibrate.add_argument("--unit", required=True)
    calibrate.add_argument("--point", action="append", required=True, help="X=Y, repeatable")
    calibrate.add_argument("--ttl-s", type=float, default=None)
    calibrate.add_argument("--actor", default="cli")

    batch = sub("batch", help="open or close a production batch")
    batch.add_argument("action", choices=("open", "close"))
    batch.add_argument("--unit", default=None)
    batch.add_argument("--code", default=None)
    batch.add_argument("--kind", default="production")
    batch.add_argument("--tonnes", type=float, default=None)
    batch.add_argument("--actor", default="cli")

    audit = sub("audit", help="print ledger entries")
    audit.add_argument("--unit", default="")
    audit.add_argument("--action", default="")
    audit.add_argument("--outcome", default="")
    audit.add_argument("--subject", default="")
    audit.add_argument("--limit", type=int, default=20)

    sub("batches", help="print the batch registry")
    verdicts = sub("verdicts", help="print recorded judgements")
    verdicts.add_argument("--limit", type=int, default=20)
    sub("generations", help="print the generation history")
    sub("confirmations", help="print confirmation slips")
    sub("baselines", help="print captured baselines")
    sub("snapshots", help="print captured snapshots")
    sub("namespace", help="print the tag registry")
    sub("issuer", help="print issued identifiers")
    sub("store", help="print the state document inventory")
    sub("sequences", help="print the three ordered sequences")
    sub("errors", help="print the error catalogue")

    records = sub("records", help="print the record stream")
    records.add_argument("--limit", type=int, default=10)

    config_cmd = sub("config", help="print the configuration envelope")
    config_cmd.add_argument("--compare", default=None, help="preview the difference against another file")

    generation = sub("generation", help="bump the parameter generation")
    generation.add_argument("--reason", required=True)
    generation.add_argument("--actor", default="cli")

    snapshot = sub("snapshot", help="capture a record snapshot")
    snapshot.add_argument("--label", required=True)
    snapshot.add_argument("--ttl-s", type=float, default=None)
    snapshot.add_argument("--actor", default="cli")

    restore = sub("restore", help="roll the watermark back to a snapshot")
    restore.add_argument("--label", required=True)
    restore.add_argument("--reason", required=True)
    restore.add_argument("--actor", default="cli")

    tombstone = sub("tombstone", help="void a committed record")
    tombstone.add_argument("--sequence", type=int, required=True)
    tombstone.add_argument("--reason", required=True)
    tombstone.add_argument("--actor", default="cli")

    for created_parser in created:
        _add_local_common(created_parser)
    return parser


def _emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _advance_clock(runtime: Runtime, seconds: float) -> None:
    """Move a simulated clock forward; wall time moves on its own."""

    try:
        runtime.clock.advance(seconds)
    except RuntimeError:
        return


def _load_config(args: argparse.Namespace):
    if args.config:
        return load_config(Path(args.config))
    return default_config()


def _runtime(args: argparse.Namespace) -> Runtime:
    return build_runtime(_load_config(args), args.data_dir)


def _target_tph(runtime: Runtime, args: argparse.Namespace) -> float:
    if args.target_tph is not None:
        return float(args.target_tph)
    return runtime.config.line(args.unit).feeder_rated_tph


def _points(raw: list[str]) -> list[tuple[float, float]]:
    pairs: list[tuple[float, float]] = []
    for item in raw:
        left, _, right = item.partition("=")
        if not left or not right:
            raise CrushError(f"a calibration point looks like X=Y, got {item!r}")
        pairs.append((float(left), float(right)))
    return pairs


def _run_selftest(args: argparse.Namespace, target_tph: float) -> dict[str, Any]:
    data_dir = Path(args.data_dir)
    clock = ManualClock()
    runtime = build_runtime(_load_config(args), data_dir, clock)
    unit = runtime.unit_ids()[0]
    line = runtime.line(unit)
    moment = clock.now()
    line.calibrate_scale(
        [ScalePoint(raw_counts=120.0, kg_per_m=18.0), ScalePoint(raw_counts=520.0, kg_per_m=74.0)],
        moment,
        "selftest",
        runtime.config.baseline_ttl_s,
    )
    line.calibrate_cone(
        [CurrentPoint(tonnes_per_hour=120.0, amps=96.0), CurrentPoint(tonnes_per_hour=420.0, amps=168.0)],
        moment,
        "selftest",
        runtime.config.baseline_ttl_s,
    )
    line.load_bin(64.0, moment, "selftest")
    line.confirm(target_tph, runtime.config.confirmation_ttl_s, moment, "selftest")
    started = line.start(target_tph, moment, "selftest")
    line.measure_feed(target_tph * 0.97, moment, "selftest")
    for _ in range(4):
        clock.advance(60.0)
        runtime.tick(60.0, "selftest")
    line.weigh(460.0, clock.now(), "selftest", seconds=60.0)
    return {
        "ok": True,
        "unit": unit,
        "state": started["state"],
        "cycle": started["cycle"],
        "stages": line.parts.machine.stages(),
        "report": unit_report(line, clock.now()),
        "records": runtime.stream.summary(),
        "audit_tail": [entry.action for entry in runtime.ledger.tail(6)],
    }


def _serve(args: argparse.Namespace) -> int:
    runtime = _runtime(args)
    app = ControlApp(runtime, log=lambda message: print(message, flush=True))
    print(
        f"{SERVICE_NAME} console on http://{args.host}:{args.port} with data dir {runtime.data_dir}",
        flush=True,
    )
    try:
        serve(app, args.host, args.port)
    except KeyboardInterrupt:
        print("console stopped", flush=True)
    return 0


def _status(runtime: Runtime, args: argparse.Namespace) -> int:
    _emit(runtime.status())
    return 0


def _report(runtime: Runtime, args: argparse.Namespace) -> int:
    if args.unit:
        summary = unit_report(runtime.line(args.unit), runtime.clock.now())
    else:
        summary = site_report(runtime)
    if args.plain:
        block = summary if args.unit is None else {"site": runtime.config.site, "units": [summary]}
        for line in report_lines(block):
            print(line)
        return 0
    _emit(summary)
    return 0


def _tick(runtime: Runtime, args: argparse.Namespace) -> int:
    if args.steps < 1:
        _emit({"ok": False, "error": "steps must be at least one"})
        return 2
    result: dict[str, Any] = {}
    for _ in range(args.steps):
        _advance_clock(runtime, args.seconds)
        result = runtime.tick(args.seconds, "cli")
    _emit(result)
    return 0


def _unit_command(runtime: Runtime, args: argparse.Namespace) -> int:
    line = runtime.line(args.unit)
    moment = runtime.clock.now()
    actor = args.actor
    action = args.action
    if action == "begin":
        _emit(line.begin(moment, actor))
    elif action == "jaw":
        _emit(line.persist_jaw_state(moment, actor))
    elif action == "magnet":
        _emit(line.ready_magnet(moment, actor))
    elif action == "belt":
        _emit(line.start_belt(moment, actor))
    elif action == "screen":
        _emit(line.start_screen(moment, actor))
    elif action == "feed":
        _emit(line.feed(_target_tph(runtime, args), moment, actor))
    elif action == "start":
        _emit(line.start(_target_tph(runtime, args), moment, actor))
    elif action == "stop":
        _emit(line.stop(moment, actor, args.reason))
    elif action == "drain":
        _emit(line.drain(moment, actor))
    elif action == "jaw-stop":
        _emit(line.stop_jaw(moment, actor, args.reason))
    elif action == "cone-stop":
        _emit(line.stop_cone(moment, actor, args.reason))
    elif action == "belt-stop":
        _emit(line.stop_belt(moment, actor, args.reason))
    elif action == "trip":
        _emit(line.emergency_stop(args.reason, moment, actor))
    elif action == "release":
        _emit(line.clear_latch(moment, actor, args.reason, force=args.force))
    elif action == "recover":
        _emit(line.recover(moment, actor))
    elif action == "plan":
        _emit(line.plan_feed(moment, actor))
    elif action == "tick":
        _advance_clock(runtime, args.seconds)
        _emit(line.tick(args.seconds, runtime.clock.now(), actor))
    else:
        _emit(runtime.reset_unit(args.unit, args.reason, actor))
    return 0


def _stage_command(runtime: Runtime, args: argparse.Namespace) -> int:
    line = runtime.line(args.unit)
    moment = runtime.clock.now()
    actor = args.actor
    if args.stage == "jaw-state":
        _emit(line.persist_jaw_state(moment, actor))
    elif args.stage == "magnet-ready":
        _emit(line.ready_magnet(moment, actor))
    elif args.stage == "belt-start":
        _emit(line.start_belt(moment, actor))
    elif args.stage == "screen-start":
        _emit(line.start_screen(moment, actor))
    else:
        _emit(line.feed(_target_tph(runtime, args), moment, actor))
    return 0


def _stop_stage_command(runtime: Runtime, args: argparse.Namespace) -> int:
    line = runtime.line(args.unit)
    moment = runtime.clock.now()
    actor = args.actor
    if args.stage == "feed-stop":
        _emit(line.stop_feed(moment, actor, args.reason))
    elif args.stage == "drain":
        _emit(line.drain(moment, actor))
    elif args.stage == "jaw-stop":
        _emit(line.stop_jaw(moment, actor, args.reason))
    elif args.stage == "cone-stop":
        _emit(line.stop_cone(moment, actor, args.reason))
    else:
        _emit(line.stop_belt(moment, actor, args.reason))
    return 0


def _sample(runtime: Runtime, args: argparse.Namespace) -> int:
    line = runtime.line(args.unit)
    moment = runtime.clock.now()
    actor = args.actor
    kind = args.kind
    if kind == "level":
        _emit(line.sample_level(args.value, moment, actor))
    elif kind == "feed":
        _emit(line.measure_feed(args.value, moment, actor))
    elif kind == "amps":
        _emit(line.sample_amps(args.machine, args.value, moment, actor))
    elif kind == "product":
        if args.second is None:
            _emit({"ok": False, "error": "grading a sample needs --second for the sample mass"})
            return 2
        _emit(line.sample_product(args.value, args.second, moment, actor))
    elif args.force:
        _emit(line.clear_chute(moment, actor, "cli clear", level_pct=args.value, force=True))
    else:
        _emit(line.survey_chute(args.value, moment, actor))
    return 0


def _calibrate(runtime: Runtime, args: argparse.Namespace) -> int:
    line = runtime.line(args.unit)
    moment = runtime.clock.now()
    ttl = args.ttl_s if args.ttl_s is not None else runtime.config.baseline_ttl_s
    pairs = _points(args.point)
    if args.kind == "scale":
        points = [ScalePoint(raw_counts=left, kg_per_m=right) for left, right in pairs]
        _emit(line.calibrate_scale(points, moment, args.actor, ttl))
    else:
        points = [CurrentPoint(tonnes_per_hour=left, amps=right) for left, right in pairs]
        _emit(line.calibrate_cone(points, moment, args.actor, ttl))
    return 0


def _batch(runtime: Runtime, args: argparse.Namespace) -> int:
    moment = runtime.clock.now()
    if args.action == "open":
        if not args.unit:
            _emit({"ok": False, "error": "opening a batch needs --unit"})
            return 2
        code = args.code or runtime.issuer.issue("batch", moment)
        _emit(runtime.batches.open(code, args.unit, args.kind, moment, args.actor).as_dict())
    else:
        if not args.code:
            _emit({"ok": False, "error": "closing a batch needs --code"})
            return 2
        _emit(runtime.batches.close(args.code, moment, args.actor, tonnes=args.tonnes).as_dict())
    return 0


def _audit(runtime: Runtime, args: argparse.Namespace) -> int:
    query = AuditQuery(
        unit=args.unit,
        action=args.action,
        outcome=args.outcome,
        subject=args.subject,
        limit=args.limit,
    )
    entries = runtime.ledger.entries(query)
    _emit(
        {
            "query": query.describe(),
            "count": len(entries),
            "outcomes": runtime.ledger.outcomes(),
            "entries": [entry.as_dict() for entry in entries],
        }
    )
    return 0


def _batches(runtime: Runtime, args: argparse.Namespace) -> int:
    _emit(
        {
            "summary": runtime.batches.summary(),
            "open": [record.as_dict() for record in runtime.batches.open_batches()],
        }
    )
    return 0


def _verdicts(runtime: Runtime, args: argparse.Namespace) -> int:
    verdicts = runtime.verdicts
    _emit(
        {
            "summary": verdicts.summary(),
            "current": {key: entry.as_dict() for key, entry in verdicts.current().items()},
            "history": [entry.as_dict() for entry in verdicts.entries(limit=args.limit)],
        }
    )
    return 0


def _generations(runtime: Runtime, args: argparse.Namespace) -> int:
    _emit(
        {
            "current": runtime.generations.current().as_dict(),
            "history": runtime.generations.history(),
        }
    )
    return 0


def _confirmations(runtime: Runtime, args: argparse.Namespace) -> int:
    register = runtime.confirmations
    _emit({"pending": register.pending(runtime.clock.now()), "all": register.history()})
    return 0


def _baselines(runtime: Runtime, args: argparse.Namespace) -> int:
    store = runtime.baselines
    _emit(
        {
            "subjects": store.subjects(),
            "current": {subject: store.latest(subject).as_dict() for subject in store.subjects()},
            "history": store.history(),
        }
    )
    return 0


def _snapshots(runtime: Runtime, args: argparse.Namespace) -> int:
    store = runtime.snapshots
    _emit(
        {
            "snapshots": [snapshot.as_dict() for snapshot in store.list()],
            "retired": store.history(),
        }
    )
    return 0


def _namespace(runtime: Runtime, args: argparse.Namespace) -> int:
    namespace = runtime.namespace
    _emit(
        {
            "summary": namespace.summary(),
            "by_area": namespace.by_area(),
            "tags": [spec.as_dict() for spec in namespace.specs()],
        }
    )
    return 0


def _issuer(runtime: Runtime, args: argparse.Namespace) -> int:
    issuer = runtime.issuer
    _emit({"site": issuer.site, "counts": issuer.counts(), "codes": issuer.all_codes()})
    return 0


def _store(runtime: Runtime, args: argparse.Namespace) -> int:
    _emit(runtime.store.stats())
    return 0


def _errors(runtime: Runtime, args: argparse.Namespace) -> int:
    _emit({"errors": catalog()})
    return 0


def _sequences(runtime: Runtime, args: argparse.Namespace) -> int:
    line = runtime.line(runtime.unit_ids()[0])
    _emit({"sequences": line.parts.machine.plans(), "unit": line.unit})
    return 0


def _records(runtime: Runtime, args: argparse.Namespace) -> int:
    _emit(
        {
            "summary": runtime.stream.summary(),
            "watermark_history": runtime.stream.watermark_history(args.limit),
            "history": runtime.stream.history()[-args.limit :],
        }
    )
    return 0


def _config(runtime: Runtime, args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {
        "config": runtime.config.as_dict(),
        "envelope": envelope_report(runtime.config),
    }
    if args.compare:
        payload["diff"] = diff_configs(runtime.config, load_config(Path(args.compare)))
    _emit(payload)
    return 0


def _generation(runtime: Runtime, args: argparse.Namespace) -> int:
    _emit(runtime.set_generation(args.reason, args.actor))
    return 0


def _snapshot(runtime: Runtime, args: argparse.Namespace) -> int:
    _emit(runtime.snapshot(args.label, ttl_seconds=args.ttl_s, actor=args.actor))
    return 0


def _restore(runtime: Runtime, args: argparse.Namespace) -> int:
    _emit(runtime.restore_snapshot(args.label, args.actor, args.reason))
    return 0


def _tombstone(runtime: Runtime, args: argparse.Namespace) -> int:
    marker = runtime.stream.tombstone(
        args.sequence,
        runtime.clock.now(),
        args.actor,
        args.reason,
    )
    _emit(marker.as_dict())
    return 0


def _scenario(runtime: Runtime, args: argparse.Namespace) -> int:
    """Run one scripted sequence against its own directory under the data root."""

    result = run_scenario(runtime.config, scenario_run_dir(runtime.data_dir, args.name), args.name)
    if args.out:
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _emit(
        {
            "scenario": result["scenario"],
            "ok": result["ok"],
            "report": result.get("report"),
            "steps": result.get("steps"),
            "error": result.get("error"),
            "audit_tail": [entry["action"] for entry in result.get("audit_tail", [])],
        }
    )
    return 0


COMMANDS: dict[str, Callable[[Runtime, argparse.Namespace], int]] = {
    "status": _status,
    "report": _report,
    "tick": _tick,
    "unit": _unit_command,
    "stage": _stage_command,
    "stop-stage": _stop_stage_command,
    "sample": _sample,
    "calibrate": _calibrate,
    "batch": _batch,
    "audit": _audit,
    "batches": _batches,
    "verdicts": _verdicts,
    "generations": _generations,
    "confirmations": _confirmations,
    "baselines": _baselines,
    "snapshots": _snapshots,
    "namespace": _namespace,
    "issuer": _issuer,
    "store": _store,
    "errors": _errors,
    "sequences": _sequences,
    "records": _records,
    "config": _config,
    "generation": _generation,
    "snapshot": _snapshot,
    "restore": _restore,
    "tombstone": _tombstone,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "status"
    try:
        if command == "serve":
            return _serve(args)
        if command == "selftest":
            _emit(_run_selftest(args, args.target_tph))
            return 0
        if command == "scenario":
            return _scenario(_runtime(args), args)
        handler = COMMANDS.get(command)
        if handler is None:
            parser.error(f"unknown command {command}")
        return handler(_runtime(args), args)
    except CrushError as error:
        print(json.dumps(error.as_payload(), ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


def run() -> int:
    """Console script entry point."""

    return main()
