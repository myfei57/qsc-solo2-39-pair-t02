"""Deck duty: how hard one screening deck is being worked."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..audit.ledger import AuditLedger
from ..clock import stamp
from ..errors import InvalidRequest
from ..store.documents import DocumentStore
from ..units import specific_throughput
from ..verdict.log import VerdictLog
from ..verdict.threshold import Limit, judge


@dataclass(frozen=True)
class DeckLoad:
    """The duty one deck carried over one weighing interval."""

    unit: str
    deck: str
    tonnes_per_hour: float
    specific_tph_per_m2: float
    utilisation_pct: float
    at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit,
            "deck": self.deck,
            "tonnes_per_hour": self.tonnes_per_hour,
            "specific_tph_per_m2": self.specific_tph_per_m2,
            "utilisation_pct": self.utilisation_pct,
            "at": self.at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any], unit: str, deck: str) -> "DeckLoad":
        return cls(
            unit=str(raw.get("unit", unit)),
            deck=str(raw.get("deck", deck)),
            tonnes_per_hour=float(raw.get("tonnes_per_hour", 0.0)),
            specific_tph_per_m2=float(raw.get("specific_tph_per_m2", 0.0)),
            utilisation_pct=float(raw.get("utilisation_pct", 0.0)),
            at=str(raw.get("at", "")),
        )


class DeckMonitor:
    """Turns a throughput into a deck duty and judges it against the rating."""

    def __init__(
        self,
        unit: str,
        deck_id: str,
        store: DocumentStore,
        ledger: AuditLedger,
        verdicts: VerdictLog,
        *,
        capacity_tph: float,
        width_m: float,
        length_m: float,
    ) -> None:
        if capacity_tph <= 0:
            raise InvalidRequest("a deck needs a positive capacity", deck=deck_id)
        self.unit = unit
        self.deck = deck_id
        self._store = store
        self._ledger = ledger
        self._verdicts = verdicts
        self.capacity_tph = float(capacity_tph)
        self.width_m = float(width_m)
        self.length_m = float(length_m)
        self.doc_id = f"screen.{unit}.deck.{deck_id}"
        self.limit = Limit("deck.utilisation", 0.0, 100.0)

    def duty(self, tonnes_per_hour: float, moment: datetime, actor: str) -> dict[str, Any]:
        """Record one duty reading and judge the share of the rating it uses."""

        if tonnes_per_hour < 0:
            raise InvalidRequest("deck throughput must not be negative", deck=self.deck)
        utilisation = round(100.0 * float(tonnes_per_hour) / self.capacity_tph, 3)
        load = DeckLoad(
            unit=self.unit,
            deck=self.deck,
            tonnes_per_hour=round(float(tonnes_per_hour), 4),
            specific_tph_per_m2=specific_throughput(tonnes_per_hour, self.width_m, self.length_m),
            utilisation_pct=utilisation,
            at=stamp(moment),
        )
        self._store.save(self.doc_id, load.as_dict(), moment)
        verdict = judge(f"{self.unit}.deck.{self.deck}", utilisation, self.limit, moment)
        self._verdicts.record_threshold(
            self.unit,
            verdict,
            moment,
            actor,
            tonnes_per_hour=load.tonnes_per_hour,
            capacity_tph=self.capacity_tph,
        )
        return {"load": load.as_dict(), "verdict": verdict.as_dict()}

    def state(self) -> DeckLoad | None:
        document = self._store.try_load(self.doc_id)
        if document is None:
            return None
        return DeckLoad.from_dict(document.payload, self.unit, self.deck)
