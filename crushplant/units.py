"""Value coercion and the throughput conversions the line needs."""

from __future__ import annotations

import math
from typing import Any

from .errors import InvalidRequest

SECONDS_PER_HOUR = 3600.0


def require_finite(value: Any, label: str) -> float:
    """Coerce to a finite float or reject the request."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidRequest(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise InvalidRequest(f"{label} must be finite")
    return number


def require_int(value: Any, label: str) -> int:
    """Coerce to an integer, accepting integral floats."""

    number = require_finite(value, label)
    if number != int(number):
        raise InvalidRequest(f"{label} must be a whole number")
    return int(number)


def require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidRequest(f"{label} must be a boolean")
    return value


def require_positive(value: Any, label: str) -> float:
    number = require_finite(value, label)
    if number <= 0:
        raise InvalidRequest(f"{label} must be positive")
    return number


def require_label(value: Any, label: str, maximum: int = 64) -> str:
    """A short non-empty identifier or free-text label."""

    if not isinstance(value, str) or not value.strip():
        raise InvalidRequest(f"{label} must be a non-empty string")
    text = value.strip()
    if len(text) > maximum:
        raise InvalidRequest(f"{label} must be at most {maximum} characters")
    return text


def clamp(value: float, low: float, high: float) -> float:
    if low > high:
        raise InvalidRequest("clamp bounds are inverted", low=low, high=high)
    return max(low, min(high, value))


def round_to(value: float, digits: int = 3) -> float:
    return round(value, digits)


def percent_of(value: float, reference: float) -> float:
    if reference == 0:
        raise InvalidRequest("reference must not be zero")
    return round_to(100.0 * value / reference, 3)


def margin(value: float, limit: float) -> float:
    """How far a reading sits below its ceiling; negative means over it."""

    return round_to(limit - value, 3)


def belt_tonnes_per_hour(kg_per_m: float, belt_speed_mps: float) -> float:
    """Belt conveyor throughput from the load carried per metre of belt.

    A belt carrying ``q`` kg/m while running at ``v`` m/s moves ``q * v`` kg/s,
    which is ``3.6 * q * v`` tonnes per hour.
    """

    if kg_per_m < 0:
        raise InvalidRequest("belt load must not be negative")
    if belt_speed_mps <= 0:
        raise InvalidRequest("belt speed must be positive")
    return round_to(3.6 * kg_per_m * belt_speed_mps, 4)


def belt_load_kg_per_m(tonnes_per_hour: float, belt_speed_mps: float) -> float:
    """Inverse of :func:`belt_tonnes_per_hour`."""

    if tonnes_per_hour < 0:
        raise InvalidRequest("throughput must not be negative")
    if belt_speed_mps <= 0:
        raise InvalidRequest("belt speed must be positive")
    return round_to(tonnes_per_hour / (3.6 * belt_speed_mps), 6)


def apply_scale(raw_counts: float, zero_kg_per_m: float, span_kg_per_m: float) -> float:
    """Map a load cell signal onto kg/m with the two calibration constants."""

    return round_to(zero_kg_per_m + span_kg_per_m * raw_counts, 6)


def crusher_load_pct(amps: float, rated_amps: float) -> float:
    """Draw as a share of the machine rating."""

    if rated_amps <= 0:
        raise InvalidRequest("rated current must be positive")
    if amps < 0:
        raise InvalidRequest("current must not be negative")
    return round_to(100.0 * amps / rated_amps, 3)


def specific_throughput(tonnes_per_hour: float, width_m: float, length_m: float) -> float:
    """Screening duty in tonnes per hour per square metre of deck."""

    if width_m <= 0 or length_m <= 0:
        raise InvalidRequest("deck dimensions must be positive")
    if tonnes_per_hour < 0:
        raise InvalidRequest("throughput must not be negative")
    return round_to(tonnes_per_hour / (width_m * length_m), 4)


def level_to_volume_m3(level_pct: float, capacity_m3: float) -> float:
    if capacity_m3 <= 0:
        raise InvalidRequest("capacity must be positive")
    return round_to(capacity_m3 * level_pct / 100.0, 4)


def volume_to_level_pct(volume_m3: float, capacity_m3: float) -> float:
    if capacity_m3 <= 0:
        raise InvalidRequest("capacity must be positive")
    return round_to(100.0 * volume_m3 / capacity_m3, 3)


def tonnes_from_volume(volume_m3: float, bulk_density_t_per_m3: float) -> float:
    if bulk_density_t_per_m3 <= 0:
        raise InvalidRequest("bulk density must be positive")
    return round_to(volume_m3 * bulk_density_t_per_m3, 4)


def reduction_ratio(feed_mm: float, product_mm: float) -> float:
    """How far the machine takes the ore down; feed is the coarse side."""

    if product_mm <= 0:
        raise InvalidRequest("product size must be positive")
    if feed_mm < product_mm:
        raise InvalidRequest("feed size must not be finer than the product size")
    return round_to(feed_mm / product_mm, 3)


def passing_fraction(undersize_t: float, total_t: float) -> float:
    """Share of a sample that reached the product size, in percent."""

    if total_t <= 0:
        raise InvalidRequest("sample mass must be positive")
    if undersize_t < 0:
        raise InvalidRequest("undersize mass must not be negative")
    return round_to(100.0 * undersize_t / total_t, 3)


def rate_per_hour(mass_t: float, seconds: float) -> float:
    """Turn an accumulated mass over an interval into a tonnes/hour rate."""

    if seconds <= 0:
        raise InvalidRequest("interval must be positive")
    if mass_t < 0:
        raise InvalidRequest("accumulated mass must not be negative")
    return round_to(mass_t * SECONDS_PER_HOUR / seconds, 4)
