"""Registry of every addressable tag the service exposes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ..errors import InvalidRequest, NameConflict, RecordNotFound

KIND_UNIT = "unit"
KIND_FEEDER = "feeder"
KIND_JAW = "jaw"
KIND_CONE = "cone"
KIND_BELT = "belt"
KIND_MAGNET = "magnet"
KIND_SCREEN = "screen"
KIND_CHUTE = "chute"
KIND_BIN = "bin"
KIND_LEVEL = "level"
KIND_LATCH = "latch"
KIND_BATCH = "batch"
KIND_CALIBRATION = "calibration"


@dataclass(frozen=True)
class SignalSpec:
    """One tag together with what it measures and where it lives."""

    tag: str
    kind: str
    unit_of_measure: str
    description: str
    area: str
    unit: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "kind": self.kind,
            "unit_of_measure": self.unit_of_measure,
            "description": self.description,
            "area": self.area,
            "unit": self.unit,
        }


class TagNamespace:
    """Every tag is registered once; a duplicate is refused rather than merged."""

    def __init__(self) -> None:
        self._tags: dict[str, SignalSpec] = {}

    def register(self, spec: SignalSpec) -> SignalSpec:
        tag = spec.tag.strip()
        if not tag:
            raise InvalidRequest("a tag must not be empty")
        if tag in self._tags:
            raise NameConflict("that tag is already registered", tag=tag)
        stored = SignalSpec(
            tag=tag,
            kind=spec.kind.strip(),
            unit_of_measure=spec.unit_of_measure.strip(),
            description=spec.description.strip(),
            area=spec.area.strip(),
            unit=spec.unit.strip(),
        )
        self._tags[tag] = stored
        return stored

    def register_all(self, specs: Iterable[SignalSpec]) -> list[SignalSpec]:
        return [self.register(spec) for spec in specs]

    def has(self, tag: str) -> bool:
        return tag in self._tags

    def get(self, tag: str) -> SignalSpec:
        spec = self._tags.get(tag)
        if spec is None:
            raise RecordNotFound("tag is not registered", tag=tag)
        return spec

    def describe(self, tag: str) -> dict[str, Any]:
        return {
            "tag": tag,
            "registered": self.has(tag),
            "spec": self.get(tag).as_dict() if self.has(tag) else None,
        }

    def tags(self, kind: str | None = None) -> list[str]:
        if kind is None:
            return sorted(self._tags)
        return sorted(tag for tag, spec in self._tags.items() if spec.kind == kind)

    def specs(self, kind: str | None = None) -> list[SignalSpec]:
        return [self._tags[tag] for tag in self.tags(kind)]

    def areas(self) -> list[str]:
        seen: set[str] = set()
        for spec in self._tags.values():
            seen.add(spec.area)
        return sorted(seen)

    def by_area(self) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {area: [] for area in self.areas()}
        for tag, spec in self._tags.items():
            grouped[spec.area].append(tag)
        return {area: sorted(tags) for area, tags in grouped.items()}

    def counts_by_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for spec in self._tags.values():
            counts[spec.kind] = counts.get(spec.kind, 0) + 1
        return counts

    def summary(self) -> dict[str, Any]:
        return {
            "tags": len(self._tags),
            "kinds": self.counts_by_kind(),
            "areas": self.areas(),
        }
