"""The buffer bin: level, tonnage and the state the feed gate reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..audit.ledger import OUTCOME_OK, AuditLedger
from ..clock import DEFAULT_EPOCH, stamp
from ..config import StockSpec
from ..errors import InvalidRequest
from ..safety.interlock import Check
from ..store.documents import DocumentStore
from ..units import level_to_volume_m3, tonnes_from_volume, volume_to_level_pct

BIN_LOW = "low"
BIN_OK = "ok"
BIN_HIGH = "high"


@dataclass(frozen=True)
class BinState:
    """How much ore the bin holds and whether that is inside its band."""

    unit: str
    level_pct: float
    volume_m3: float
    tonnes: float
    capacity_t: float
    low_pct: float
    high_pct: float
    state: str
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit,
            "level_pct": self.level_pct,
            "volume_m3": self.volume_m3,
            "tonnes": self.tonnes,
            "capacity_t": self.capacity_t,
            "low_pct": self.low_pct,
            "high_pct": self.high_pct,
            "state": self.state,
            "updated_at": self.updated_at,
        }

    def describe(self) -> str:
        return f"bin {self.state} at {self.level_pct}% ({self.tonnes} t of {self.capacity_t} t)"


class OreBin:
    """Turns a level reading into tonnage and keeps the bin inside its band."""

    def __init__(self, unit: str, stock: StockSpec, store: DocumentStore, ledger: AuditLedger) -> None:
        self.unit = unit
        self._stock = stock
        self._store = store
        self._ledger = ledger
        self.doc_id = f"stock.{unit}.bin"
        self.capacity_t = round(
            tonnes_from_volume(stock.bin_capacity_m3, stock.bulk_density_t_per_m3),
            4,
        )

    def _payload(self) -> dict[str, Any]:
        document = self._store.try_load(self.doc_id)
        return {} if document is None else document.payload

    def _build(self, level_pct: float, moment: datetime) -> BinState:
        volume = level_to_volume_m3(level_pct, self._stock.bin_capacity_m3)
        state = BIN_OK
        if level_pct < self._stock.level_low_pct:
            state = BIN_LOW
        elif level_pct > self._stock.level_high_pct:
            state = BIN_HIGH
        return BinState(
            unit=self.unit,
            level_pct=round(float(level_pct), 3),
            volume_m3=volume,
            tonnes=tonnes_from_volume(volume, self._stock.bulk_density_t_per_m3),
            capacity_t=self.capacity_t,
            low_pct=self._stock.level_low_pct,
            high_pct=self._stock.level_high_pct,
            state=state,
            updated_at=stamp(moment),
        )

    def state(self) -> BinState:
        payload = self._payload()
        if not payload:
            return self._build(0.0, DEFAULT_EPOCH)
        return BinState(
            unit=str(payload.get("unit", self.unit)),
            level_pct=float(payload.get("level_pct", 0.0)),
            volume_m3=float(payload.get("volume_m3", 0.0)),
            tonnes=float(payload.get("tonnes", 0.0)),
            capacity_t=float(payload.get("capacity_t", self.capacity_t)),
            low_pct=float(payload.get("low_pct", self._stock.level_low_pct)),
            high_pct=float(payload.get("high_pct", self._stock.level_high_pct)),
            state=str(payload.get("state", BIN_LOW)),
            updated_at=str(payload.get("updated_at", "")),
        )

    def load(self, level_pct: float, moment: datetime, actor: str) -> BinState:
        """Adopt a measured level, which is how the bin enters the system."""

        if level_pct < 0 or level_pct > 100:
            raise InvalidRequest("a bin level sits between 0 and 100 percent", level=level_pct)
        state = self._build(level_pct, moment)
        self._write(state, moment)
        self._ledger.record(
            self.unit,
            "bin.load",
            OUTCOME_OK,
            actor,
            moment,
            subject=f"{self.unit}.bin",
            level_pct=state.level_pct,
            tonnes=state.tonnes,
        )
        return state

    def draw(self, tonnes: float, moment: datetime, actor: str) -> BinState:
        """Take ore out of the bin and report the level that leaves behind."""

        if tonnes < 0:
            raise InvalidRequest("a draw must not be negative", unit=self.unit)
        return self._shift(-float(tonnes), moment, actor, "bin.draw")

    def fill(self, tonnes: float, moment: datetime, actor: str) -> BinState:
        """Put ore back into the bin, as the tipping point does."""

        if tonnes < 0:
            raise InvalidRequest("a fill must not be negative", unit=self.unit)
        return self._shift(float(tonnes), moment, actor, "bin.fill")

    def check(self) -> Check:
        state = self.state()
        return Check(name=f"{self.unit}.bin", ok=state.state != BIN_LOW, detail=state.describe())

    def _shift(self, delta_t: float, moment: datetime, actor: str, action: str) -> BinState:
        current = self.state()
        volume = current.volume_m3 + delta_t / self._stock.bulk_density_t_per_m3
        if volume < 0:
            raise InvalidRequest("the bin does not hold that much ore", unit=self.unit, tonnes=current.tonnes)
        level = min(100.0, volume_to_level_pct(volume, self._stock.bin_capacity_m3))
        state = self._build(level, moment)
        self._write(state, moment)
        self._ledger.record(
            self.unit,
            action,
            OUTCOME_OK,
            actor,
            moment,
            subject=f"{self.unit}.bin",
            tonnes=round(abs(delta_t), 4),
            level_pct=state.level_pct,
        )
        return state

    def _write(self, state: BinState, moment: datetime) -> None:
        self._store.save(self.doc_id, state.as_dict(), moment)
