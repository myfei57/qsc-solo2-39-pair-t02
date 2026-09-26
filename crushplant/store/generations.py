"""Generation counters, confirmation slips and expiring baselines.

Every parameter set carries a generation number.  A confirmation or a baseline
is only accepted while it matches the live generation and has not passed its
own deadline, which is what keeps a stale approval from releasing a later feed
step or from re-using a calibration the line no longer runs.

A confirmation slip is single use and keeps its whole life on disk: who
approved it, the generation it belongs to, and -- once it leaves the live set
-- who consumed it or voided it, when and why.  It is never deleted in place,
so the trail survives the very shift change it has to be auditable across.
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

SLIP_LIVE = "live"
SLIP_REDEEMED = "redeemed"
SLIP_INVALIDATED = "invalidated"


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

    The slip carries the generation it was approved under and, once it has
    been consumed or voided, the actor, the moment and the reason of that
    closing step, so the paper trail is part of the slip itself.
    """

    slip_id: str
    subject: str
    generation: int
    issued_at: str
    expires_at: str
    actor: str
    status: str = SLIP_LIVE
    conditions: dict[str, Any] = field(default_factory=dict)
    closed_at: str = ""
    closed_by: str = ""
    void_reason: str = ""
    superseded_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "slip_id": self.slip_id,
            "subject": self.subject,
            "generation": self.generation,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "actor": self.actor,
            "status": self.status,
            "conditions": self.conditions,
            "closed_at": self.closed_at,
            "closed_by": self.closed_by,
            "void_reason": self.void_reason,
            "superseded_at": self.superseded_at,
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
            status=str(raw.get("status", SLIP_LIVE)),
            conditions=dict(conditions) if isinstance(conditions, dict) else {},
            closed_at=str(raw.get("closed_at", "")),
            closed_by=str(raw.get("closed_by", "")),
            void_reason=str(raw.get("void_reason", "")),
            superseded_at=str(raw.get("superseded_at", "")),
        )

    @property
    def deadline(self) -> datetime:
        return parse_stamp(self.expires_at)

    @property
    def closed(self) -> bool:
        return self.status != SLIP_LIVE

    def expiring(self, moment: datetime) -> bool:
        return moment >= self.deadline

    def live(self, moment: datetime) -> bool:
        return self.status == SLIP_LIVE and not self.expiring(moment)

    def describe(self, moment: datetime) -> str:
        """A short operator-facing state for one slip."""

        if self.status == SLIP_REDEEMED:
            return f"redeemed at {self.closed_at} by {self.closed_by}"
        if self.status == SLIP_INVALIDATED:
            return f"invalidated at {self.closed_at}: {self.void_reason}"
        if self.expiring(moment):
            return "expired"
        return f"valid until {self.expires_at}"


