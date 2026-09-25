"""The configured site used when no configuration file is supplied."""

from __future__ import annotations

from .config import LineSpec, PlantConfig, QualitySpec, SafetySpec, StockSpec

DEFAULT_UNIT = "CP-1"
SECOND_UNIT = "CP-2"


def default_lines() -> tuple[LineSpec, ...]:
    """Two lines: one that carries the base load and one held in reserve."""

    return (
        LineSpec(
            unit=DEFAULT_UNIT,
            feeder_rated_tph=620.0,
            feeder_min_tph=180.0,
            feeder_max_tph=750.0,
            feeder_ramp_tph_per_second=12.0,
            jaw_gap_mm=110.0,
            jaw_feed_mm=750.0,
            jaw_product_mm=180.0,
            jaw_rated_amps=320.0,
            jaw_min_amps=90.0,
            jaw_max_amps=380.0,
            cone_rated_amps=240.0,
            cone_min_amps=70.0,
            cone_max_amps=300.0,
            cone_stall_pct=88.0,
            belt_speed_mps=1.8,
            belt_rated_tph=800.0,
            screen_deck_id="D1",
            screen_width_m=2.4,
            screen_length_m=6.0,
            screen_capacity_tph=700.0,
            screen_aperture_mm=32.0,
            chute_block_pct=78.0,
            chute_clear_pct=62.0,
            chute_hold_seconds=45.0,
            chute_min_samples=3,
        ),
        LineSpec(
            unit=SECOND_UNIT,
            feeder_rated_tph=430.0,
            feeder_min_tph=140.0,
            feeder_max_tph=520.0,
            feeder_ramp_tph_per_second=9.0,
            jaw_gap_mm=95.0,
            jaw_feed_mm=600.0,
            jaw_product_mm=150.0,
            jaw_rated_amps=260.0,
            jaw_min_amps=80.0,
            jaw_max_amps=310.0,
            cone_rated_amps=195.0,
            cone_min_amps=60.0,
            cone_max_amps=245.0,
            cone_stall_pct=90.0,
            belt_speed_mps=1.6,
            belt_rated_tph=560.0,
            screen_deck_id="D2",
            screen_width_m=2.0,
            screen_length_m=5.4,
            screen_capacity_tph=480.0,
            screen_aperture_mm=28.0,
            chute_block_pct=80.0,
            chute_clear_pct=64.0,
            chute_hold_seconds=40.0,
            chute_min_samples=3,
        ),
    )


def default_config() -> PlantConfig:
    """The site configuration the service falls back to."""

    return PlantConfig(
        site="north-pit",
        generation=1,
        lines=default_lines(),
        safety=SafetySpec(
            latch_hold_s=120.0,
            drain_seconds=90.0,
            trip_ramp_tph_per_second=40.0,
            magnet_recovery_hold_s=60.0,
        ),
        quality=QualitySpec(
            undersize_min_pct=88.0,
            oversize_max_pct=12.0,
            window_seconds=300.0,
            window_samples=4,
            verdict_max_age_s=45.0,
        ),
        stock=StockSpec(
            bin_capacity_m3=320.0,
            bulk_density_t_per_m3=1.85,
            level_low_pct=25.0,
            level_high_pct=85.0,
            level_max_age_s=40.0,
            feed_gain_tph_per_pct=9.0,
        ),
        confirmation_ttl_s=1800.0,
        baseline_ttl_s=3600.0,
        snapshot_ttl_s=7200.0,
    )
