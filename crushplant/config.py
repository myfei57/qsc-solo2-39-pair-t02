"""Line configuration with the operational envelopes the service enforces."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import ConfigError


@dataclass(frozen=True)
class LineSpec:
    """Every number one crushing line needs to run inside its envelope."""

    unit: str
    feeder_rated_tph: float
    feeder_min_tph: float
    feeder_max_tph: float
    feeder_ramp_tph_per_second: float
    jaw_gap_mm: float
    jaw_feed_mm: float
    jaw_product_mm: float
    jaw_rated_amps: float
    jaw_min_amps: float
    jaw_max_amps: float
    cone_rated_amps: float
    cone_min_amps: float
    cone_max_amps: float
    cone_stall_pct: float
    belt_speed_mps: float
    belt_rated_tph: float
    screen_deck_id: str
    screen_width_m: float
    screen_length_m: float
    screen_capacity_tph: float
    screen_aperture_mm: float
    chute_block_pct: float
    chute_clear_pct: float
    chute_hold_seconds: float
    chute_min_samples: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit,
            "feeder_rated_tph": self.feeder_rated_tph,
            "feeder_min_tph": self.feeder_min_tph,
            "feeder_max_tph": self.feeder_max_tph,
            "feeder_ramp_tph_per_second": self.feeder_ramp_tph_per_second,
            "jaw_gap_mm": self.jaw_gap_mm,
            "jaw_feed_mm": self.jaw_feed_mm,
            "jaw_product_mm": self.jaw_product_mm,
            "jaw_rated_amps": self.jaw_rated_amps,
            "jaw_min_amps": self.jaw_min_amps,
            "jaw_max_amps": self.jaw_max_amps,
            "cone_rated_amps": self.cone_rated_amps,
            "cone_min_amps": self.cone_min_amps,
            "cone_max_amps": self.cone_max_amps,
            "cone_stall_pct": self.cone_stall_pct,
            "belt_speed_mps": self.belt_speed_mps,
            "belt_rated_tph": self.belt_rated_tph,
            "screen_deck_id": self.screen_deck_id,
            "screen_width_m": self.screen_width_m,
            "screen_length_m": self.screen_length_m,
            "screen_capacity_tph": self.screen_capacity_tph,
            "screen_aperture_mm": self.screen_aperture_mm,
            "chute_block_pct": self.chute_block_pct,
            "chute_clear_pct": self.chute_clear_pct,
            "chute_hold_seconds": self.chute_hold_seconds,
            "chute_min_samples": self.chute_min_samples,
        }

    def feeder_window(self) -> tuple[float, float]:
        return (self.feeder_min_tph, self.feeder_max_tph)

    def jaw_window(self) -> tuple[float, float]:
        return (self.jaw_min_amps, self.jaw_max_amps)

    def cone_window(self) -> tuple[float, float]:
        return (self.cone_min_amps, self.cone_max_amps)

    def stall_amps(self) -> float:
        """The draw at which the cone is treated as stalled."""

        return round(self.cone_rated_amps * self.cone_stall_pct / 100.0, 3)


@dataclass(frozen=True)
class SafetySpec:
    """Protective envelopes shared by every line."""

    latch_hold_s: float
    drain_seconds: float
    trip_ramp_tph_per_second: float
    magnet_recovery_hold_s: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "latch_hold_s": self.latch_hold_s,
            "drain_seconds": self.drain_seconds,
            "trip_ramp_tph_per_second": self.trip_ramp_tph_per_second,
            "magnet_recovery_hold_s": self.magnet_recovery_hold_s,
        }


@dataclass(frozen=True)
class QualitySpec:
    """Product quality limits and the window a release is judged over."""

    undersize_min_pct: float
    oversize_max_pct: float
    window_seconds: float
    window_samples: int
    verdict_max_age_s: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "undersize_min_pct": self.undersize_min_pct,
            "oversize_max_pct": self.oversize_max_pct,
            "window_seconds": self.window_seconds,
            "window_samples": self.window_samples,
            "verdict_max_age_s": self.verdict_max_age_s,
        }


@dataclass(frozen=True)
class StockSpec:
    """The buffer bin, the ore it holds and how fast the level may be chased."""

    bin_capacity_m3: float
    bulk_density_t_per_m3: float
    level_low_pct: float
    level_high_pct: float
    level_max_age_s: float
    feed_gain_tph_per_pct: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "bin_capacity_m3": self.bin_capacity_m3,
            "bulk_density_t_per_m3": self.bulk_density_t_per_m3,
            "level_low_pct": self.level_low_pct,
            "level_high_pct": self.level_high_pct,
            "level_max_age_s": self.level_max_age_s,
            "feed_gain_tph_per_pct": self.feed_gain_tph_per_pct,
        }

    def target_level_pct(self) -> float:
        return round((self.level_low_pct + self.level_high_pct) / 2.0, 3)


@dataclass(frozen=True)
class PlantConfig:
    """Whole site configuration, including the shared control limits."""

    site: str
    generation: int
    lines: tuple[LineSpec, ...]
    safety: SafetySpec
    quality: QualitySpec
    stock: StockSpec
    confirmation_ttl_s: float = 1800.0
    baseline_ttl_s: float = 3600.0
    snapshot_ttl_s: float = 7200.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "site": self.site,
            "generation": self.generation,
            "lines": [line.as_dict() for line in self.lines],
            "safety": self.safety.as_dict(),
            "quality": self.quality.as_dict(),
            "stock": self.stock.as_dict(),
            "confirmation_ttl_s": self.confirmation_ttl_s,
            "baseline_ttl_s": self.baseline_ttl_s,
            "snapshot_ttl_s": self.snapshot_ttl_s,
        }

    def unit_ids(self) -> list[str]:
        return [line.unit for line in self.lines]

    def line(self, unit: str) -> LineSpec:
        for spec in self.lines:
            if spec.unit == unit:
                return spec
        raise ConfigError("unit is not configured", unit=unit, known=self.unit_ids())


def _require(raw: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in raw:
        raise ConfigError("required field is missing", field=key, section=where)
    return raw[key]


def _number(raw: Mapping[str, Any], key: str, where: str) -> float:
    value = _require(raw, key, where)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError("field must be numeric", field=key, section=where)
    return float(value)


def _integer(raw: Mapping[str, Any], key: str, where: str) -> int:
    value = _number(raw, key, where)
    if value != int(value):
        raise ConfigError("field must be a whole number", field=key, section=where)
    return int(value)


LINE_NUMBER_FIELDS = (
    "feeder_rated_tph",
    "feeder_min_tph",
    "feeder_max_tph",
    "feeder_ramp_tph_per_second",
    "jaw_gap_mm",
    "jaw_feed_mm",
    "jaw_product_mm",
    "jaw_rated_amps",
    "jaw_min_amps",
    "jaw_max_amps",
    "cone_rated_amps",
    "cone_min_amps",
    "cone_max_amps",
    "cone_stall_pct",
    "belt_speed_mps",
    "belt_rated_tph",
    "screen_width_m",
    "screen_length_m",
    "screen_capacity_tph",
    "screen_aperture_mm",
    "chute_block_pct",
    "chute_clear_pct",
    "chute_hold_seconds",
)


def line_from_dict(raw: Mapping[str, Any]) -> LineSpec:
    """Build one line specification from decoded JSON."""

    where = f"line {raw.get('unit', '?')}"
    numbers = {field: _number(raw, field, where) for field in LINE_NUMBER_FIELDS}
    return LineSpec(
        unit=str(_require(raw, "unit", where)),
        screen_deck_id=str(_require(raw, "screen_deck_id", where)),
        chute_min_samples=_integer(raw, "chute_min_samples", where),
        **numbers,
    )


def plant_config_from_dict(raw: Mapping[str, Any]) -> PlantConfig:
    """Build a configuration object from decoded JSON and check it holds."""

    where = "plant"
    lines_raw = _require(raw, "lines", where)
    if not isinstance(lines_raw, list):
        raise ConfigError("lines must be a list", section=where)
    safety = _require(raw, "safety", where)
    quality = _require(raw, "quality", where)
    stock = _require(raw, "stock", where)
    config = PlantConfig(
        site=str(_require(raw, "site", where)),
        generation=_integer(raw, "generation", where),
        lines=tuple(line_from_dict(item) for item in lines_raw),
        safety=SafetySpec(
            latch_hold_s=_number(safety, "latch_hold_s", "safety"),
            drain_seconds=_number(safety, "drain_seconds", "safety"),
            trip_ramp_tph_per_second=_number(safety, "trip_ramp_tph_per_second", "safety"),
            magnet_recovery_hold_s=_number(safety, "magnet_recovery_hold_s", "safety"),
        ),
        quality=QualitySpec(
            undersize_min_pct=_number(quality, "undersize_min_pct", "quality"),
            oversize_max_pct=_number(quality, "oversize_max_pct", "quality"),
            window_seconds=_number(quality, "window_seconds", "quality"),
            window_samples=_integer(quality, "window_samples", "quality"),
            verdict_max_age_s=_number(quality, "verdict_max_age_s", "quality"),
        ),
        stock=StockSpec(
            bin_capacity_m3=_number(stock, "bin_capacity_m3", "stock"),
            bulk_density_t_per_m3=_number(stock, "bulk_density_t_per_m3", "stock"),
            level_low_pct=_number(stock, "level_low_pct", "stock"),
            level_high_pct=_number(stock, "level_high_pct", "stock"),
            level_max_age_s=_number(stock, "level_max_age_s", "stock"),
            feed_gain_tph_per_pct=_number(stock, "feed_gain_tph_per_pct", "stock"),
        ),
        confirmation_ttl_s=_number(raw, "confirmation_ttl_s", where),
        baseline_ttl_s=_number(raw, "baseline_ttl_s", where),
        snapshot_ttl_s=_number(raw, "snapshot_ttl_s", where),
    )
    validate(config)
    return config


def load_config(path: Path | str) -> PlantConfig:
    """Read a configuration file from disk."""

    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError("configuration file is missing", path=str(source)) from exc
    except json.JSONDecodeError as exc:
        raise ConfigError("configuration file is not valid JSON", path=str(source), detail=str(exc)) from exc
    if not isinstance(raw, dict):
        raise ConfigError("configuration file must hold an object", path=str(source))
    return plant_config_from_dict(raw)


def validate(config: PlantConfig) -> None:
    """Reject configurations that break a site level envelope."""

    problems: list[str] = []
    if not config.site.strip():
        problems.append("site name is empty")
    if config.generation < 1:
        problems.append("generation must start at one")
    if not config.lines:
        problems.append("no line is configured")
    seen: set[str] = set()
    for line in config.lines:
        where = f"line {line.unit}"
        if line.unit in seen:
            problems.append(f"{where} is configured twice")
        seen.add(line.unit)
        if line.feeder_min_tph >= line.feeder_max_tph:
            problems.append(f"{where} feeder band is empty")
        if not line.feeder_min_tph <= line.feeder_rated_tph <= line.feeder_max_tph:
            problems.append(f"{where} rated feed sits outside the band")
        if line.feeder_ramp_tph_per_second <= 0:
            problems.append(f"{where} needs a positive feed ramp")
        if line.jaw_gap_mm <= 0 or line.jaw_product_mm <= 0:
            problems.append(f"{where} crusher gap and product size must be positive")
        if line.jaw_feed_mm < line.jaw_product_mm:
            problems.append(f"{where} jaw feed is finer than its product")
        if line.jaw_min_amps >= line.jaw_max_amps:
            problems.append(f"{where} jaw current band is inverted")
        if not line.jaw_min_amps <= line.jaw_rated_amps <= line.jaw_max_amps:
            problems.append(f"{where} rated jaw current sits outside the band")
        if line.cone_min_amps >= line.cone_max_amps:
            problems.append(f"{where} cone current band is inverted")
        if not 0 < line.cone_stall_pct <= 100:
            problems.append(f"{where} cone stall share must sit inside (0, 100]")
        if line.belt_speed_mps <= 0 or line.belt_rated_tph <= 0:
            problems.append(f"{where} belt speed and rating must be positive")
        if line.screen_width_m <= 0 or line.screen_length_m <= 0:
            problems.append(f"{where} screen deck dimensions must be positive")
        if line.screen_capacity_tph <= 0 or line.screen_aperture_mm <= 0:
            problems.append(f"{where} screen capacity and aperture must be positive")
        if line.chute_clear_pct >= line.chute_block_pct:
            problems.append(f"{where} chute thresholds are inverted")
        if line.chute_hold_seconds <= 0 or line.chute_min_samples < 1:
            problems.append(f"{where} chute window is out of range")
    decks: dict[str, str] = {}
    for line in config.lines:
        owner = decks.get(line.screen_deck_id)
        if owner is not None:
            problems.append(f"deck {line.screen_deck_id} is shared by {owner} and {line.unit}")
        decks[line.screen_deck_id] = line.unit
    safety = config.safety
    if safety.latch_hold_s < 0 or safety.magnet_recovery_hold_s < 0:
        problems.append("protective hold times must not be negative")
    if safety.drain_seconds <= 0 or safety.trip_ramp_tph_per_second <= 0:
        problems.append("drain time and trip ramp must be positive")
    quality = config.quality
    if not 0 < quality.undersize_min_pct <= 100:
        problems.append("undersize share must sit inside (0, 100]")
    if not 0 <= quality.oversize_max_pct < 100:
        problems.append("oversize share must sit inside [0, 100)")
    if quality.window_seconds <= 0 or quality.window_samples < 1:
        problems.append("quality window is out of range")
    if quality.verdict_max_age_s <= 0:
        problems.append("quality verdict budget must be positive")
    stock = config.stock
    if stock.bin_capacity_m3 <= 0 or stock.bulk_density_t_per_m3 <= 0:
        problems.append("bin capacity and bulk density must be positive")
    if stock.level_low_pct >= stock.level_high_pct:
        problems.append("bin level band is inverted")
    if stock.level_max_age_s <= 0 or stock.feed_gain_tph_per_pct <= 0:
        problems.append("level budget and feed gain must be positive")
    if config.confirmation_ttl_s <= 0 or config.baseline_ttl_s <= 0 or config.snapshot_ttl_s <= 0:
        problems.append("confirmation, baseline and snapshot lifetimes must be positive")
    if problems:
        raise ConfigError("configuration violates the site envelope", problems=problems)


def envelope_report(config: PlantConfig) -> dict[str, Any]:
    """Summarise the windows inside which the site may operate."""

    return {
        "site": config.site,
        "generation": config.generation,
        "lines": {
            line.unit: {
                "feed_tph": list(line.feeder_window()),
                "rated_tph": line.feeder_rated_tph,
                "feed_ramp_tph_per_s": line.feeder_ramp_tph_per_second,
                "jaw_amps": list(line.jaw_window()),
                "cone_amps": list(line.cone_window()),
                "cone_stall_amps": line.stall_amps(),
                "belt_speed_mps": line.belt_speed_mps,
                "belt_rated_tph": line.belt_rated_tph,
                "screen_capacity_tph": line.screen_capacity_tph,
                "screen_aperture_mm": line.screen_aperture_mm,
                "chute_pct": [line.chute_clear_pct, line.chute_block_pct],
            }
            for line in config.lines
        },
        "safety": config.safety.as_dict(),
        "quality": config.quality.as_dict(),
        "stock": config.stock.as_dict(),
        "lifetimes_s": {
            "confirmation": config.confirmation_ttl_s,
            "baseline": config.baseline_ttl_s,
            "snapshot": config.snapshot_ttl_s,
        },
    }


def diff_configs(current: PlantConfig, candidate: PlantConfig) -> dict[str, Any]:
    """Field by field difference used by the change preview endpoint."""

    changes: list[dict[str, Any]] = []
    current_lines = {line.unit: line.as_dict() for line in current.lines}
    candidate_lines = {line.unit: line.as_dict() for line in candidate.lines}
    for unit in sorted(set(current_lines) | set(candidate_lines)):
        before = current_lines.get(unit)
        after = candidate_lines.get(unit)
        if before == after:
            continue
        changes.append(
            {
                "scope": f"line.{unit}",
                "before": before,
                "after": after,
                "kind": "added" if before is None else ("removed" if after is None else "modified"),
            }
        )
    for section in ("safety", "quality", "stock"):
        before = getattr(current, section).as_dict()
        after = getattr(candidate, section).as_dict()
        if before != after:
            changes.append({"scope": section, "before": before, "after": after, "kind": "modified"})
    for field_name in ("site", "generation", "confirmation_ttl_s", "baseline_ttl_s", "snapshot_ttl_s"):
        before = getattr(current, field_name)
        after = getattr(candidate, field_name)
        if before != after:
            changes.append(
                {"scope": f"plant.{field_name}", "before": before, "after": after, "kind": "modified"}
            )
    return {"changes": changes, "count": len(changes)}
