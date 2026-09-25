"""The bin level instrument and the age limit on its reading."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..audit.ledger import OUTCOME_OK, AuditLedger
from ..clock import parse_stamp, stamp
from ..errors import InvalidRequest, StaleRecord
from ..store.documents import DocumentStore


@dataclass(frozen=True)
class LevelReading:
    """One level reading together with how old it has become."""

    unit: str
    level_pct: float
    at: str
    age_seconds: float
    max_age_s: float

    def stale(self) -> bool:
        return self.age_seconds > self.max_age_s

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit,
            "level_pct": self.level_pct,
            "at": self.at,
            "age_seconds": self.age_seconds,
            "max_age_s": self.max_age_s,
            "stale": self.stale(),
        }


class LevelGauge:
    """Holds the last reading and refuses to let an old one drive the feeder."""

    def __init__(self, unit: str, store: DocumentStore, ledger: AuditLedger, *, max_age_s: float) -> None:
        if max_age_s <= 0:
            raise InvalidRequest("a level reading needs a positive age budget", unit=unit)
        self.unit = unit
        self._store = store
        self._ledger = ledger
        self.max_age_s = float(max_age_s)
        self.doc_id = f"level.{unit}.gauge"

    def sample(self, level_pct: float, moment: datetime, actor: str) -> LevelReading:
        if level_pct < 0 or level_pct > 100:
            raise InvalidRequest("a level sits between 0 and 100 percent", level=level_pct)
        reading = LevelReading(
            unit=self.unit,
            level_pct=round(float(level_pct), 3),
            at=stamp(moment),
            age_seconds=0.0,
            max_age_s=self.max_age_s,
        )
        self._store.save(
            self.doc_id,
            {"unit": self.unit, "level_pct": reading.level_pct, "at": reading.at},
            moment,
        )
        self._ledger.record(
            self.unit,
            "level.sample",
            OUTCOME_OK,
            actor,
            moment,
            subject=f"{self.unit}.level",
            level_pct=reading.level_pct,
        )
        return reading

    def reading(self, moment: datetime) -> LevelReading:
        document = self._store.try_load(self.doc_id)
        if document is None:
            raise StaleRecord(f"{self.unit}.level", "no level reading was ever taken")
        at = str(document.payload.get("at", ""))
        taken = parse_stamp(at)
        return LevelReading(
            unit=self.unit,
            level_pct=float(document.payload.get("level_pct", 0.0)),
            at=at,
            age_seconds=round(max(0.0, (moment - taken).total_seconds()), 3),
            max_age_s=self.max_age_s,
        )

    def require_fresh(self, moment: datetime, action: str) -> LevelReading:
        reading = self.reading(moment)
        if reading.stale():
            raise StaleRecord(
                f"{self.unit}.level",
                f"the level reading is {reading.age_seconds} s old, budget is {reading.max_age_s} s",
                action=action,
            )
        return reading

    def state(self, moment: datetime) -> dict[str, Any]:
        try:
            return self.reading(moment).as_dict()
        except StaleRecord as error:
            return {"unit": self.unit, "level_pct": None, "reason": error.reason, "stale": True}
