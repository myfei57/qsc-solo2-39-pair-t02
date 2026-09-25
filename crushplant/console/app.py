"""Route table for the JSON console and the embedded pages."""

from __future__ import annotations

from typing import Any, Callable

from ..audit.query import AuditQuery
from ..belt.scale import ScalePoint
from ..config import diff_configs, envelope_report, load_config
from ..cone.calibrate import CurrentPoint
from ..errors import CrushError, InvalidRequest, catalog
from ..report import lines, site_report, unit_report
from ..runtime import Runtime
from ..scenario import plan_scenarios, run_scenario, scenario_names, scenario_run_dir
from . import pages
from .params import flag, integer, number, optional_text, pairs, query_integer, text
from .response import Response, created, failure, html, ok
from .router import Request, Router

Handler = Callable[[Request], Response]

REFUSAL_CODES = frozenset(
    {
        "ordering-violation",
        "interlock",
        "latched",
        "not-durable",
        "stale",
        "limit-exceeded",
    }
)


class ControlApp:
    """Maps HTTP requests onto the control core."""

    def __init__(self, runtime: Runtime, log: Callable[[str], None] | None = None) -> None:
        self.runtime = runtime
        self._log = log or (lambda message: None)
        self.router = Router()
        self._register_pages()
        self._register_reads()
        self._register_writes()

    # ----------------------------------------------------------------- routing
    def handle(self, request: Request) -> Response:
        try:
            return self.router.dispatch(request)
        except CrushError as error:
            self._note_refusal(request, error)
            return failure(error)
        except Exception as error:  # noqa: BLE001 - the console always answers
            self._log(f"{request.method} {request.path} failed: {error}")
            return failure(error)

    def inventory(self) -> list[dict[str, str]]:
        return self.router.inventory()

    def _note_refusal(self, request: Request, error: CrushError) -> None:
        """Write a refused step down, so a blocked attempt is still auditable."""

        if error.code not in REFUSAL_CODES:
            return
        unit = request.params.get("unit") or request.body.get("unit") or ""
        if not unit:
            return
        self.runtime.ledger.blocked(
            str(unit),
            f"api.{request.method.lower()}.{request.path.strip('/')}",
            optional_text(request.body, "actor", "console"),
            self.runtime.clock.now(),
            error,
            subject=str(unit),
        )

    def _line(self, unit: str):
        return self.runtime.line(unit)

    def _unit(self, request: Request) -> str:
        return request.params["unit"]

    def _register_pages(self) -> None:
        for path, name in pages.page_names():
            renderer = pages.RENDERERS[name]
            self.router.get(path, self._page(renderer), f"{name} page")

    def _page(self, renderer: Callable[[Runtime], str]) -> Handler:
        def handler(request: Request) -> Response:
            return html(renderer(self.runtime))

        return handler

    # ------------------------------------------------------------------- reads
    def _register_reads(self) -> None:
        router = self.router
        router.get("/healthz", self._health, "liveness and record watermark")
        router.get("/api/routes", self._routes, "endpoint inventory")
        router.get("/api/state", self._state, "every line state")
        router.get("/api/errors", self._errors, "error catalogue")
        router.get("/api/status", self._status, "site status")
        router.get("/api/report", self._report, "operator report")
        router.get("/api/config", self._config, "configuration and envelope")
        router.get("/api/namespace", self._namespace, "registered tags")
        router.get("/api/units", self._units, "line list")
        router.get("/api/units/{unit}", self._unit_status, "one line")
        router.get("/api/units/{unit}/report", self._unit_report, "one line, operator view")
        router.get("/api/units/{unit}/gate", self._gate, "feed preconditions")
        router.get("/api/units/{unit}/feeder", self._feeder, "feeder drive")
        router.get("/api/units/{unit}/jaw", self._jaw, "primary crusher")
        router.get("/api/units/{unit}/belt", self._belt, "discharge belt")
        router.get("/api/units/{unit}/magnet", self._magnet, "overbelt magnet")
        router.get("/api/units/{unit}/screen", self._screen, "vibrating screen")
        router.get("/api/units/{unit}/chute", self._chute, "transfer chute")
        router.get("/api/units/{unit}/cone", self._cone, "secondary crusher")
        router.get("/api/units/{unit}/bin", self._bin, "buffer bin")
        router.get("/api/units/{unit}/level", self._level, "level instrument")
        router.get("/api/units/{unit}/plan", self._plan, "feed rate plan")
        router.get("/api/units/{unit}/scale", self._scale, "belt scale mapping")
        router.get("/api/audit", self._audit, "operation ledger")
        router.get("/api/batches", self._batches, "batch registry")
        router.get("/api/records", self._records, "record stream and watermark")
        router.get("/api/verdicts", self._verdicts, "judgements")
        router.get("/api/generations", self._generations, "generation history")
        router.get("/api/confirmations", self._confirmations, "confirmation slips")
        router.get("/api/baselines", self._baselines, "captured baselines")
        router.get("/api/snapshots", self._snapshots, "record snapshots")
        router.get("/api/issuer", self._issuer, "issued identifiers")
        router.get("/api/scenarios", self._scenario_names, "scripted runs")
        router.get("/api/store", self._store, "state documents")

    def _health(self, request: Request) -> Response:
        health = self.runtime.health()
        health["routes"] = len(self.router.routes())
        return ok(health)

    def _routes(self, request: Request) -> Response:
        return ok({"routes": self.inventory(), "count": len(self.router.routes())})

    def _state(self, request: Request) -> Response:
        moment = self.runtime.clock.now()
        return ok({unit: line.status(moment) for unit, line in sorted(self.runtime.lines.items())})

    def _errors(self, request: Request) -> Response:
        entries = catalog()
        return ok({"errors": entries, "count": len(entries)})

    def _status(self, request: Request) -> Response:
        return ok(self.runtime.status())

    def _report(self, request: Request) -> Response:
        report = site_report(self.runtime)
        if request.query.get("plain") in ("1", "true", "yes"):
            return ok({"lines": lines(report), "report": report})
        return ok(report)

    def _config(self, request: Request) -> Response:
        payload: dict[str, Any] = {
            "config": self.runtime.config.as_dict(),
            "envelope": envelope_report(self.runtime.config),
        }
        if request.query.get("compare"):
            payload["diff"] = diff_configs(self.runtime.config, load_config(request.query["compare"]))
        return ok(payload)

    def _namespace(self, request: Request) -> Response:
        namespace = self.runtime.namespace
        return ok(
            {
                "summary": namespace.summary(),
                "by_area": namespace.by_area(),
                "tags": [spec.as_dict() for spec in namespace.specs()],
            }
        )

    def _units(self, request: Request) -> Response:
        return ok({"units": self.runtime.unit_ids()})

    def _unit_status(self, request: Request) -> Response:
        return ok(self._line(self._unit(request)).status(self.runtime.clock.now()))

    def _unit_report(self, request: Request) -> Response:
        return ok(unit_report(self._line(self._unit(request)), self.runtime.clock.now()))

    def _parts(self, request: Request):
        return self._line(self._unit(request)).parts

    def _gate(self, request: Request) -> Response:
        return ok(self._parts(request).gate.preview())

    def _feeder(self, request: Request) -> Response:
        return ok(self._parts(request).feeder.state().as_dict())

    def _jaw(self, request: Request) -> Response:
        parts = self._parts(request)
        return ok({"state": parts.jaw.state().as_dict(), "committed": parts.jaw.committed()})

    def _belt(self, request: Request) -> Response:
        return ok(self._parts(request).belt.state().as_dict())

    def _magnet(self, request: Request) -> Response:
        parts = self._parts(request)
        return ok({"state": parts.magnet.state().as_dict(), "alarm": parts.alarm.state().as_dict()})

    def _screen(self, request: Request) -> Response:
        parts = self._parts(request)
        deck = parts.decks.state()
        return ok({"state": parts.screen.state().as_dict(), "deck": None if deck is None else deck.as_dict()})

    def _chute(self, request: Request) -> Response:
        return ok(self._parts(request).chute.state(self.runtime.clock.now()))

    def _cone(self, request: Request) -> Response:
        parts = self._parts(request)
        return ok({"state": parts.cone.state().as_dict(), "calibration": parts.calibration.summary()})

    def _bin(self, request: Request) -> Response:
        return ok(self._parts(request).bin.state().as_dict())

    def _level(self, request: Request) -> Response:
        return ok(self._parts(request).gauge.state(self.runtime.clock.now()))

    def _plan(self, request: Request) -> Response:
        return ok(self._parts(request).planner.check(self.runtime.clock.now()))

    def _scale(self, request: Request) -> Response:
        return ok(self._parts(request).scale.mapping())

    def _audit(self, request: Request) -> Response:
        query = AuditQuery.from_mapping(dict(request.query))
        entries = self.runtime.ledger.entries(query)
        return ok(
            {
                "query": query.describe(),
                "entries": [entry.as_dict() for entry in entries],
                "count": len(entries),
                "outcomes": self.runtime.ledger.outcomes(),
            }
        )

    def _batches(self, request: Request) -> Response:
        return ok(
            {
                "summary": self.runtime.batches.summary(),
                "open": [record.as_dict() for record in self.runtime.batches.open_batches()],
            }
        )

    def _records(self, request: Request) -> Response:
        limit = query_integer(request.query, "limit", 20)
        return ok(
            {
                "summary": self.runtime.stream.summary(),
                "watermark_history": self.runtime.stream.watermark_history(limit),
                "history": self.runtime.stream.history()[-limit:],
            }
        )

    def _verdicts(self, request: Request) -> Response:
        verdicts = self.runtime.verdicts
        return ok(
            {
                "summary": verdicts.summary(),
                "current": {key: entry.as_dict() for key, entry in verdicts.current().items()},
                "history": [entry.as_dict() for entry in verdicts.entries(limit=20)],
            }
        )

    def _generations(self, request: Request) -> Response:
        return ok(
            {
                "current": self.runtime.generations.current().as_dict(),
                "history": self.runtime.generations.history(),
            }
        )

    def _confirmations(self, request: Request) -> Response:
        register = self.runtime.confirmations
        return ok({"pending": register.pending(self.runtime.clock.now()), "all": register.history()})

    def _baselines(self, request: Request) -> Response:
        store = self.runtime.baselines
        return ok(
            {
                "subjects": store.subjects(),
                "current": {subject: store.latest(subject).as_dict() for subject in store.subjects()},
                "history": store.history(),
            }
        )

    def _snapshots(self, request: Request) -> Response:
        store = self.runtime.snapshots
        return ok(
            {
                "snapshots": [snapshot.as_dict() for snapshot in store.list()],
                "retired": store.history(),
            }
        )

    def _issuer(self, request: Request) -> Response:
        issuer = self.runtime.issuer
        return ok({"site": issuer.site, "counts": issuer.counts(), "codes": issuer.all_codes()})

    def _scenario_names(self, request: Request) -> Response:
        return ok({"scenarios": scenario_names(), "sequences": plan_scenarios()})

    def _store(self, request: Request) -> Response:
        return ok(self.runtime.store.stats())

    # ------------------------------------------------------------------ writes
    def _register_writes(self) -> None:
        router = self.router
        router.post("/api/feeder/start", self._feeder_start, "gated feed setpoint")
        router.post("/api/feeder/stop", self._feeder_stop, "stop the feeder")
        router.post("/api/belt/start", self._belt_start, "start the discharge belt")
        router.post("/api/screen/start", self._screen_start, "start the screen")
        router.post("/api/magnet/trip", self._magnet_trip, "trip the magnet")
        router.post("/api/magnet/reset", self._magnet_reset, "acknowledge and reset the magnet")
        router.post("/api/units/{unit}/begin", self._begin, "open a new cycle")
        router.post("/api/units/{unit}/start", self._start, "walk the whole bring-up order")
        router.post("/api/units/{unit}/stage", self._stage, "run one bring-up step")
        router.post("/api/units/{unit}/stop", self._stop, "walk the whole shutdown order")
        router.post("/api/units/{unit}/stop-stage", self._stop_stage, "run one shutdown step")
        router.post("/api/units/{unit}/confirm", self._confirm, "issue a feed confirmation")
        router.post("/api/units/{unit}/level", self._level_sample, "record a bin level")
        router.post("/api/units/{unit}/bin", self._bin_move, "draw from or fill the bin")
        router.post("/api/units/{unit}/measure", self._measure, "record a feeder rate")
        router.post("/api/units/{unit}/weigh", self._weigh, "map a belt scale reading")
        router.post("/api/units/{unit}/chute", self._chute_survey, "record a chute level")
        router.post("/api/units/{unit}/chute/clear", self._chute_clear, "clear the chute and release")
        router.post("/api/units/{unit}/product", self._product, "grade a product sample")
        router.post("/api/units/{unit}/amps", self._amps, "record a crusher draw")
        router.post("/api/units/{unit}/calibrate", self._calibrate, "calibrate a line baseline")
        router.post("/api/units/{unit}/plan", self._plan_feed, "work out the feed setpoint")
        router.post("/api/units/{unit}/tick", self._tick_unit, "advance one line")
        router.post("/api/units/{unit}/emergency-stop", self._emergency_stop, "run the emergency order")
        router.post("/api/units/{unit}/latch-release", self._latch_release, "release the feed latch")
        router.post("/api/units/{unit}/recover", self._recover, "return a tripped line to standby")
        router.post("/api/units/{unit}/reset", self._reset_unit, "clear the stored line state")
        router.post("/api/tick", self._tick_site, "advance every line")
        router.post("/api/batches/open", self._batch_open, "open a production batch")
        router.post("/api/batches/close", self._batch_close, "close a production batch")
        router.post("/api/generation", self._set_generation, "bump the parameter generation")
        router.post("/api/snapshots", self._snapshot, "capture a record snapshot")
        router.post("/api/records/restore", self._restore, "roll the watermark back")
        router.post("/api/records/tombstone", self._tombstone, "void a committed record")
        router.post("/api/scenarios/run", self._run_scenario, "run a scripted scenario")

    def _feeder_start(self, request: Request) -> Response:
        unit = text(request.body, "unit")
        target = number(request.body, "target_tph")
        return created(self._line(unit).feed(target, self.runtime.clock.now(), self._actor(request)))

    def _feeder_stop(self, request: Request) -> Response:
        unit = text(request.body, "unit")
        reason = optional_text(request.body, "reason", "console request")
        return ok(self._line(unit).stop_feed(self.runtime.clock.now(), self._actor(request), reason))

    def _belt_start(self, request: Request) -> Response:
        unit = text(request.body, "unit")
        return created(self._line(unit).start_belt(self.runtime.clock.now(), self._actor(request)))

    def _screen_start(self, request: Request) -> Response:
        unit = text(request.body, "unit")
        return created(self._line(unit).start_screen(self.runtime.clock.now(), self._actor(request)))

    def _magnet_trip(self, request: Request) -> Response:
        unit = text(request.body, "unit")
        reason = text(request.body, "reason")
        return ok(self._line(unit).trip_magnet(reason, self.runtime.clock.now(), self._actor(request)))

    def _magnet_reset(self, request: Request) -> Response:
        unit = text(request.body, "unit")
        reason = text(request.body, "reason")
        return ok(self._line(unit).reset_magnet(self.runtime.clock.now(), self._actor(request), reason))

    def _actor(self, request: Request) -> str:
        return optional_text(request.body, "actor", "console")

    def _begin(self, request: Request) -> Response:
        unit = self._unit(request)
        return created(self._line(unit).begin(self.runtime.clock.now(), self._actor(request)))

    def _start(self, request: Request) -> Response:
        unit = self._unit(request)
        target = number(request.body, "target_tph")
        return created(self._line(unit).start(target, self.runtime.clock.now(), self._actor(request)))

    def _stage(self, request: Request) -> Response:
        unit = self._unit(request)
        line = self._line(unit)
        moment = self.runtime.clock.now()
        actor = self._actor(request)
        stage = text(request.body, "stage")
        if stage == "jaw-state":
            return created(line.persist_jaw_state(moment, actor))
        if stage == "magnet-ready":
            return created(line.ready_magnet(moment, actor))
        if stage == "belt-start":
            return created(line.start_belt(moment, actor))
        if stage == "screen-start":
            return created(line.start_screen(moment, actor))
        if stage == "cone-start":
            return created(line.start_cone(moment, actor))
        if stage == "feed":
            return created(line.feed(number(request.body, "target_tph"), moment, actor))
        raise InvalidRequest("that stage is not part of the bring-up order", stage=stage)

    def _stop(self, request: Request) -> Response:
        unit = self._unit(request)
        reason = optional_text(request.body, "reason", "console request")
        return ok(self._line(unit).stop(self.runtime.clock.now(), self._actor(request), reason))

    def _stop_stage(self, request: Request) -> Response:
        unit = self._unit(request)
        line = self._line(unit)
        moment = self.runtime.clock.now()
        actor = self._actor(request)
        reason = optional_text(request.body, "reason", "console request")
        stage = text(request.body, "stage")
        handlers: dict[str, Callable[[], dict[str, Any]]] = {
            "feed-stop": lambda: line.stop_feed(moment, actor, reason),
            "drain": lambda: line.drain(moment, actor),
            "jaw-stop": lambda: line.stop_jaw(moment, actor, reason),
            "cone-stop": lambda: line.stop_cone(moment, actor, reason),
            "belt-stop": lambda: line.stop_belt(moment, actor, reason),
        }
        handler = handlers.get(stage)
        if handler is None:
            raise InvalidRequest("that stage is not part of the shutdown order", stage=stage)
        return created(handler())

    def _confirm(self, request: Request) -> Response:
        unit = self._unit(request)
        target = number(request.body, "target_tph")
        ttl = number(request.body, "ttl_s", self.runtime.config.confirmation_ttl_s)
        return created(self._line(unit).confirm(target, ttl, self.runtime.clock.now(), self._actor(request)))

    def _level_sample(self, request: Request) -> Response:
        unit = self._unit(request)
        level = number(request.body, "level_pct")
        return created(self._line(unit).sample_level(level, self.runtime.clock.now(), self._actor(request)))

    def _bin_move(self, request: Request) -> Response:
        unit = self._unit(request)
        line = self._line(unit)
        moment = self.runtime.clock.now()
        actor = self._actor(request)
        tonnes = number(request.body, "tonnes")
        operation = optional_text(request.body, "op", "draw")
        if operation == "draw":
            return ok(line.draw_bin(tonnes, moment, actor))
        if operation == "fill":
            return ok(line.fill_bin(tonnes, moment, actor))
        raise InvalidRequest("a bin move is a draw or a fill", op=operation)

    def _measure(self, request: Request) -> Response:
        unit = self._unit(request)
        tonnes_per_hour = number(request.body, "tonnes_per_hour")
        return ok(self._line(unit).measure_feed(tonnes_per_hour, self.runtime.clock.now(), self._actor(request)))

    def _weigh(self, request: Request) -> Response:
        unit = self._unit(request)
        raw = number(request.body, "raw_counts")
        seconds = number(request.body, "seconds", 0.0)
        return ok(self._line(unit).weigh(raw, self.runtime.clock.now(), self._actor(request), seconds))

    def _chute_survey(self, request: Request) -> Response:
        unit = self._unit(request)
        level = number(request.body, "level_pct")
        return ok(self._line(unit).survey_chute(level, self.runtime.clock.now(), self._actor(request)))

    def _chute_clear(self, request: Request) -> Response:
        unit = self._unit(request)
        reason = text(request.body, "reason")
        level = number(request.body, "level_pct", 0.0)
        force = flag(request.body, "force", False)
        return ok(
            self._line(unit).clear_chute(
                self.runtime.clock.now(),
                self._actor(request),
                reason,
                level_pct=level,
                force=force,
            )
        )

    def _product(self, request: Request) -> Response:
        unit = self._unit(request)
        undersize = number(request.body, "undersize_t")
        total = number(request.body, "total_t")
        return ok(self._line(unit).sample_product(undersize, total, self.runtime.clock.now(), self._actor(request)))

    def _amps(self, request: Request) -> Response:
        unit = self._unit(request)
        machine = optional_text(request.body, "machine", "jaw")
        amps = number(request.body, "amps")
        return ok(self._line(unit).sample_amps(machine, amps, self.runtime.clock.now(), self._actor(request)))

    def _calibrate(self, request: Request) -> Response:
        unit = self._unit(request)
        line = self._line(unit)
        moment = self.runtime.clock.now()
        actor = self._actor(request)
        ttl = number(request.body, "ttl_s", self.runtime.config.baseline_ttl_s)
        kind = optional_text(request.body, "kind", "scale")
        if kind == "scale":
            raw = pairs(request.body, "points", "raw_counts", "kg_per_m")
            points = [ScalePoint(raw_counts=item["raw_counts"], kg_per_m=item["kg_per_m"]) for item in raw]
            return created(line.calibrate_scale(points, moment, actor, ttl))
        if kind == "cone":
            raw = pairs(request.body, "points", "tonnes_per_hour", "amps")
            points = [CurrentPoint(tonnes_per_hour=item["tonnes_per_hour"], amps=item["amps"]) for item in raw]
            return created(line.calibrate_cone(points, moment, actor, ttl))
        raise InvalidRequest("that baseline cannot be calibrated here", kind=kind)

    def _plan_feed(self, request: Request) -> Response:
        unit = self._unit(request)
        return created(self._line(unit).plan_feed(self.runtime.clock.now(), self._actor(request)))

    def _tick_unit(self, request: Request) -> Response:
        unit = self._unit(request)
        seconds = number(request.body, "seconds", 30.0)
        self._advance(seconds)
        return ok(self._line(unit).tick(seconds, self.runtime.clock.now(), self._actor(request)))

    def _tick_site(self, request: Request) -> Response:
        seconds = number(request.body, "seconds", 30.0)
        self._advance(seconds)
        return ok(self.runtime.tick(seconds, self._actor(request)))

    def _advance(self, seconds: float) -> None:
        """Move a simulated clock forward; wall time moves on its own."""

        try:
            self.runtime.clock.advance(seconds)
        except RuntimeError:
            return

    def _emergency_stop(self, request: Request) -> Response:
        unit = self._unit(request)
        reason = text(request.body, "reason")
        return ok(self._line(unit).emergency_stop(reason, self.runtime.clock.now(), self._actor(request)))

    def _latch_release(self, request: Request) -> Response:
        unit = self._unit(request)
        reason = text(request.body, "reason")
        force = flag(request.body, "force", False)
        return ok(
            self._line(unit).clear_latch(
                self.runtime.clock.now(),
                self._actor(request),
                reason,
                force=force,
            )
        )

    def _recover(self, request: Request) -> Response:
        unit = self._unit(request)
        return ok(self._line(unit).recover(self.runtime.clock.now(), self._actor(request)))

    def _reset_unit(self, request: Request) -> Response:
        unit = self._unit(request)
        reason = optional_text(request.body, "reason", "maintenance handback")
        return ok(self.runtime.reset_unit(unit, reason, self._actor(request)))

    def _batch_open(self, request: Request) -> Response:
        unit = text(request.body, "unit")
        kind = optional_text(request.body, "kind", "production")
        moment = self.runtime.clock.now()
        code = optional_text(request.body, "code") or self.runtime.issuer.issue("batch", moment)
        notes = optional_text(request.body, "notes")
        return created(
            self.runtime.batches.open(code, unit, kind, moment, self._actor(request), notes=notes).as_dict()
        )

    def _batch_close(self, request: Request) -> Response:
        code = text(request.body, "code")
        tonnes = request.body.get("tonnes")
        return ok(
            self.runtime.batches.close(
                code,
                self.runtime.clock.now(),
                self._actor(request),
                tonnes=None if tonnes is None else number(request.body, "tonnes"),
            ).as_dict()
        )

    def _set_generation(self, request: Request) -> Response:
        reason = text(request.body, "reason")
        return ok(self.runtime.set_generation(reason, self._actor(request)))

    def _snapshot(self, request: Request) -> Response:
        label = text(request.body, "label")
        ttl = request.body.get("ttl_s")
        return created(
            self.runtime.snapshot(
                label,
                ttl_seconds=None if ttl is None else number(request.body, "ttl_s"),
                actor=self._actor(request),
            )
        )

    def _restore(self, request: Request) -> Response:
        label = text(request.body, "label")
        reason = optional_text(request.body, "reason", "operator restore")
        return ok(self.runtime.restore_snapshot(label, self._actor(request), reason))

    def _tombstone(self, request: Request) -> Response:
        sequence = integer(request.body, "sequence")
        reason = text(request.body, "reason")
        marker = self.runtime.stream.tombstone(
            sequence,
            self.runtime.clock.now(),
            self._actor(request),
            reason,
        )
        return ok(marker.as_dict())

    def _run_scenario(self, request: Request) -> Response:
        name = optional_text(request.body, "name", "cold-start")
        if name not in scenario_names():
            raise InvalidRequest("that scenario does not exist", name=name)
        # Every run gets its own directory, so a second run never starts on top
        # of the first one's state.
        run_dir = scenario_run_dir(self.runtime.data_dir, name)
        result = run_scenario(
            self.runtime.config,
            run_dir,
            name,
            actor=self._actor(request),
        )
        return ok(
            {
                "scenario": result["scenario"],
                "ok": result["ok"],
                "data_dir": str(run_dir),
                "report": result.get("report"),
                "steps": result.get("steps"),
                "error": result.get("error"),
            }
        )
