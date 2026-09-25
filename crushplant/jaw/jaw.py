"""The primary crusher: gap setting, run state and its load reading."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..audit.ledger import OUTCOME_OK, AuditLedger
from ..config import LineSpec
from ..errors import InvalidRequest, StateConflict
from ..units import crusher_load_pct, reduction_ratio
from ..verdict.log import VerdictLog
from ..verdict.threshold import Limit, judge
from .state import RUNNING, STOPPED, JawState, JawStateStore


class JawCrusher:
    """Owns the jaw setting and keeps its state on disk at every change."""

    def __init__(
        self,
        unit: str,
        spec: LineSpec,
        states: JawStateStore,
        ledger: AuditLedger,
        verdicts: VerdictLog,
    ) -> None:
        self.unit = unit
        self._spec = spec
        self._states = states
        self._ledger = ledger
        self._verdicts = verdicts
        self.limit = Limit("jaw.current", spec.jaw_min_amps, spec.jaw_max_amps)

    @property
    def doc_id(self) -> str:
        return self._states.doc_id

    def state(self) -> JawState:
        return self._states.current()

    def committed(self) -> bool:
        return self._states.committed()

    def require_persisted(self, action: str) -> JawState:
        return self._states.require_committed(action)

    def republish_state(self, moment: datetime, actor: str) -> JawState:
        """Write the current state onto the journal again.

        A watermark rollback can leave a state document that no longer sits
        below the commit line.  Re-publishing is the operator's way of putting
        the same state back on the record without moving the machine.
        """

        state = self._states.persist(moment, actor)
        self._ledger.record(
            self.unit,
            "jaw.republish",
            OUTCOME_OK,
            actor,
            moment,
            subject=f"{self.unit}.jaw",
            revision=state.revision,
            record=state.committed_sequence,
        )
        return state

    def running(self) -> bool:
        return self.state().state == RUNNING

    def _settings(self) -> tuple[float, float, float]:
        state = self.state()
        gap = state.gap_mm or self._spec.jaw_gap_mm
        feed = state.feed_mm or self._spec.jaw_feed_mm
        product = state.product_mm or self._spec.jaw_product_mm
        return gap, feed, product

    def start(self, moment: datetime, actor: str) -> JawState:
        """Set the crusher running and write the new state down immediately."""

        state = self.state()
        if state.state == RUNNING:
            raise StateConflict("the crusher is already running", unit=self.unit, since=state.drafted_at)
        gap, feed, product = self._settings()
        self._states.draft(
            state=RUNNING,
            gap_mm=gap,
            feed_mm=feed,
            product_mm=product,
            reduction=reduction_ratio(feed, product),
            moment=moment,
        )
        committed = self._states.persist(moment, actor)
        self._ledger.record(
            self.unit,
            "jaw.start",
            OUTCOME_OK,
            actor,
            moment,
            subject=f"{self.unit}.jaw",
            revision=committed.revision,
            gap_mm=committed.gap_mm,
            record=committed.committed_sequence,
        )
        return committed

    def stop(self, moment: datetime, actor: str, reason: str = "operator stop") -> JawState:
        """Bring the crusher down; the caller must have stopped feeding first."""

        state = self.state()
        if state.state != RUNNING:
            raise StateConflict("the crusher is not running", unit=self.unit, state=state.state)
        gap, feed, product = self._settings()
        self._states.draft(
            state=STOPPED,
            gap_mm=gap,
            feed_mm=feed,
            product_mm=product,
            reduction=reduction_ratio(feed, product),
            moment=moment,
        )
        committed = self._states.persist(moment, actor)
        self._ledger.record(
            self.unit,
            "jaw.stop",
            OUTCOME_OK,
            actor,
            moment,
            subject=f"{self.unit}.jaw",
            reason=reason.strip(),
            revision=committed.revision,
            record=committed.committed_sequence,
        )
        return committed

    def set_gap(self, gap_mm: float, moment: datetime, actor: str) -> JawState:
        """Re-set the closed side; only a stopped crusher may be adjusted."""

        if gap_mm <= 0:
            raise InvalidRequest("the closed side setting must be positive", unit=self.unit)
        if self.state().state != STOPPED:
            raise StateConflict("the crusher must be stopped before the gap is changed", unit=self.unit)
        _, feed, product = self._settings()
        self._states.draft(
            state=STOPPED,
            gap_mm=gap_mm,
            feed_mm=feed,
            product_mm=product,
            reduction=reduction_ratio(feed, product),
            moment=moment,
        )
        committed = self._states.persist(moment, actor)
        self._ledger.record(
            self.unit,
            "jaw.gap",
            OUTCOME_OK,
            actor,
            moment,
            subject=f"{self.unit}.jaw",
            gap_mm=committed.gap_mm,
            revision=committed.revision,
        )
        return committed

    def sample_amps(self, amps: float, moment: datetime, actor: str) -> dict[str, Any]:
        """Judge one draw reading against the crusher current band."""

        verdict = judge(f"{self.unit}.jaw", float(amps), self.limit, moment)
        self._verdicts.record_threshold(
            self.unit,
            verdict,
            moment,
            actor,
            load_pct=crusher_load_pct(amps, self._spec.jaw_rated_amps),
        )
        return verdict.as_dict()
