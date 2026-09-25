"""Typed readers for JSON request bodies."""

from __future__ import annotations

from typing import Any, Mapping

from ..errors import InvalidRequest
from ..units import require_bool, require_finite, require_int, require_label

_MISSING = object()


def _lookup(body: dict[str, Any], key: str, default: Any) -> Any:
    if key not in body or body[key] is None:
        if default is _MISSING:
            raise InvalidRequest(f"missing field: {key}")
        return default
    return body[key]


def number(body: dict[str, Any], key: str, default: Any = _MISSING) -> float:
    raw = _lookup(body, key, default)
    if raw is default:
        return raw
    return require_finite(raw, key)


def query_integer(query: Mapping[str, str], key: str, default: int) -> int:
    """Read a whole number out of a query string."""

    raw = query.get(key)
    if raw in (None, ""):
        return default
    try:
        return require_int(int(raw), key)
    except ValueError as exc:
        raise InvalidRequest(f"{key} must be a whole number", value=raw) from exc


def integer(body: dict[str, Any], key: str, default: Any = _MISSING) -> int:
    raw = _lookup(body, key, default)
    if raw is default:
        return raw
    return require_int(raw, key)


def flag(body: dict[str, Any], key: str, default: Any = _MISSING) -> bool:
    raw = _lookup(body, key, default)
    if raw is default:
        return raw
    return require_bool(raw, key)


def text(body: dict[str, Any], key: str, default: Any = _MISSING) -> str:
    raw = _lookup(body, key, default)
    if raw is default:
        return raw
    return require_label(raw, key)


def optional_text(body: dict[str, Any], key: str, default: str = "") -> str:
    raw = body.get(key)
    if raw is None:
        return default
    if not isinstance(raw, str):
        raise InvalidRequest(f"{key} must be a string")
    return raw.strip()


def pairs(body: dict[str, Any], key: str, *fields: str) -> list[dict[str, float]]:
    """Read a list of calibration points, as the scale and cone calibrations use."""

    raw = _lookup(body, key, _MISSING)
    if not isinstance(raw, list) or not raw:
        raise InvalidRequest(f"{key} must be a non-empty list")
    points: list[dict[str, float]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise InvalidRequest(f"{key}[{index}] must be an object")
        point: dict[str, float] = {}
        for field in fields:
            if field not in item:
                raise InvalidRequest(f"{key}[{index}] is missing {field}")
            point[field] = require_finite(item[field], f"{key}[{index}].{field}")
        points.append(point)
    return points
