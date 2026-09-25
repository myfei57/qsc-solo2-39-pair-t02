"""Assembly: turn a configuration into a running site."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .audit.batches import BatchRegistry
from .audit.ledger import OUTCOME_OK, AuditLedger
from .belt.belt import BeltConveyor
from .belt.scale import BeltScale
from .chute.chute import TransferChute
from .chute.detect import BlockageDetector
from .clock import Clock, WallClock
from .cone.calibrate import CurrentCalibration
from .cone.cone import ConeCrusher
from .config import LineSpec, PlantConfig
from .errors import InvalidRequest, RecordNotFound
from .feeder.feeder import Feeder
from .feeder.gate import FeedGate
from .jaw.jaw import JawCrusher
from .jaw.state import JawStateStore
from .level.gauge import LevelGauge
from .level.rate import FeedRatePlanner
from .line.plant import CrushingLine, LineParts
from .line.stage import LineStateMachine
from .magnet.alarm import MagnetAlarm
from .magnet.unit import MagnetSeparator
from .ns.ids import IdIssuer
from .ns.namespace import (
    KIND_BATCH,
    KIND_BELT,
    KIND_BIN,
    KIND_CALIBRATION,
    KIND_CHUTE,
    KIND_CONE,
    KIND_FEEDER,
    KIND_JAW,
    KIND_LATCH,
    KIND_LEVEL,
    KIND_MAGNET,
    KIND_SCREEN,
    KIND_UNIT,
    SignalSpec,
    TagNamespace,
)
from .safety.latch import Latch
from .screen.deck import DeckMonitor
from .screen.screen import VibratingScreen
from .stock.bin import OreBin
from .store.documents import DocumentStore
from .store.generations import BaselineStore, ConfirmationRegister, GenerationRegistry
from .store.journal import RecordJournal
from .store.records import RecordStream, ReplayOutcome
from .store.snapshot import SnapshotStore
from .store.watermark import CommitWatermark
from .verdict.log import VerdictLog

STATE_DIR = "state"
RECORD_DIR = "records"
STREAM_NAME = "operations"
DEFAULT_DATA_DIR = "data"


@dataclass
class Runtime:
    """Everything a request handler, a scenario or a report needs."""

    config: PlantConfig
    data_dir: Path
    clock: Clock
    store: DocumentStore
    stream: RecordStream
    ledger: AuditLedger
    namespace: TagNamespace
    generations: GenerationRegistry
    confirmations: ConfirmationRegister
    baselines: BaselineStore
    snapshots: SnapshotStore
    verdicts: VerdictLog
    batches: BatchRegistry
    issuer: IdIssuer
    lines: dict[str, CrushingLine]
    replay: ReplayOutcome
    projections: dict[str, dict[str, int]] = field(default_factory=dict)

    def unit_ids(self) -> list[str]:
        return sorted(self.lines)

    def line(self, unit: str) -> CrushingLine:
        found = self.lines.get(unit)
        if found is None:
            raise RecordNotFound("unknown unit", unit=unit, known=self.unit_ids())
        return found

    def specs(self) -> dict[str, LineSpec]:
        return {unit: line.spec for unit, line in self.lines.items()}

    def unit_states(self) -> dict[str, str]:
        return {unit: line.state() for unit, line in self.lines.items()}

    def tick(self, seconds: float, actor: str = "control") -> dict[str, Any]:
        """Advance every line by one control cycle.

        A cycle is one committed unit of work: the steps a line takes while it
        runs stay invisible until the whole cycle has finished.
        """

        moment = self.clock.now()
        with self.ledger.batch("site", actor):
            units = [line.tick(seconds, moment, actor) for _, line in sorted(self.lines.items())]
        return {
            "units": units,
            "at": moment.isoformat(),
            "records": self.stream.summary(),
        }

    def health(self) -> dict[str, Any]:
        pending = self.stream.pending()
        records = self.stream.summary()
        return {
            "ok": not pending,
            "units": self.unit_ids(),
            "states": self.unit_states(),
            "generation": self.generations.generation(),
            "records": records,
            "watermark": records["watermark"],
            "pending_records": len(pending),
            "replayed_on_start": self.replay.as_dict(),
            "startup_projections": self.projections,
            "latched": [unit for unit, line in self.lines.items() if line.parts.latch.is_set()],
        }

    def status(self) -> dict[str, Any]:
        return {
            "site": self.config.site,
            "generation": self.generations.generation(),
            "data_dir": str(self.data_dir),
            "clock": self.clock.now().isoformat(),
            "units": {unit: line.status(self.clock.now()) for unit, line in sorted(self.lines.items())},
            "batches": self.batches.summary(),
            "audit": self.ledger.summary(),
            "records": self.stream.summary(),
            "verdicts": self.verdicts.summary(),
            "ids": self.issuer.counts(),
        }

    def snapshot(self, label: str, ttl_seconds: float | None = None, actor: str = "operator") -> dict[str, Any]:
        """Capture the observable point a later restore may return to."""

        return self.snapshots.capture(
            label,
            self.clock.now(),
            generation=self.generations.generation(),
            sequence=self.stream.journal.sequence,
            records=len(self.stream.visible()),
            ttl_seconds=self.config.snapshot_ttl_s if ttl_seconds is None else ttl_seconds,
            actor=actor,
        ).as_dict()

    def restore_snapshot(self, label: str, actor: str, reason: str) -> dict[str, Any]:
        """Roll the watermark back to a snapshot that is still fresh.

        The restore does not append a record of its own: a record written now
        would sit above the snapshot and publishing it would lift the watermark
        straight back.  The move, its actor and its reason are kept in the
        watermark history instead.
        """

        moment = self.clock.now()
        snapshot = self.snapshots.require_fresh(label, moment, self.generations.generation())
        watermark = self.stream.rollback_to(snapshot.sequence, moment, actor, reason)
        return {
            "snapshot": snapshot.as_dict(),
            "watermark": watermark.as_dict(),
            "history": self.stream.watermark_history(3),
        }

    def set_generation(self, reason: str, actor: str = "operator") -> dict[str, Any]:
        """Bump the parameter generation and retire every live feed confirmation.

        A confirmation that was issued for one generation must not release a
        feed setpoint that belongs to the next one, so the bump invalidates the
        slips rather than leaving them to expire on their own.
        """

        moment = self.clock.now()
        state = self.generations.bump(moment, actor, reason)
        invalidated: list[str] = []
        for unit in self.unit_ids():
            subject = self.line(unit).parts.gate.subject
            invalidated.extend(
                self.confirmations.invalidate(subject, f"generation changed: {reason}", moment, actor)
            )
        self.ledger.record(
            "site",
            "config.generation",
            OUTCOME_OK,
            actor,
            moment,
            subject=self.config.site,
            generation=state.generation,
            reason=reason.strip(),
            invalidated=invalidated,
        )
        return {"generation": state.as_dict(), "invalidated": invalidated}

    def reset_unit(self, unit: str, reason: str, actor: str = "operator") -> dict[str, Any]:
        """Clear the stored state of one line, for use after maintenance."""

        if not reason.strip():
            raise InvalidRequest("clearing unit state needs a reason", unit=unit)
        line = self.line(unit)
        cleared: list[str] = []
        for component in line.parts.components():
            doc_id = getattr(component, "doc_id", None)
            if doc_id and self.store.delete(doc_id):
                cleared.append(doc_id)
        self.ledger.record(
            unit,
            "state.cleared",
            OUTCOME_OK,
            actor,
            self.clock.now(),
            subject=unit,
            reason=reason.strip(),
            documents=cleared,
        )
        return {"unit": unit, "cleared": cleared}


def build_namespace(config: PlantConfig) -> TagNamespace:
    """Register every addressable object of the configured site."""

    namespace = TagNamespace()
    specs: list[SignalSpec] = []
    for line in config.lines:
        unit = line.unit
        specs.append(SignalSpec(unit, KIND_UNIT, "", f"line {unit}", "site", unit))
        specs.append(SignalSpec(f"{unit}.machine", KIND_UNIT, "", "line state machine", "line", unit))
        specs.append(SignalSpec(f"{unit}.feeder", KIND_FEEDER, "t/h", "apron feeder setpoint", "feed", unit))
        specs.append(SignalSpec(f"{unit}.jaw", KIND_JAW, "A", "primary crusher draw", "crushing", unit))
        specs.append(SignalSpec(f"{unit}.jaw-state", KIND_JAW, "", "crusher state record", "crushing", unit))
        specs.append(SignalSpec(f"{unit}.cone", KIND_CONE, "A", "secondary crusher draw", "crushing", unit))
        specs.append(SignalSpec(f"{unit}.belt", KIND_BELT, "t/h", "discharge belt tonnage", "conveying", unit))
        specs.append(SignalSpec(f"{unit}.belt-scale", KIND_BELT, "kg/m", "belt scale mapping", "conveying", unit))
        specs.append(SignalSpec(f"{unit}.magnet", KIND_MAGNET, "", "overbelt magnet readiness", "conveying", unit))
        specs.append(SignalSpec(f"{unit}.alarm", KIND_MAGNET, "", "magnet alarm", "conveying", unit))
        specs.append(SignalSpec(f"{unit}.screen", KIND_SCREEN, "", "vibrating screen", "screening", unit))
        specs.append(SignalSpec(f"{unit}.deck.{line.screen_deck_id}", KIND_SCREEN, "t/h", "deck duty", "screening", unit))
        specs.append(SignalSpec(f"{unit}.chute", KIND_CHUTE, "%", "transfer chute level", "screening", unit))
        specs.append(SignalSpec(f"{unit}.bin", KIND_BIN, "%", "buffer bin level", "stock", unit))
        specs.append(SignalSpec(f"{unit}.level", KIND_LEVEL, "%", "bin level instrument", "stock", unit))
        specs.append(SignalSpec(f"{unit}.feed-plan", KIND_LEVEL, "t/h", "feed rate plan", "stock", unit))
        specs.append(SignalSpec(f"{unit}.feed", KIND_LATCH, "", "feed latch", "safety", unit))
        specs.append(SignalSpec(f"{unit}.cone-current", KIND_CALIBRATION, "A/t/h", "cone current line", "crushing", unit))
    specs.append(SignalSpec("line.batches", KIND_BATCH, "", "production batch registry", "line", ""))
    specs.append(SignalSpec("line.records", KIND_BATCH, "", "operation record stream", "line", ""))
    namespace.register_all(specs)
    return namespace


def build_line(
    config: PlantConfig,
    spec: LineSpec,
    store: DocumentStore,
    stream: RecordStream,
    ledger: AuditLedger,
    verdicts: VerdictLog,
    confirmations: ConfirmationRegister,
    generations: GenerationRegistry,
    baselines: BaselineStore,
) -> CrushingLine:
    """Wire one line's components and hand them to the sequence object."""

    unit = spec.unit
    machine = LineStateMachine(unit, store, ledger)
    feeder = Feeder(unit, spec, store, ledger, verdicts)
    jaw = JawCrusher(unit, spec, JawStateStore(unit, store, stream), ledger, verdicts)
    alarm = MagnetAlarm(f"{unit}.magnet", store)
    magnet = MagnetSeparator(
        unit,
        store,
        ledger,
        alarm,
        hold_amps=round(spec.jaw_rated_amps * 0.03, 3),
    )
    belt = BeltConveyor(unit, spec, store, ledger, magnet, verdicts)
    scale = BeltScale(unit, spec.belt_speed_mps, store, ledger, baselines, generations, verdicts)
    decks = DeckMonitor(
        unit,
        spec.screen_deck_id,
        store,
        ledger,
        verdicts,
        capacity_tph=spec.screen_capacity_tph,
        width_m=spec.screen_width_m,
        length_m=spec.screen_length_m,
    )
    screen = VibratingScreen(unit, spec, config.quality, store, ledger, decks, verdicts)
    latch = Latch(f"{unit}.feed", store, hold_seconds=config.safety.latch_hold_s)
    detector = BlockageDetector(
        unit,
        store,
        block_pct=spec.chute_block_pct,
        clear_pct=spec.chute_clear_pct,
        hold_seconds=spec.chute_hold_seconds,
        min_samples=spec.chute_min_samples,
    )
    chute = TransferChute(unit, store, ledger, latch, detector, verdicts)
    calibration = CurrentCalibration(unit, store, ledger, baselines, generations)
    cone = ConeCrusher(unit, spec, store, ledger, calibration, verdicts)
    gauge = LevelGauge(unit, store, ledger, max_age_s=config.stock.level_max_age_s)
    planner = FeedRatePlanner(unit, spec, config.stock, store, ledger, gauge, verdicts)
    bin_state = OreBin(unit, config.stock, store, ledger)
    gate = FeedGate(
        unit,
        spec,
        ledger,
        jaw,
        belt,
        screen,
        cone,
        magnet,
        bin_state,
        latch,
        confirmations,
        generations,
    )
    parts = LineParts(
        spec=spec,
        store=store,
        machine=machine,
        feeder=feeder,
        gate=gate,
        jaw=jaw,
        belt=belt,
        magnet=magnet,
        alarm=alarm,
        decks=decks,
        screen=screen,
        detector=detector,
        chute=chute,
        cone=cone,
        calibration=calibration,
        gauge=gauge,
        planner=planner,
        bin=bin_state,
        latch=latch,
        scale=scale,
        verdicts=verdicts,
        confirmation_ttl_s=config.confirmation_ttl_s,
        drain_seconds=config.safety.drain_seconds,
    )
    return CrushingLine(parts, ledger)


