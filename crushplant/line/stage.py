"""The multi stage state machine that walks a line through a cycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from ..audit.ledger import OUTCOME_OK, AuditLedger
from ..clock import parse_stamp, stamp
from ..errors import StateConflict
from ..store.documents import DocumentStore
from .plan import SequencePlan, start_plan, stop_plan, trip_plan

STANDBY = "standby"
STARTING = "starting"
RUNNING = "running"
STOPPING = "stopping"
TRIPPED = "tripped"

STAGE_PENDING = "pending"
STAGE_DONE = "done"


@dataclass(frozen=True)
class StageRecord:
    """One completed step of one cycle."""

    stage: str
    state: str
    cycle: int
    at: str
    actor: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "state": self.state,
            "cycle": self.cycle,
            "at": self.at,
            "actor": self.actor,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "StageRecord":
        return cls(
            stage=str(raw.get("stage", "")),
            state=str(raw.get("state", STAGE_DONE)),
            cycle=int(raw.get("cycle", 0)),
            at=str(raw.get("at", "")),
            actor=str(raw.get("actor", "")),
        )

    def moment(self) -> datetime:
        return parse_stamp(self.at)


class LineStateMachine:
    """Holds the line state, the cycle counter and the stage ledger.

    The machine refuses any step that is not the next one its plan expects, so
    the only way to reach ``running`` is to walk the bring-up order in full.
    """

    def __init__(self, unit: str, store: DocumentStore, ledger: AuditLedger) -> None:
        self.unit = unit
        self._store = store
        self._ledger = ledger
        self.doc_id = f"line.{unit}.machine"
        self._start = start_plan(unit)
        self._stop = stop_plan(unit)
        self._trip = trip_plan(unit)

    def _payload(self) -> dict[str, Any]:
        document = self._store.try_load(self.doc_id)
        return {} if document is None else document.payload

    def _raw_stages(self) -> list[dict[str, Any]]:
        return [item for item in self._payload().get("stages", []) if isinstance(item, dict)]

    def state(self) -> str:
        return str(self._payload().get("state", STANDBY))

    def cycle(self) -> int:
        return int(self._payload().get("cycle", 0))

    def stage_log(self) -> list[StageRecord]:
        return [StageRecord.from_dict(item) for item in self._raw_stages()]

    def cycle_stages(self, cycle: int | None = None) -> list[StageRecord]:
        wanted = self.cycle() if cycle is None else int(cycle)
        return [record for record in self.stage_log() if record.cycle == wanted]

    def stages(self, cycle: int | None = None) -> list[str]:
        return [record.stage for record in self.cycle_stages(cycle)]

    def plans(self) -> dict[str, list[str]]:
        return {
            "start": list(self._start.steps),
            "stop": list(self._stop.steps),
            "trip": list(self._trip.steps),
        }

    def plan_for(self, label: str) -> SequencePlan:
        plans = {"start": self._start, "stop": self._stop, "trip": self._trip}
        found = plans.get(label)
        if found is None:
            raise StateConflict("no such sequence", unit=self.unit, sequence=label)
        return found

    def progress(self, label: str) -> dict[str, Any]:
        plan = self.plan_for(label)
        done = [step for step in self.stages() if step in plan.steps]
        return plan.as_dict(done)

    # ----------------------------------------------------------- transitions
    def open_cycle(self, moment: datetime, actor: str) -> dict[str, Any]:
        """Standby to starting; a new cycle number is issued here."""
        state = self.state()
        if state != STANDBY:
            raise StateConflict("the line is not standing by", unit=self.unit, state=state)
        self._write(STARTING, self.cycle() + 1, self._raw_stages(), moment)
        self._transition(actor, moment, STARTING)
        return {"state": STARTING, "cycle": self.cycle()}

    def complete_start_stage(self, stage: str, moment: datetime, actor: str) -> StageRecord:
        if self.state() != STARTING:
            raise StateConflict(
                "start stages may only run while the line is starting",
                unit=self.unit,
                state=self.state(),
                stage=stage,
            )
        self._start.require_step(self.stages(), stage)
        record = self._append(stage, moment, actor)
        if self._start.is_complete(self.stages()):
            self._write(RUNNING, self.cycle(), self._raw_stages(), moment)
            self._transition(actor, moment, RUNNING)
        return record

    def begin_stop(self, moment: datetime, actor: str) -> dict[str, Any]:
        state = self.state()
        if state != RUNNING:
            raise StateConflict("only a running line can be stopped", unit=self.unit, state=state)
        self._write(STOPPING, self.cycle(), self._raw_stages(), moment)
        self._transition(actor, moment, STOPPING)
        return {"state": STOPPING, "cycle": self.cycle()}

    def complete_stop_stage(self, stage: str, moment: datetime, actor: str) -> StageRecord:
        state = self.state()
        if state not in (STOPPING, TRIPPED):
            raise StateConflict(
                "stop stages may only run while the line is stopping",
                unit=self.unit,
                state=state,
                stage=stage,
            )
        plan = self._stop if state == STOPPING else self._trip
        plan.require_step([item for item in self.stages() if item in plan.steps], stage)
        return self._append(stage, moment, actor)

    def close_cycle(self, moment: datetime, actor: str) -> dict[str, Any]:
        if self.state() != STOPPING:
            raise StateConflict("the line is not stopping", unit=self.unit, state=self.state())
        done = [item for item in self.stages() if item in self._stop.steps]
        if not self._stop.is_complete(done):
            raise StateConflict(
                "the stop sequence is not complete",
                unit=self.unit,
                outstanding=self._stop.outstanding(done),
            )
        self._write(STANDBY, self.cycle(), self._raw_stages(), moment)
        self._transition(actor, moment, STANDBY)
        return {"state": STANDBY, "cycle": self.cycle()}

    def trip(self, reason: str, moment: datetime, actor: str) -> dict[str, Any]:
        state = self.state()
        if state == STANDBY:
            raise StateConflict("a line that is standing by cannot trip", unit=self.unit, state=state)
        self._write(TRIPPED, self.cycle(), self._raw_stages(), moment)
        self._ledger.record(
            self.unit,
            "line.transition",
            OUTCOME_OK,
            actor,
            moment,
            subject=self.unit,
            state=TRIPPED,
            reason=reason.strip(),
        )
        return {"state": TRIPPED, "reason": reason.strip(), "trip": self.progress("trip")}

    def recover(self, moment: datetime, actor: str) -> dict[str, Any]:
        """Tripped to standby, once the emergency sequence has been walked."""
        if self.state() != TRIPPED:
            raise StateConflict("the line is not tripped", unit=self.unit, state=self.state())
        done = [item for item in self.stages() if item in self._trip.steps]
        if not self._trip.is_complete(done):
            raise StateConflict(
                "the emergency sequence is not complete",
                unit=self.unit,
                outstanding=self._trip.outstanding(done),
            )
        self._write(STANDBY, self.cycle(), self._raw_stages(), moment)
        self._transition(actor, moment, STANDBY)
        return {"state": STANDBY, "cycle": self.cycle()}

    def clear(self, moment: datetime, actor: str) -> None:
        """Drop the stored machine state so maintenance can hand the line back."""
        self._store.delete(self.doc_id)
        self._ledger.record(self.unit, "line.clear", OUTCOME_OK, actor, moment, subject=self.unit)

    # ------------------------------------------------------------- internals
    def _transition(self, actor: str, moment: datetime, state: str) -> None:
        self._ledger.record(
            self.unit,
            "line.transition",
            OUTCOME_OK,
            actor,
            moment,
            subject=self.unit,
            state=state,
            cycle=self.cycle(),
        )

    def _append(self, stage: str, moment: datetime, actor: str) -> StageRecord:
        record = StageRecord(
            stage=stage,
            state=STAGE_DONE,
            cycle=self.cycle(),
            at=stamp(moment),
            actor=actor.strip(),
        )
        stages = self._raw_stages()
        stages.append(record.as_dict())
        self._write(self.state(), self.cycle(), stages, moment)
        self._ledger.record(
            self.unit,
            f"stage.{stage}",
            OUTCOME_OK,
            actor,
            moment,
            subject=self.unit,
            stage=stage,
            cycle=record.cycle,
        )
        return record

    def _write(self, state: str, cycle: int, stages: Sequence[Any], moment: datetime) -> None:
        self._store.save(
            self.doc_id,
            {"state": state, "cycle": int(cycle), "stages": list(stages), "at": stamp(moment)},
            moment,
        )
