"""Generation counters, confirmation slips and expiring baselines.

Every parameter set carries a generation number.  A confirmation or a baseline
is only accepted while it matches the live generation and has not passed its
own deadline, which is what keeps a stale approval from releasing a later feed
step or from re-using a calibration the line no longer runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from ..clock import parse_stamp, stamp
from ..errors import InvalidRequest, NameConflict, RecordNotFound, StaleRecord
from .documents import DocumentStore

GENERATION_DOC = "config.generation"
CONFIRMATION_DOC = "safety.confirmations"
BASELINE_DOC = "line.baselines"


@dataclass(frozen=True)
class GenerationState:
    """The generation a live line currently runs at."""

    generation: int = 1
    bumped_at: str = ""
    reason: str = ""
    actor: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "bumped_at": self.bumped_at,
            "reason": self.reason,
            "actor": self.actor,
        }


class GenerationRegistry:
    """Tracks the parameter generation and the reason each one was issued."""

    def __init__(self, store: DocumentStore) -> None:
        self._store = store
        self.doc_id = GENERATION_DOC

    def current(self) -> GenerationState:
        document = self._store.try_load(self.doc_id)
        if document is None:
            return GenerationState()
        payload = document.payload
        return GenerationState(
            generation=int(payload.get("generation", 1)),
            bumped_at=str(payload.get("bumped_at", "")),
            reason=str(payload.get("reason", "")),
            actor=str(payload.get("actor", "")),
        )

    def generation(self) -> int:
        return self.current().generation

    def bump(self, moment: datetime, actor: str, reason: str) -> GenerationState:
        if not reason.strip():
            raise InvalidRequest("a generation change needs a reason")
        previous = self.current()
        state = GenerationState(
            generation=previous.generation + 1,
            bumped_at=stamp(moment),
            reason=reason.strip(),
            actor=actor.strip(),
        )
        history = self.history()
        history.append(previous.as_dict())
        self._store.save(self.doc_id, {**state.as_dict(), "history": history[-40:]}, moment)
        return state

    def history(self) -> list[dict[str, Any]]:
        document = self._store.try_load(self.doc_id)
        if document is None:
            return []
        recorded = document.payload.get("history", [])
        return [dict(entry) for entry in recorded if isinstance(entry, dict)]


@dataclass(frozen=True)
class ConfirmationSlip:
    """A single-use approval for one action at one generation.

    A slip is never deleted: redeeming or invalidating it only adds the
    tombstone fields, so the register can always answer who approved it, when
    it was written off and why it was retired.
    """

    slip_id: str
    subject: str
    generation: int
    issued_at: str
    expires_at: str
    actor: str
    conditions: dict[str, Any] = field(default_factory=dict)
    redeemed_at: str = ""
    redeemed_by: str = ""
    invalidated_at: str = ""
    invalidated_by: str = ""
    invalidation_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "slip_id": self.slip_id,
            "subject": self.subject,
            "generation": self.generation,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "actor": self.actor,
            "conditions": self.conditions,
            "redeemed_at": self.redeemed_at,
            "redeemed_by": self.redeemed_by,
            "invalidated_at": self.invalidated_at,
            "invalidated_by": self.invalidated_by,
            "invalidation_reason": self.invalidation_reason,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ConfirmationSlip":
        conditions = raw.get("conditions")
        return cls(
            slip_id=str(raw.get("slip_id", "")),
            subject=str(raw.get("subject", "")),
            generation=int(raw.get("generation", 0)),
            issued_at=str(raw.get("issued_at", "")),
            expires_at=str(raw.get("expires_at", "")),
            actor=str(raw.get("actor", "")),
            conditions=dict(conditions) if isinstance(conditions, dict) else {},
            redeemed_at=str(raw.get("redeemed_at", "")),
            redeemed_by=str(raw.get("redeemed_by", "")),
            invalidated_at=str(raw.get("invalidated_at", "")),
            invalidated_by=str(raw.get("invalidated_by", "")),
            invalidation_reason=str(raw.get("invalidation_reason", "")),
        )

    @property
    def deadline(self) -> datetime:
        return parse_stamp(self.expires_at)

    def expiring(self, moment: datetime) -> bool:
        return moment >= self.deadline

    @property
    def redeemed(self) -> bool:
        return bool(self.redeemed_at)

    @property
    def invalidated(self) -> bool:
        return bool(self.invalidated_at)

    def live(self, moment: datetime) -> bool:
        return not self.redeemed and not self.invalidated and not self.expiring(moment)

    def redeem(self, moment: datetime, actor: str) -> "ConfirmationSlip":
        """Mark the slip written off, keeping who did it and when."""

        return replace(self, redeemed_at=stamp(moment), redeemed_by=actor.strip())

    def invalidate(self, reason: str, moment: datetime, actor: str) -> "ConfirmationSlip":
        """Retire the slip, keeping why, who and when on the record."""

        return replace(
            self,
            invalidated_at=stamp(moment),
            invalidated_by=actor.strip(),
            invalidation_reason=reason.strip(),
        )

    def describe(self, moment: datetime) -> str:
        """A short operator-facing state for one slip."""

        if self.invalidated:
            return f"invalidated at {self.invalidated_at}: {self.invalidation_reason}"
        if self.redeemed:
            return f"redeemed at {self.redeemed_at} by {self.redeemed_by}"
        if self.expiring(moment):
            return "expired"
        return f"valid until {self.expires_at}"


class ConfirmationRegister:
    """Issues, redeems and invalidates confirmation slips.

    Redeemed and invalidated slips stay on the record as tombstones instead of
    being deleted, which is what makes the trail auditable: every slip keeps
    who issued it, who wrote it off and when, or who retired it and why.
    """

    def __init__(self, store: DocumentStore) -> None:
        self._store = store
        self.doc_id = CONFIRMATION_DOC

    def _payload(self) -> dict[str, Any]:
        document = self._store.try_load(self.doc_id)
        return {} if document is None else document.payload

    def _slips(self) -> dict[str, ConfirmationSlip]:
        raw = self._payload().get("slips", {})
        if not isinstance(raw, dict):
            return {}
        return {
            key: ConfirmationSlip.from_dict(value)
            for key, value in raw.items()
            if isinstance(value, dict)
        }

    def _counters(self) -> dict[str, int]:
        raw = self._payload().get("counters", {})
        if not isinstance(raw, dict):
            return {}
        return {str(key): int(value) for key, value in raw.items()}

    def _save(
        self,
        slips: dict[str, ConfirmationSlip],
        counters: dict[str, int],
        moment: datetime,
    ) -> None:
        payload = {
            "slips": {key: slip.as_dict() for key, slip in slips.items()},
            "counters": dict(counters),
        }
        self._store.save(self.doc_id, payload, moment)

    def _next_id(self, subject: str, generation: int, counters: dict[str, int], taken: set[str]) -> str:
        """The next serialised slip id, unique across restarts and tombstones."""

        key = f"{subject}@{generation}"
        serial = counters.get(key, 0)
        while True:
            serial += 1
            identifier = f"{subject}-{generation}-{serial}"
            if identifier not in taken:
                counters[key] = serial
                return identifier

    def issue(
        self,
        subject: str,
        generation: int,
        ttl_seconds: float,
        moment: datetime,
        actor: str,
        *,
        conditions: dict[str, Any] | None = None,
        slip_id: str | None = None,
    ) -> ConfirmationSlip:
        if ttl_seconds <= 0:
            raise InvalidRequest("a confirmation needs a positive lifetime")
        slips = self._slips()
        counters = self._counters()
        subject = subject.strip()
        generation = int(generation)
        if slip_id is None:
            identifier = self._next_id(subject, generation, counters, set(slips))
        else:
            identifier = slip_id.strip()
        if identifier in slips:
            raise NameConflict("that confirmation slip already exists", slip_id=identifier)
        slip = ConfirmationSlip(
            slip_id=identifier,
            subject=subject,
            generation=generation,
            issued_at=stamp(moment),
            expires_at=stamp(moment + timedelta(seconds=float(ttl_seconds))),
            actor=actor.strip(),
            conditions=dict(conditions or {}),
        )
        slips[identifier] = slip
        self._save(slips, counters, moment)
        return slip

    def get(self, slip_id: str) -> ConfirmationSlip:
        slip = self._slips().get(slip_id)
        if slip is None:
            raise RecordNotFound("no such confirmation slip", slip_id=slip_id)
        return slip

    def require(self, subject: str, generation: int, moment: datetime) -> ConfirmationSlip:
        """Return a live slip for ``subject`` or refuse the action."""

        # Slips are examined in the order they were issued, so the newest one
        # wins even when two of them carry the same second-resolution stamp.
        candidates = [slip for slip in self._slips().values() if slip.subject == subject]
        if not candidates:
            raise StaleRecord(subject, "no confirmation was issued")
        latest = candidates[-1]
        if latest.invalidated:
            raise StaleRecord(
                subject,
                f"confirmation {latest.slip_id} was invalidated at {latest.invalidated_at}: "
                f"{latest.invalidation_reason}",
                slip_id=latest.slip_id,
            )
        if latest.redeemed:
            raise StaleRecord(
                subject,
                f"confirmation {latest.slip_id} was already redeemed at {latest.redeemed_at}",
                slip_id=latest.slip_id,
            )
        if latest.generation != int(generation):
            raise StaleRecord(
                subject,
                f"confirmation is for generation {latest.generation}, live generation is {generation}",
                slip_id=latest.slip_id,
            )
        if latest.expiring(moment):
            raise StaleRecord(subject, f"confirmation expired at {latest.expires_at}", slip_id=latest.slip_id)
        return latest

    def redeem(self, slip_id: str, moment: datetime, actor: str) -> ConfirmationSlip:
        """Write a slip off exactly once, keeping who did it and when."""

        slips = self._slips()
        slip = slips.get(slip_id)
        if slip is None:
            raise RecordNotFound("no such confirmation slip", slip_id=slip_id)
        if slip.invalidated:
            raise StaleRecord(
                slip.subject,
                f"confirmation {slip_id} was invalidated at {slip.invalidated_at}: {slip.invalidation_reason}",
                slip_id=slip_id,
            )
        if slip.redeemed:
            raise StaleRecord(
                slip.subject,
                f"confirmation {slip_id} was already redeemed at {slip.redeemed_at}",
                slip_id=slip_id,
            )
        if slip.expiring(moment):
            raise StaleRecord(slip.subject, f"confirmation expired at {slip.expires_at}", slip_id=slip_id)
        redeemed = slip.redeem(moment, actor)
        slips[slip_id] = redeemed
        self._save(slips, self._counters(), moment)
        return redeemed

    def invalidate(self, subject: str, reason: str, moment: datetime, actor: str) -> list[str]:
        """Invalidate every open slip of one subject, returning their ids."""

        if not reason.strip():
            raise InvalidRequest("invalidating a confirmation needs a reason")
        slips = self._slips()
        touched: list[str] = []
        for key, slip in list(slips.items()):
            if slip.subject != subject or slip.redeemed or slip.invalidated:
                continue
            slips[key] = slip.invalidate(reason, moment, actor)
            touched.append(key)
        if touched:
            self._save(slips, self._counters(), moment)
        return touched

    def pending(self, moment: datetime) -> list[dict[str, Any]]:
        """Every slip that is still usable right now."""

        return [slip.as_dict() for slip in self._slips().values() if slip.live(moment)]

    def history(self) -> list[dict[str, Any]]:
        """Every slip on record, including the redeemed and invalidated ones."""

        return [slip.as_dict() for slip in self._slips().values()]


@dataclass(frozen=True)
class BaselineRecord:
    """A measured steady-state reference bound to one generation."""

    subject: str
    value: float
    generation: int
    captured_at: str
    expires_at: str
    samples: int
    actor: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "value": self.value,
            "generation": self.generation,
            "captured_at": self.captured_at,
            "expires_at": self.expires_at,
            "samples": self.samples,
            "actor": self.actor,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BaselineRecord":
        return cls(
            subject=str(raw.get("subject", "")),
            value=float(raw.get("value", 0.0)),
            generation=int(raw.get("generation", 0)),
            captured_at=str(raw.get("captured_at", "")),
            expires_at=str(raw.get("expires_at", "")),
            samples=int(raw.get("samples", 0)),
            actor=str(raw.get("actor", "")),
        )

    @property
    def deadline(self) -> datetime:
        return parse_stamp(self.expires_at)

    def expiring(self, moment: datetime) -> bool:
        return moment >= self.deadline

    def age_seconds(self, moment: datetime) -> float:
        return max(0.0, (moment - parse_stamp(self.captured_at)).total_seconds())


class BaselineStore:
    """Keeps the newest baseline of every subject together with its trail."""

    def __init__(self, store: DocumentStore, *, history_limit: int = 20) -> None:
        self._store = store
        self._history_limit = max(1, history_limit)
        self.doc_id = BASELINE_DOC

    def _payload(self) -> dict[str, Any]:
        document = self._store.try_load(self.doc_id)
        return {} if document is None else document.payload

    def record(
        self,
        subject: str,
        value: float,
        generation: int,
        moment: datetime,
        ttl_seconds: float,
        *,
        samples: int = 1,
        actor: str = "",
    ) -> BaselineRecord:
        if ttl_seconds <= 0:
            raise InvalidRequest("a baseline needs a positive lifetime")
        if samples <= 0:
            raise InvalidRequest("a baseline needs at least one sample")
        record = BaselineRecord(
            subject=subject.strip(),
            value=float(value),
            generation=int(generation),
            captured_at=stamp(moment),
            expires_at=stamp(moment + timedelta(seconds=float(ttl_seconds))),
            samples=int(samples),
            actor=actor.strip(),
        )
        payload = self._payload()
        current = payload.get("current", {})
        history = payload.get("history", [])
        if isinstance(current, dict) and current:
            history = list(history) + [dict(current)]
        merged = dict(current) if isinstance(current, dict) else {}
        merged[subject] = record.as_dict()
        self._store.save(
            self.doc_id,
            {"current": merged, "history": history[-self._history_limit :]},
            moment,
        )
        return record

    def latest(self, subject: str) -> BaselineRecord | None:
        current = self._payload().get("current", {})
        if not isinstance(current, dict):
            return None
        raw = current.get(subject)
        return None if not isinstance(raw, dict) else BaselineRecord.from_dict(raw)

    def require_fresh(self, subject: str, generation: int, moment: datetime) -> BaselineRecord:
        record = self.latest(subject)
        if record is None:
            raise StaleRecord(subject, "no baseline was captured")
        if record.generation != int(generation):
            raise StaleRecord(
                subject,
                f"baseline is for generation {record.generation}, live generation is {generation}",
            )
        if record.expiring(moment):
            raise StaleRecord(subject, f"baseline expired at {record.expires_at}")
        return record

    def history(self) -> list[dict[str, Any]]:
        entries = self._payload().get("history", [])
        return [dict(entry) for entry in entries if isinstance(entry, dict)]

    def subjects(self) -> list[str]:
        current = self._payload().get("current", {})
        return sorted(current) if isinstance(current, dict) else []