def build_projections() -> dict[str, dict[str, int]]:
    """The counters a restart rebuilds from the committed records."""

    return {"actions": {}, "outcomes": {}, "units": {}, "kinds": {}, "verdicts": {}}


def project_into(projections: dict[str, dict[str, int]]) -> Callable[[Any], None]:
    """Build the replay projection that counts what the stream holds."""

    def project(record: Any) -> None:
        kinds = projections["kinds"]
        kinds[record.kind] = kinds.get(record.kind, 0) + 1
        if record.kind == "verdict":
            verdicts = projections["verdicts"]
            state = str(record.payload.get("state", ""))
            verdicts[state] = verdicts.get(state, 0) + 1
        if record.kind != "audit":
            return
        action = str(record.payload.get("action", ""))
        outcome = str(record.payload.get("outcome", ""))
        actions = projections["actions"]
        outcomes = projections["outcomes"]
        actions[action] = actions.get(action, 0) + 1
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        if record.unit:
            units = projections["units"]
            units[record.unit] = units.get(record.unit, 0) + 1

    return project


def build_runtime(
    config: PlantConfig,
    data_dir: Path | str = DEFAULT_DATA_DIR,
    clock: Clock | None = None,
) -> Runtime:
    """Wire a site, replay anything left above the watermark and return it."""

    chosen = clock or WallClock()
    root = Path(data_dir)
    store = DocumentStore(root / STATE_DIR)
    stream = RecordStream(
        RecordJournal(root / RECORD_DIR / f"{STREAM_NAME}.jsonl"),
        CommitWatermark(store, STREAM_NAME),
    )
    ledger = AuditLedger(stream)
    generations = GenerationRegistry(store)
    verdicts = VerdictLog(stream, lambda: generations.generation())
    projections = build_projections()
    project = project_into(projections)

    already_committed = stream.visible()
    replay = stream.replay(project, chosen.now(), "startup")
    for record in already_committed:
        project(record)

    confirmations = ConfirmationRegister(store)
    baselines = BaselineStore(store)
    snapshots = SnapshotStore(store)
    batches = BatchRegistry(store, ledger)
    issuer = IdIssuer(config.site)
    lines = {
        spec.unit: build_line(
            config,
            spec,
            store,
            stream,
            ledger,
            verdicts,
            confirmations,
            generations,
            baselines,
        )
        for spec in config.lines
    }
    return Runtime(
        config=config,
        data_dir=root,
        clock=chosen,
        store=store,
        stream=stream,
        ledger=ledger,
        namespace=build_namespace(config),
        generations=generations,
        confirmations=confirmations,
        baselines=baselines,
        snapshots=snapshots,
        verdicts=verdicts,
        batches=batches,
        issuer=issuer,
        lines=lines,
        replay=replay,
        projections=projections,
    )
