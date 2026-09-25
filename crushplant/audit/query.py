"""Filter conditions applied to ledger entries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from ..clock import parse_stamp
from ..errors import InvalidRequest


def optional_moment(value: Any) -> datetime | None:
    """Read an ISO instant from a query string, or nothing at all."""

    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return parse_stamp(str(value))
    except ValueError as exc:
        raise InvalidRequest("timestamp filters must be ISO instants", value=value) from exc


@dataclass(frozen=True)
class AuditQuery:
    """The filter a caller may apply to the operation ledger.

    Every field narrows the result set and an unset field places no condition,
    so the same object serves the console, the command line and the tests.
    """

    unit: str = ""
    action: str = ""
    outcome: str = ""
    subject: str = ""
    since: datetime | None = None
    until: datetime | None = None
    limit: int | None = None

    def matches(self, entry: Any) -> bool:
        if self.unit and entry.unit != self.unit:
            return False
        if self.action and entry.action != self.action:
            return False
        if self.outcome and entry.outcome != self.outcome:
            return False
        if self.subject and entry.subject != self.subject:
            return False
        moment = entry.moment()
        if self.since is not None and moment < self.since:
            return False
        if self.until is not None and moment > self.until:
            return False
        return True

    def apply(self, entries: Iterable[Any]) -> list[Any]:
        selected = [entry for entry in entries if self.matches(entry)]
        if self.limit is None:
            return selected
        if self.limit < 0:
            raise InvalidRequest("query limit must not be negative")
        if self.limit == 0:
            return []
        return selected[-self.limit :]

    def describe(self) -> dict[str, Any]:
        return {
            "unit": self.unit,
            "action": self.action,
            "outcome": self.outcome,
            "subject": self.subject,
            "since": None if self.since is None else self.since.isoformat(),
            "until": None if self.until is None else self.until.isoformat(),
            "limit": self.limit,
        }

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "AuditQuery":
        """Build a query from console style parameters."""

        limit_raw = raw.get("limit")
        limit: int | None = None
        if limit_raw not in (None, ""):
            try:
                limit = int(limit_raw)
            except (TypeError, ValueError) as exc:
                raise InvalidRequest("limit must be a whole number", value=limit_raw) from exc
        return cls(
            unit=str(raw.get("unit", "") or ""),
            action=str(raw.get("action", "") or ""),
            outcome=str(raw.get("outcome", "") or ""),
            subject=str(raw.get("subject", "") or ""),
            since=optional_moment(raw.get("since")),
            until=optional_moment(raw.get("until")),
            limit=limit,
        )
