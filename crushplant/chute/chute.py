"""The transfer chute that sets the feed latch when it blocks."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..audit.ledger import OUTCOME_OK, OUTCOME_TRIPPED, AuditLedger
from ..errors import StateConflict
from ..safety.latch import Latch
from ..store.documents import DocumentStore
from ..verdict.log import VerdictLog
from .detect import BlockageDetector

BLOCKED = "blocked"


class TransferChute:
    """Watches the chute level and owns the latch that blocks feeding.

    A confirmed blockage sets the latch, which the feed gate then reports as an
    unmet precondition.  Clearing the chute is a deliberate two part act: the
    level has to have fallen back past the low threshold and the operator has to
    say why the latch may come off.
    """

    def __init__(
        self,
        unit: str,
        store: DocumentStore,
        ledger: AuditLedger,
        latch: Latch,
        detector: BlockageDetector,
        verdicts: VerdictLog,
    ) -> None:
        self.unit = unit
        self._store = store
        self._ledger = ledger
        self._latch = latch
        self._detector = detector
        self._verdicts = verdicts
        self.doc_id = f"chute.{unit}.state"
        self.subject = f"{unit}.chute"

    def survey(self, level_pct: float, moment: datetime, actor: str) -> dict[str, Any]:
        """Record one level reading; a confirmed blockage latches the feed."""

        verdict = self._detector.observe(level_pct, moment)
        self._store.save(
            self.doc_id,
            {
                "unit": self.unit,
                "blocked": verdict.blocked,
                "level_pct": verdict.level_pct,
                "samples": verdict.samples,
                "at": moment.isoformat(),
            },
            moment,
        )
        self._verdicts.record(
            self.unit,
            self.subject,
            "chute.blockage",
            BLOCKED if verdict.blocked else "ok",
            verdict.level_pct,
            moment,
            actor,
            samples=verdict.samples,
            span_seconds=verdict.span_seconds,
            margin_pct=verdict.margin_pct,
        )
        if verdict.blocked:
            self._latch.trip(
                f"chute level {verdict.level_pct}% held above {verdict.block_pct}%",
                moment,
                actor,
            )
            self._ledger.record(
                self.unit,
                "chute.blocked",
                OUTCOME_TRIPPED,
                actor,
                moment,
                subject=self.subject,
                level_pct=verdict.level_pct,
                samples=verdict.samples,
            )
        return verdict.as_dict()

    def state(self, moment: datetime) -> dict[str, Any]:
        """The detector, the latch and the stored chute state together."""

        verdict = self._detector.state(moment)
        document = self._store.try_load(self.doc_id)
        return {
            "unit": self.unit,
            "blockage": verdict.as_dict(),
            "latch": self._latch.state().as_dict(),
            "stored": {} if document is None else document.payload,
        }

    def clear(
        self,
        moment: datetime,
        actor: str,
        reason: str,
        level_pct: float = 0.0,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        """Release the latch once the chute has actually emptied."""

        if not self._detector.is_clear(level_pct):
            raise StateConflict(
                "the chute is still loaded",
                unit=self.unit,
                level_pct=level_pct,
                clear_pct=self._detector.clear_pct,
            )
        verdict = self._detector.clear(moment, level_pct)
        released = self._latch.release(moment, actor, reason, force=force)
        self._ledger.record(
            self.unit,
            "chute.cleared",
            OUTCOME_OK,
            actor,
            moment,
            subject=self.subject,
            reason=reason.strip(),
            forced=force,
        )
        return {"blockage": verdict.as_dict(), "latch": released.as_dict()}

    def is_blocked(self, moment: datetime) -> bool:
        return self._detector.state(moment).blocked
