"""Error taxonomy shared by the control components and the console."""

from __future__ import annotations

from typing import Any, Iterable


class CrushError(Exception):
    """Base class for every error the service reports to a caller."""

    code = "crush-error"
    status = 500

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = dict(details)

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"error": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


class InvalidRequest(CrushError):
    """A caller supplied value is outside the accepted range or shape."""

    code = "invalid-request"
    status = 400


class RecordNotFound(CrushError):
    """The requested record, document or object does not exist."""

    code = "not-found"
    status = 404


class Conflict(CrushError):
    """A revision or compare-and-set guard rejected the write."""

    code = "conflict"
    status = 409


class NameConflict(CrushError):
    """A uniqueness constraint rejected the write."""

    code = "duplicate"
    status = 409


class StateConflict(CrushError):
    """The requested transition is not allowed from the current state."""

    code = "invalid-state"
    status = 409


class OrderingViolation(StateConflict):
    """A process step was requested out of its mandated order."""

    code = "ordering-violation"
    status = 409


class InterlockBlocked(CrushError):
    """One or more cross component preconditions are not satisfied."""

    code = "interlock"
    status = 409

    def __init__(self, action: str, unmet: Iterable[str], **details: Any) -> None:
        self.unmet = list(unmet)
        super().__init__(
            f"{action} is blocked by {len(self.unmet)} unmet precondition(s)",
            action=action,
            unmet=self.unmet,
            **details,
        )


class LatchActive(CrushError):
    """A protective latch is set and blocks the requested action."""

    code = "latched"
    status = 423

    def __init__(self, target: str, reason: str, **details: Any) -> None:
        self.target = target
        self.reason = reason
        super().__init__(f"{target} is latched: {reason}", reason=reason, **details)


class StaleRecord(CrushError):
    """A confirmation, baseline or snapshot is missing, superseded or expired."""

    code = "stale"
    status = 409

    def __init__(self, subject: str, reason: str, **details: Any) -> None:
        self.subject = subject
        self.reason = reason
        super().__init__(f"{subject} is stale: {reason}", subject=subject, reason=reason, **details)


class DurabilityError(CrushError):
    """A value that must survive a restart was not committed in time."""

    code = "not-durable"
    status = 409

    def __init__(self, subject: str, **details: Any) -> None:
        super().__init__(f"{subject} has no committed record", subject=subject, **details)


class LimitExceeded(CrushError):
    """A measured or commanded value crossed a configured limit."""

    code = "limit-exceeded"
    status = 422


class StoreError(CrushError):
    """A document or journal operation failed at the storage layer."""

    code = "store"
    status = 500


class ConfigError(CrushError):
    """The supplied configuration violates an operational envelope."""

    code = "config"
    status = 422


STATUS_BY_CODE = {
    cls.code: cls.status
    for cls in (
        InvalidRequest,
        RecordNotFound,
        Conflict,
        NameConflict,
        StateConflict,
        OrderingViolation,
        InterlockBlocked,
        LatchActive,
        StaleRecord,
        DurabilityError,
        LimitExceeded,
        StoreError,
        ConfigError,
    )
}


def status_for(error: BaseException) -> int:
    """Map an exception to the HTTP status the console should return."""

    if isinstance(error, CrushError):
        return STATUS_BY_CODE.get(error.code, 500)
    return 500


def payload_for(error: BaseException) -> dict[str, Any]:
    """Map an exception to the JSON body the console should return."""

    if isinstance(error, CrushError):
        return error.as_payload()
    return {"error": "internal", "message": str(error)}


def catalog() -> list[dict[str, Any]]:
    """Every code the service can report, with the status it maps to.

    The console, the command line and the run books all read this one list, so
    a new failure mode cannot be added without appearing in the inventory.
    """

    known = {
        cls.code: cls
        for cls in (
            InvalidRequest,
            RecordNotFound,
            Conflict,
            NameConflict,
            StateConflict,
            OrderingViolation,
            InterlockBlocked,
            LatchActive,
            StaleRecord,
            DurabilityError,
            LimitExceeded,
            StoreError,
            ConfigError,
        )
    }
    return [
        {
            "code": code,
            "status": status,
            "error": known[code].__name__,
            "summary": (known[code].__doc__ or "").strip(),
        }
        for code, status in sorted(STATUS_BY_CODE.items())
    ]
