"""Cross component preconditions collected in one place."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from ..errors import InterlockBlocked


@dataclass(frozen=True)
class Check:
    """One precondition and whether it currently holds."""

    name: str
    ok: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


def unmet(checks: Iterable[Check]) -> list[str]:
    """The details of every check that does not hold."""

    return [check.detail for check in checks if not check.ok]


def summarise(checks: Iterable[Check]) -> dict[str, Any]:
    """Read-only precheck payload for the console."""

    items = [check.as_dict() for check in checks]
    failing = [item["detail"] for item in items if not item["ok"]]
    return {"ok": not failing, "checks": items, "unmet": failing}


def guard(action: str, checks: Iterable[Check], error_factory: Callable[..., Exception] | None = None) -> None:
    """Raise when any check fails, reporting every unmet precondition at once."""

    materialised = list(checks)
    failing = unmet(materialised)
    if not failing:
        return
    factory = error_factory or InterlockBlocked
    raise factory(action, failing)
