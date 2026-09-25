"""The feed gate: every condition that has to hold before ore may move."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..audit.ledger import OUTCOME_BLOCKED, OUTCOME_OK, AuditLedger
from ..config import LineSpec
from ..safety.interlock import Check, guard, summarise
from ..safety.latch import Latch
from ..store.generations import ConfirmationRegister, GenerationRegistry
from ..verdict.threshold import Limit, judge

FEED_SUBJECT = "feed"


class FeedGate:
    """Collects the preconditions of the feed step into one checked place.

    The gate never decides for itself: it asks each component for its own state
    and reports what is missing, which keeps the ordering knowledge in one
    place instead of spread across the components.
    """

    def __init__(
        self,
        unit: str,
        spec: LineSpec,
        ledger: AuditLedger,
        jaw: Any,
        belt: Any,
        screen: Any,
        cone: Any,
        magnet: Any,
        bin_state: Any,
        latch: Latch,
        confirmations: ConfirmationRegister,
        generations: GenerationRegistry,
    ) -> None:
        self.unit = unit
        self._spec = spec
        self._ledger = ledger
        self._jaw = jaw
        self._belt = belt
        self._screen = screen
        self._cone = cone
        self._magnet = magnet
        self._bin = bin_state
        self._latch = latch
        self._confirmations = confirmations
        self._generations = generations
        self.limit = Limit("feed.setpoint", spec.feeder_min_tph, spec.feeder_max_tph)

    @property
    def subject(self) -> str:
        return f"{self.unit}.{FEED_SUBJECT}"

    def checks(self) -> list[Check]:
        """Every precondition, whether or not it currently holds."""

        jaw = self._jaw.state()
        magnet = self._magnet.state()
        belt = self._belt.state()
        screen = self._screen.state()
        cone = self._cone.state()
        bin_state = self._bin.state()
        return [
            Check(
                "jaw-state",
                self._jaw.committed(),
                f"crusher state revision {jaw.revision} on disk" if self._jaw.committed() else "crusher state was never written to disk",
            ),
            Check("magnet", magnet.ready, magnet.describe()),
            Check("belt", belt.running, f"discharge belt running={belt.running}"),
            Check("screen", screen.running, f"screen running={screen.running} deck={screen.deck}"),
            Check("cone", cone.running, f"secondary crusher running={cone.running}"),
            Check("bin", bin_state.state != "low", bin_state.describe()),
            self._latch.check(),
        ]

    def preview(self) -> dict[str, Any]:
        """A read-only view a caller may render before trying the step."""

        return summarise(self.checks())

    def require(self, moment: datetime, actor: str) -> None:
        """Refuse the feed step, writing the refusal down before raising."""

        checks = self.checks()
        failing = [check for check in checks if not check.ok]
        if not failing:
            self._ledger.record(
                self.unit,
                "gate.feed",
                OUTCOME_OK,
                actor,
                moment,
                subject=self.subject,
                checks=[check.name for check in checks],
            )
            return
        self._ledger.record(
            self.unit,
            "gate.feed",
            OUTCOME_BLOCKED,
            actor,
            moment,
            subject=self.subject,
            unmet=[check.detail for check in failing],
        )
        guard("feed", checks)

    def require_confirmation(self, moment: datetime) -> dict[str, Any]:
        """Demand a live confirmation slip for this generation."""

        slip = self._confirmations.require(self.subject, self._generations.generation(), moment)
        return slip.as_dict()

    def issue_confirmation(
        self,
        ttl_seconds: float,
        moment: datetime,
        actor: str,
        *,
        target_tph: float | None = None,
    ) -> dict[str, Any]:
        """Issue the slip that ``require_confirmation`` will look for."""

        conditions: dict[str, Any] = {"unit": self.unit}
        if target_tph is not None:
            conditions["target_tph"] = float(target_tph)
        slip = self._confirmations.issue(
            self.subject,
            self._generations.generation(),
            ttl_seconds,
            moment,
            actor,
            conditions=conditions,
        )
        return slip.as_dict()

    def judge(self, target_tph: float, moment: datetime) -> dict[str, Any]:
        """Compare the requested setpoint against the feeder band."""

        return judge(self.subject, float(target_tph), self.limit, moment).as_dict()
