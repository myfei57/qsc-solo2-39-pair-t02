"""One crushing line: the components, the sequence and the interlocks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from ..audit.ledger import OUTCOME_OK, OUTCOME_TRIPPED, AuditLedger
from ..belt.belt import BeltConveyor
from ..belt.scale import BeltScale
from ..chute.chute import TransferChute
from ..chute.detect import BlockageDetector
from ..cone.calibrate import CurrentCalibration
from ..cone.cone import ConeCrusher
from ..config import LineSpec
from ..errors import InvalidRequest, OrderingViolation
from ..feeder.feeder import Feeder
from ..feeder.gate import FeedGate
from ..jaw.jaw import JawCrusher
from ..level.gauge import LevelGauge
from ..level.rate import FeedRatePlanner
from ..magnet.alarm import MagnetAlarm
from ..magnet.unit import MagnetSeparator
from ..safety.latch import Latch
from ..screen.deck import DeckMonitor
from ..screen.screen import VibratingScreen
from ..stock.bin import OreBin
from ..store.documents import DocumentStore
from ..verdict.log import VerdictLog
from .stage import RUNNING, LineStateMachine


@dataclass
class LineParts:
    """Every component one line is built from."""

    spec: LineSpec
    store: DocumentStore
    machine: LineStateMachine
    feeder: Feeder
    gate: FeedGate
    jaw: JawCrusher
    belt: BeltConveyor
    magnet: MagnetSeparator
    alarm: MagnetAlarm
    decks: DeckMonitor
    screen: VibratingScreen
    detector: BlockageDetector
    chute: TransferChute
    cone: ConeCrusher
    calibration: CurrentCalibration
    gauge: LevelGauge
    planner: FeedRatePlanner
    bin: OreBin
    latch: Latch
    scale: BeltScale
    verdicts: VerdictLog
    confirmation_ttl_s: float
    drain_seconds: float

    def components(self) -> list[Any]:
        """Every part that keeps a state document of its own."""

        return [
            self.feeder,
            self.jaw,
            self.belt,
            self.magnet,
            self.alarm,
            self.decks,
            self.screen,
            self.chute,
            self.cone,
            self.calibration,
            self.gauge,
            self.planner,
            self.bin,
            self.latch,
            self.machine,
        ]


class CrushingLine:
    """Runs one line: the bring-up order, the shutdown order and the interlocks.

    Nothing in here reaches into a component's state document.  Each step asks
    its component to do the work and then records the stage, which is what makes
    the ordering and the durability rules hold no matter who calls them.
    """

    def __init__(self, parts: LineParts, ledger: AuditLedger) -> None:
        self.parts = parts
        self._ledger = ledger

    @property
    def unit(self) -> str:
        return self.parts.spec.unit

    @property
    def spec(self) -> LineSpec:
        return self.parts.spec

    def state(self) -> str:
        return self.parts.machine.state()

    def progress(self, label: str = "start") -> dict[str, Any]:
        return self.parts.machine.progress(label)

    def status(self, moment: datetime) -> dict[str, Any]:
        """A compact view of the whole line, the way a panel would show it."""

        return {
            "unit": self.unit,
            "machine": {
                "state": self.parts.machine.state(),
                "cycle": self.parts.machine.cycle(),
                "stages": self.parts.machine.stages(),
                "start": self.parts.machine.progress("start"),
                "stop": self.parts.machine.progress("stop"),
                "plans": self.parts.machine.plans(),
            },
            "feeder": self.parts.feeder.state().as_dict(),
            "jaw": self.parts.jaw.state().as_dict(),
            "belt": self.parts.belt.state().as_dict(),
            "magnet": self.parts.magnet.state().as_dict(),
            "alarm": self.parts.alarm.state().as_dict(),
            "screen": self.parts.screen.state().as_dict(),
            "deck": None if self.parts.decks.state() is None else self.parts.decks.state().as_dict(),
            "chute": self.parts.chute.state(moment)["blockage"],
            "cone": self.parts.cone.state().as_dict(),
            "calibration": self.parts.calibration.summary(),
            "scale": self.parts.scale.mapping(),
            "level": self.parts.gauge.state(moment),
            "plan": self.parts.planner.state(),
            "bin": self.parts.bin.state().as_dict(),
            "latch": self.parts.latch.state().as_dict(),
            "gate": self.parts.gate.preview(),
        }

    def _require_next(self, label: str, step: str) -> None:
        """Refuse a step that is not the next one the plan expects."""

        progress = self.parts.machine.progress(label)
        outstanding = progress["outstanding"]
        if step in progress["completed"]:
            raise OrderingViolation(
                "that step has already run in this cycle",
                unit=self.unit,
                sequence=label,
                step=step,
            )
        expected = outstanding[0] if outstanding else None
        if step != expected:
            raise OrderingViolation(
                "that step is out of order",
                unit=self.unit,
                sequence=label,
                step=step,
                expected=expected,
                completed=progress["completed"],
            )

    # ------------------------------------------------------------ bring-up
    def begin(self, moment: datetime, actor: str) -> dict[str, Any]:
        """Open a new cycle on a line that is standing by."""

        opened = self.parts.machine.open_cycle(moment, actor)
        self._ledger.record(
            self.unit,
            "line.begin",
            OUTCOME_OK,
            actor,
            moment,
            subject=self.unit,
            cycle=opened["cycle"],
        )
        return opened

    def persist_jaw_state(self, moment: datetime, actor: str) -> dict[str, Any]:
        """First step: the crusher runs and its state reaches the journal."""

        self._require_next("start", "jaw-state")
        state = self.parts.jaw.start(moment, actor)
        stage = self.parts.machine.complete_start_stage("jaw-state", moment, actor)
        return {"jaw": state.as_dict(), "stage": stage.as_dict()}

    def ready_magnet(self, moment: datetime, actor: str) -> dict[str, Any]:
        """Second step: the magnet is energised and declares itself ready."""

        self._require_next("start", "magnet-ready")
        state = self.parts.magnet.ready_up(moment, actor)
        stage = self.parts.machine.complete_start_stage("magnet-ready", moment, actor)
        return {"magnet": state.as_dict(), "stage": stage.as_dict()}

    def start_belt(self, moment: datetime, actor: str) -> dict[str, Any]:
        """Third step: the discharge belt, which the magnet has to allow."""

        self._require_next("start", "belt-start")
        state = self.parts.belt.start(moment, actor)
        stage = self.parts.machine.complete_start_stage("belt-start", moment, actor)
        return {"belt": state.as_dict(), "stage": stage.as_dict()}

    def start_screen(self, moment: datetime, actor: str) -> dict[str, Any]:
        """Fourth step: the screen has to run before any ore may be fed."""

        self._require_next("start", "screen-start")
        state = self.parts.screen.start(moment, actor)
        stage = self.parts.machine.complete_start_stage("screen-start", moment, actor)
        return {"screen": state.as_dict(), "stage": stage.as_dict()}

    def start_cone(self, moment: datetime, actor: str) -> dict[str, Any]:
        """Fifth step: the secondary crusher, which needs a fresh calibration."""

        self._require_next("start", "cone-start")
        state = self.parts.cone.start(moment, actor)
        stage = self.parts.machine.complete_start_stage("cone-start", moment, actor)
        return {"cone": state.as_dict(), "stage": stage.as_dict()}

    def feed(self, target_tph: float, moment: datetime, actor: str) -> dict[str, Any]:
        """Fifth step: the gated feed setpoint.

        The order here is deliberate.  The crusher state has to be durable, the
        cross component preconditions have to hold, the confirmation has to be
        live at the current generation -- and is consumed as it releases this
        step, so one slip never feeds twice -- and only then is the setpoint
        judged and the feeder started.
        """

        self._require_next("start", "feed")
        self.parts.jaw.require_persisted("feed")
        self.parts.gate.require(moment, actor)
        slip = self.parts.gate.consume_confirmation(moment, actor)
        verdict = self.parts.gate.judge(target_tph, moment)
        state = self.parts.feeder.start(target_tph, moment, actor)
        stage = self.parts.machine.complete_start_stage("feed", moment, actor)
        return {
            "feeder": state.as_dict(),
            "slip": slip,
            "target": verdict,
            "state": self.parts.machine.state(),
            "stage": stage.as_dict(),
        }

    def start(self, target_tph: float, moment: datetime, actor: str) -> dict[str, Any]:
        """Walk the whole bring-up order and report what each step produced."""

        steps = [self.begin(moment, actor)]
        steps.append(self.persist_jaw_state(moment, actor))
        steps.append(self.ready_magnet(moment, actor))
        steps.append(self.start_belt(moment, actor))
        steps.append(self.start_screen(moment, actor))
        steps.append(self.start_cone(moment, actor))
        feed = self.feed(target_tph, moment, actor)
        return {
            "unit": self.unit,
            "target_tph": target_tph,
            "steps": steps,
            "feed": feed,
            "state": self.parts.machine.state(),
            "cycle": self.parts.machine.cycle(),
        }

    # ----------------------------------------------------------- shutdown
    def stop_feed(self, moment: datetime, actor: str, reason: str = "shutdown sequence") -> dict[str, Any]:
        """First step down: the feeder stops before anything else moves."""

        self._require_next("stop", "feed-stop")
        state = self.parts.feeder.stop(moment, actor, reason)
        stage = self.parts.machine.complete_stop_stage("feed-stop", moment, actor)
        return {"feeder": state.as_dict(), "stage": stage.as_dict()}

    def drain(self, moment: datetime, actor: str) -> dict[str, Any]:
        """Second step: stop the screen and let the belt empty itself."""

        self._require_next("stop", "drain")
        screen = self.parts.screen.stop(moment, actor, "drain")
        stage = self.parts.machine.complete_stop_stage("drain", moment, actor)
        self._ledger.record(
            self.unit,
            "line.drain",
            OUTCOME_OK,
            actor,
            moment,
            subject=self.unit,
            seconds=self.parts.drain_seconds,
        )
        return {"screen": screen.as_dict(), "drain_seconds": self.parts.drain_seconds, "stage": stage.as_dict()}

    def stop_jaw(self, moment: datetime, actor: str, reason: str = "shutdown sequence") -> dict[str, Any]:
        """Third step: only a line that has stopped feeding may stop the crusher."""

        self._require_next("stop", "jaw-stop")
        state = self.parts.jaw.stop(moment, actor, reason)
        stage = self.parts.machine.complete_stop_stage("jaw-stop", moment, actor)
        return {"jaw": state.as_dict(), "stage": stage.as_dict()}

    def stop_cone(self, moment: datetime, actor: str, reason: str = "shutdown sequence") -> dict[str, Any]:
        """Fourth step: the secondary crusher follows the primary."""

        self._require_next("stop", "cone-stop")
        state = self.parts.cone.stop(moment, actor, reason)
        stage = self.parts.machine.complete_stop_stage("cone-stop", moment, actor)
        return {"cone": state.as_dict(), "stage": stage.as_dict()}

    def stop_belt(self, moment: datetime, actor: str, reason: str = "shutdown sequence") -> dict[str, Any]:
        """Last step: the discharge belt goes down once nothing is coming off it."""

        self._require_next("stop", "belt-stop")
        state = self.parts.belt.stop(moment, actor, reason)
        stage = self.parts.machine.complete_stop_stage("belt-stop", moment, actor)
        return {"belt": state.as_dict(), "stage": stage.as_dict()}

    def stop(self, moment: datetime, actor: str, reason: str = "shutdown sequence") -> dict[str, Any]:
        """Walk the whole shutdown order and return the line to standby."""

        opened = self.parts.machine.begin_stop(moment, actor)
        steps = [
            self.stop_feed(moment, actor, reason),
            self.drain(moment, actor),
            self.stop_jaw(moment, actor, reason),
            self.stop_cone(moment, actor, reason),
            self.stop_belt(moment, actor, reason),
        ]
        closed = self.parts.machine.close_cycle(moment, actor)
        return {
            "unit": self.unit,
            "began": opened,
            "steps": [step["stage"]["stage"] for step in steps],
            "state": closed["state"],
            "cycle": closed["cycle"],
            "tonnage": self.parts.feeder.total_tonnes(),
        }

    # ------------------------------------------------------------ emergency
    def emergency_stop(self, reason: str, moment: datetime, actor: str) -> dict[str, Any]:
        """The emergency order: cut the feed, shed the belt, then the machines."""

        if not reason.strip():
            raise InvalidRequest("an emergency stop needs a reason", unit=self.unit)
        tripped = self.parts.machine.trip(reason, moment, actor)
        self.parts.latch.trip(reason, moment, actor)
        done: list[str] = []
        if self.parts.feeder.running():
            self.parts.feeder.stop(moment, actor, reason)
        done.append(self.parts.machine.complete_stop_stage("feed-stop", moment, actor).stage)
        if self.parts.belt.running():
            self.parts.belt.stop(moment, actor, reason)
        done.append(self.parts.machine.complete_stop_stage("belt-stop", moment, actor).stage)
        if self.parts.jaw.running():
            self.parts.jaw.stop(moment, actor, reason)
        done.append(self.parts.machine.complete_stop_stage("jaw-stop", moment, actor).stage)
        if self.parts.cone.running():
            self.parts.cone.stop(moment, actor, reason)
        done.append(self.parts.machine.complete_stop_stage("cone-stop", moment, actor).stage)
        self._ledger.record(
            self.unit,
            "line.emergency-stop",
            OUTCOME_TRIPPED,
            actor,
            moment,
            subject=self.unit,
            reason=reason.strip(),
            steps=done,
        )
        done.append(self.parts.machine.complete_stop_stage("audit", moment, actor).stage)
        return {"trip": tripped, "steps": done, "latch": self.parts.latch.state().as_dict()}

    def clear_latch(self, moment: datetime, actor: str, reason: str, *, force: bool = False) -> dict[str, Any]:
        """Release the protective latch once its condition has been dealt with."""

        released = self.parts.latch.release(moment, actor, reason, force=force)
        self._ledger.record(
            self.unit,
            "line.latch-release",
            OUTCOME_OK,
            actor,
            moment,
            subject=self.unit,
            reason=reason.strip(),
            forced=force,
        )
        return released.as_dict()

    def recover(self, moment: datetime, actor: str) -> dict[str, Any]:
        """Return a tripped line to standby once the emergency sequence is done."""

        recovered = self.parts.machine.recover(moment, actor)
        return recovered

    # -------------------------------------------------------- running the line
    def confirm(self, target_tph: float, ttl_seconds: float, moment: datetime, actor: str) -> dict[str, Any]:
        """Issue the confirmation slip the feed step insists on."""

        slip = self.parts.gate.issue_confirmation(ttl_seconds, moment, actor, target_tph=target_tph)
        self._ledger.record(
            self.unit,
            "gate.confirm",
            OUTCOME_OK,
            actor,
            moment,
            subject=self.parts.gate.subject,
            slip_id=slip["slip_id"],
            target_tph=target_tph,
        )
        return slip

    def load_bin(self, level_pct: float, moment: datetime, actor: str) -> dict[str, Any]:
        return self.parts.bin.load(level_pct, moment, actor).as_dict()

    def draw_bin(self, tonnes: float, moment: datetime, actor: str) -> dict[str, Any]:
        return self.parts.bin.draw(tonnes, moment, actor).as_dict()

    def fill_bin(self, tonnes: float, moment: datetime, actor: str) -> dict[str, Any]:
        return self.parts.bin.fill(tonnes, moment, actor).as_dict()

    def sample_level(self, level_pct: float, moment: datetime, actor: str) -> dict[str, Any]:
        """Take a level reading and mirror it onto the bin."""

        reading = self.parts.gauge.sample(level_pct, moment, actor)
        bin_state = self.parts.bin.load(level_pct, moment, actor)
        return {"reading": reading.as_dict(), "bin": bin_state.as_dict()}

    def plan_feed(self, moment: datetime, actor: str) -> dict[str, Any]:
        """Work out the setpoint the bin level asks for."""

        return self.parts.planner.plan(moment, actor)

    def measure_feed(self, tonnes_per_hour: float, moment: datetime, actor: str) -> dict[str, Any]:
        return self.parts.feeder.measure(tonnes_per_hour, moment, actor)

    def weigh(self, raw_counts: float, moment: datetime, actor: str, seconds: float = 0.0) -> dict[str, Any]:
        """Map one belt scale reading and, if asked, carry the tonnage forward."""

        reading = self.parts.scale.read(raw_counts, moment, actor)
        belt = self.parts.belt.carry(reading, seconds, moment, actor) if seconds > 0 else self.parts.belt.state()
        return {"reading": reading.as_dict(), "belt": belt.as_dict()}

    def survey_chute(self, level_pct: float, moment: datetime, actor: str) -> dict[str, Any]:
        return self.parts.chute.survey(level_pct, moment, actor)

    def clear_chute(
        self,
        moment: datetime,
        actor: str,
        reason: str,
        *,
        level_pct: float = 0.0,
        force: bool = False,
    ) -> dict[str, Any]:
        return self.parts.chute.clear(moment, actor, reason, level_pct, force=force)

    def sample_product(self, undersize_t: float, total_t: float, moment: datetime, actor: str) -> dict[str, Any]:
        return self.parts.screen.grade(undersize_t, total_t, moment, actor)

    def sample_amps(self, machine: str, amps: float, moment: datetime, actor: str) -> dict[str, Any]:
        """Judge one draw reading on the crusher the caller names."""

        if machine == "jaw":
            return {"jaw": self.parts.jaw.sample_amps(amps, moment, actor)}
        if machine == "cone":
            return {"cone": self.parts.cone.sample_amps(amps, moment, actor)}
        raise InvalidRequest("that machine has no current reading", unit=self.unit, machine=machine)

    def calibrate_scale(
        self,
        points: Sequence[Any],
        moment: datetime,
        actor: str,
        ttl_seconds: float,
    ) -> dict[str, Any]:
        return self.parts.scale.calibrate(points, moment, actor, ttl_seconds).as_dict()

    def calibrate_cone(
        self,
        points: Sequence[Any],
        moment: datetime,
        actor: str,
        ttl_seconds: float,
    ) -> dict[str, Any]:
        return self.parts.calibration.calibrate(points, moment, actor, ttl_seconds).as_dict()

    def trip_magnet(self, reason: str, moment: datetime, actor: str) -> dict[str, Any]:
        return self.parts.magnet.trip(reason, moment, actor).as_dict()

    def reset_magnet(self, moment: datetime, actor: str, reason: str) -> dict[str, Any]:
        return self.parts.magnet.reset(moment, actor, reason).as_dict()

    def tick(self, seconds: float, moment: datetime, actor: str) -> dict[str, Any]:
        """One control cycle: the feeder moves ore and the bin gives it up.

        The steps of a cycle are staged inside one batch, so a reader sees
        either the whole cycle or none of it.
        """

        before = self.parts.feeder.total_tonnes()
        with self._ledger.batch(self.unit, actor):
            feeder = self.parts.feeder.carry(seconds, moment)
            moved = round(feeder.tonnage - before, 4)
            drawn = self.parts.bin.draw(moved, moment, actor) if moved > 0 else self.parts.bin.state()
        return {
            "unit": self.unit,
            "seconds": seconds,
            "moved_t": moved,
            "feeder": feeder.as_dict(),
            "bin": drawn.as_dict(),
            "state": self.parts.machine.state(),
        }

    def ready(self) -> bool:
        return self.parts.machine.state() == RUNNING
