"""Addressable tag namespace and identifier issuance."""

from __future__ import annotations

from .ids import ISSUABLE_KINDS, IdIssuer, TagCode, normalise_code, parse_code, site_code
from .namespace import (
    KIND_BATCH,
    KIND_BELT,
    KIND_BIN,
    KIND_CALIBRATION,
    KIND_CHUTE,
    KIND_CONE,
    KIND_FEEDER,
    KIND_JAW,
    KIND_LATCH,
    KIND_LEVEL,
    KIND_MAGNET,
    KIND_SCREEN,
    KIND_UNIT,
    SignalSpec,
    TagNamespace,
)

__all__ = [
    "ISSUABLE_KINDS",
    "KIND_BATCH",
    "KIND_BELT",
    "KIND_BIN",
    "KIND_CALIBRATION",
    "KIND_CHUTE",
    "KIND_CONE",
    "KIND_FEEDER",
    "KIND_JAW",
    "KIND_LATCH",
    "KIND_LEVEL",
    "KIND_MAGNET",
    "KIND_SCREEN",
    "KIND_UNIT",
    "IdIssuer",
    "SignalSpec",
    "TagCode",
    "TagNamespace",
    "normalise_code",
    "parse_code",
    "site_code",
]
