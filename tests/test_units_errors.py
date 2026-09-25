"""Value coercion, the unit conversions and the error taxonomy."""

from __future__ import annotations

import pytest

from crushplant.errors import (
    Conflict,
    CrushError,
    DurabilityError,
    InterlockBlocked,
    InvalidRequest,
    LatchActive,
    LimitExceeded,
    StaleRecord,
    payload_for,
    status_for,
)
from crushplant.units import (
    apply_scale,
    belt_load_kg_per_m,
    belt_tonnes_per_hour,
    clamp,
    crusher_load_pct,
    level_to_volume_m3,
    margin,
    passing_fraction,
    percent_of,
    rate_per_hour,
    reduction_ratio,
    require_bool,
    require_finite,
    require_int,
    require_label,
    require_positive,
    round_to,
    specific_throughput,
    tonnes_from_volume,
    volume_to_level_pct,
)


def test_value_coercion_rejects_the_shapes_it_cannot_use() -> None:
    assert require_finite(3, "value") == 3.0
    assert require_int(4.0, "count") == 4
    assert require_bool(True, "flag") is True
    assert require_label("  feed  ", "label") == "feed"
    assert require_positive(2.5, "rate") == 2.5
    for bad in (True, "3", None):
        with pytest.raises(InvalidRequest):
            require_finite(bad, "value")
    with pytest.raises(InvalidRequest):
        require_int(2.5, "count")
    with pytest.raises(InvalidRequest):
        require_bool(1, "flag")
    with pytest.raises(InvalidRequest):
        require_label("", "label")
    with pytest.raises(InvalidRequest):
        require_label("x" * 80, "label")
    with pytest.raises(InvalidRequest):
        require_positive(0, "rate")
    with pytest.raises(InvalidRequest):
        clamp(1.0, 5.0, 2.0)


def test_the_belt_conversions_are_inverses_of_each_other() -> None:
    assert belt_tonnes_per_hour(74.0, 1.8) == 479.52
    assert belt_load_kg_per_m(479.52, 1.8) == 74.0
    assert apply_scale(520.0, 1.2, 0.14) == 74.0
    assert belt_load_kg_per_m(0.0, 1.8) == 0.0
    with pytest.raises(InvalidRequest):
        belt_tonnes_per_hour(10.0, 0.0)
    with pytest.raises(InvalidRequest):
        belt_tonnes_per_hour(-1.0, 1.8)
    with pytest.raises(InvalidRequest):
        belt_load_kg_per_m(-1.0, 1.8)


def test_the_level_and_mass_conversions_follow_the_bin() -> None:
    assert level_to_volume_m3(50.0, 320.0) == 160.0
    assert volume_to_level_pct(160.0, 320.0) == 50.0
    assert tonnes_from_volume(160.0, 1.85) == 296.0
    with pytest.raises(InvalidRequest):
        level_to_volume_m3(50.0, 0.0)
    with pytest.raises(InvalidRequest):
        tonnes_from_volume(160.0, 0.0)


def test_the_small_helpers_keep_their_contracts() -> None:
    assert round_to(12.3456, 2) == 12.35
    assert percent_of(50.0, 200.0) == 25.0
    assert margin(210.0, 300.0) == 90.0
    assert margin(310.0, 300.0) == -10.0
    assert crusher_load_pct(160.0, 320.0) == 50.0
    assert specific_throughput(600.0, 2.4, 6.0) == 41.6667
    assert reduction_ratio(750.0, 180.0) == 4.167
    assert passing_fraction(228.0, 250.0) == 91.2
    assert rate_per_hour(15.0, 90.0) == 600.0
    with pytest.raises(InvalidRequest):
        percent_of(1.0, 0.0)
    with pytest.raises(InvalidRequest):
        reduction_ratio(100.0, 180.0)
    with pytest.raises(InvalidRequest):
        passing_fraction(1.0, 0.0)
    with pytest.raises(InvalidRequest):
        rate_per_hour(1.0, 0.0)


def test_every_error_maps_to_a_status_and_a_payload() -> None:
    cases: list[tuple[CrushError, int, str]] = [
        (InvalidRequest("bad field"), 400, "invalid-request"),
        (Conflict("moved on"), 409, "conflict"),
        (InterlockBlocked("feed", ["screen running=False"]), 409, "interlock"),
        (LatchActive("CP-1.feed", "chute loaded"), 423, "latched"),
        (StaleRecord("CP-1.feed", "expired"), 409, "stale"),
        (DurabilityError("CP-1.jaw-state", stage="jaw-state"), 409, "not-durable"),
        (LimitExceeded("setpoint is outside the band", unit="CP-1"), 422, "limit-exceeded"),
    ]

    for error, status, code in cases:
        assert status_for(error) == status
        assert payload_for(error)["error"] == code
        assert error.as_payload()["message"]

    assert status_for(RuntimeError("boom")) == 500
    assert payload_for(RuntimeError("boom")) == {"error": "internal", "message": "boom"}


def test_an_error_carries_the_details_its_caller_needs() -> None:
    error = InterlockBlocked("feed", ["screen running=False", "bin low at 4.0%"])

    assert error.unmet == ["screen running=False", "bin low at 4.0%"]
    assert error.details["action"] == "feed"
    assert "2 unmet precondition" in str(error)

    stale = StaleRecord("CP-1.belt-scale", "expired at 06:30", slip_id="CP-1.feed-1-1")

    assert stale.subject == "CP-1.belt-scale"
    assert stale.reason == "expired at 06:30"
    assert stale.details["slip_id"] == "CP-1.feed-1-1"

    latch = LatchActive("CP-1.feed", "chute loaded", requested="release")

    assert latch.target == "CP-1.feed"
    assert latch.details["requested"] == "release"