class ConfirmationRegister:
    """Issues, redeems and invalidates confirmation slips.

    Closing a slip never erases it: the register keeps the closed slips beside
    the live ones, bounded by ``history_limit``, so an after-the-fact review
    can still see who approved a slip, who consumed it and why it was voided.
    """

    def __init__(self, store: DocumentStore, *, history_limit: int = 100) -> None:
        self._store = store
        self._history_limit = max(1, history_limit)
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

    def _serials(self) -> dict[str, int]:
        raw = self._payload().get("serials", {})
        if not isinstance(raw, dict):
            return {}
        return {str(key): int(value) for key, value in raw.items()}

    def _save(self, slips: dict[str, ConfirmationSlip], serials: dict[str, int], moment: datetime) -> None:
        closed = [key for key, slip in slips.items() if slip.closed]
        if len(closed) > self._history_limit:
            # The oldest closings leave the register first; a live slip is
            # never trimmed.
            closed.sort(key=lambda key: (slips[key].closed_at, key))
            for key in closed[: len(closed) - self._history_limit]:
                slips.pop(key, None)
        payload = {
            "slips": {key: slip.as_dict() for key, slip in slips.items()},
            "serials": dict(serials),
        }
        self._store.save(self.doc_id, payload, moment)

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
        serials = self._serials()
        subject = subject.strip()
        serial = serials.get(subject, 0) + 1
        identifier = (slip_id or f"{subject}-{int(generation)}-{serial}").strip()
        if identifier in slips:
            raise NameConflict("that confirmation slip already exists", slip_id=identifier)
        slip = ConfirmationSlip(
            slip_id=identifier,
            subject=subject,
            generation=int(generation),
            issued_at=stamp(moment),
            expires_at=stamp(moment + timedelta(seconds=float(ttl_seconds))),
            actor=actor.strip(),
            conditions=dict(conditions or {}),
        )
        slips[identifier] = slip
        serials[subject] = serial
        self._save(slips, serials, moment)
        return slip

    def get(self, slip_id: str) -> ConfirmationSlip:
        slip = self._slips().get(slip_id)
        if slip is None:
            raise RecordNotFound("no such confirmation slip", slip_id=slip_id)
        return slip

    def require(self, subject: str, generation: int, moment: datetime) -> ConfirmationSlip:
        """Return a live slip for ``subject`` or refuse the action.

        The refusal names what actually stands in the way -- nothing issued,
        already redeemed, voided, expired or written for another generation --
        so a blocked feed step explains itself.
        """

        candidates = [slip for slip in self._slips().values() if slip.subject == subject]
        if not candidates:
            raise StaleRecord(subject, "no confirmation was issued")
        live = [slip for slip in candidates if slip.status == SLIP_LIVE]
        latest = live[-1] if live else candidates[-1]
        if latest.status == SLIP_REDEEMED:
            raise StaleRecord(
                subject,
                f"confirmation already redeemed at {latest.closed_at} by {latest.closed_by}",
                slip_id=latest.slip_id,
            )
        if latest.status == SLIP_INVALIDATED:
            raise StaleRecord(
                subject,
                f"confirmation invalidated at {latest.closed_at}: {latest.void_reason}",
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
        """Consume one live slip, writing down who used it and when."""

        slips = self._slips()
        slip = slips.get(slip_id)
        if slip is None:
            raise RecordNotFound("no such confirmation slip", slip_id=slip_id)
        if slip.status == SLIP_REDEEMED:
            raise StaleRecord(
                slip.subject,
                f"confirmation already redeemed at {slip.closed_at} by {slip.closed_by}",
                slip_id=slip_id,
            )
        if slip.status == SLIP_INVALIDATED:
            raise StaleRecord(
                slip.subject,
                f"confirmation invalidated at {slip.closed_at}: {slip.void_reason}",
                slip_id=slip_id,
            )
        if slip.expiring(moment):
            raise StaleRecord(slip.subject, f"confirmation expired at {slip.expires_at}", slip_id=slip_id)
        redeemed = replace(slip, status=SLIP_REDEEMED, closed_at=stamp(moment), closed_by=actor.strip())
        slips[slip_id] = redeemed
        self._save(slips, self._serials(), moment)
        return redeemed

    def invalidate(self, subject: str, reason: str, moment: datetime, actor: str) -> list[str]:
        """Retire every slip of one subject, returning the ids it retired.

        A slip that was already consumed is not rewritten -- its redemption
        record stands -- but it is still superseded by the change and marked
        as such, so the retirement covers the whole paper trail of the
        subject, not just the slips that happen to be live.
        """

        if not reason.strip():
            raise InvalidRequest("invalidating a confirmation needs a reason")
        slips = self._slips()
        touched: list[str] = []
        for key, slip in list(slips.items()):
            if slip.subject != subject:
                continue
            if slip.status == SLIP_LIVE:
                slips[key] = replace(
                    slip,
                    status=SLIP_INVALIDATED,
                    closed_at=stamp(moment),
                    closed_by=actor.strip(),
                    void_reason=reason.strip(),
                    superseded_at=stamp(moment),
                )
            elif not slip.superseded_at:
                slips[key] = replace(slip, superseded_at=stamp(moment))
            touched.append(key)
        if touched:
            self._save(slips, self._serials(), moment)
        return touched

    def pending(self, moment: datetime) -> list[dict[str, Any]]:
        """Every slip that is still usable right now."""

        return [slip.as_dict() for slip in self._slips().values() if slip.live(moment)]

    def history(self) -> list[dict[str, Any]]:
        """The whole trail, live and closed slips alike, oldest first."""

        slips = sorted(self._slips().values(), key=lambda slip: (slip.issued_at, slip.slip_id))
        return [slip.as_dict() for slip in slips]


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
