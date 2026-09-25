"""Restart behaviour: what a rebuilt process sees and replays."""

from __future__ import annotations

from pathlib import Path

from tests.support import DEFAULT_UNIT, advance, bring_up, clock_of, manual_runtime, reload_runtime


def test_a_restart_keeps_the_committed_stream_unchanged(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    advance(runtime, 120.0)
    before = [record.as_dict() for record in runtime.stream.visible()]

    reloaded = reload_runtime(runtime)

    assert [record.as_dict() for record in reloaded.stream.visible()] == before
    assert reloaded.health()["replayed_on_start"]["applied"] == 0
    assert line.parts.jaw.state().revision == reloaded.line(DEFAULT_UNIT).parts.jaw.state().revision


def test_a_restart_replays_the_records_staged_above_the_watermark(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    bring_up(runtime)
    runtime.stream.stage(
        "audit",
        runtime.clock.now(),
        unit=DEFAULT_UNIT,
        actor="control",
        payload={"action": "half-written", "outcome": "ok", "detail": {}},
    )
    assert runtime.stream.summary()["pending"] == 1

    reloaded = reload_runtime(runtime)

    assert reloaded.health()["replayed_on_start"]["applied"] == 1
    assert reloaded.health()["startup_projections"]["actions"]["half-written"] == 1
    assert reloaded.stream.summary()["pending"] == 0


def test_a_restart_keeps_the_generation_and_the_live_confirmations(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    runtime.set_generation("liner changed", "engineer")
    runtime.line(DEFAULT_UNIT).confirm(600.0, runtime.config.confirmation_ttl_s, runtime.clock.now(), "test")

    reloaded = reload_runtime(runtime)

    assert reloaded.generations.generation() == 2
    assert reloaded.generations.history()[-1]["generation"] == 1
    assert len(reloaded.confirmations.pending(reloaded.clock.now())) == 1


def test_a_restart_keeps_the_calibrations_and_the_recorded_tonnage(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    line.measure_feed(580.0, runtime.clock.now(), "test")
    advance(runtime, 180.0)
    tonnage = line.parts.feeder.total_tonnes()
    assert tonnage > 0

    reloaded = reload_runtime(runtime)
    restored = reloaded.line(DEFAULT_UNIT)

    assert restored.parts.feeder.total_tonnes() == tonnage
    assert restored.parts.bin.state().level_pct == line.parts.bin.state().level_pct
    assert restored.parts.scale.read(520.0, reloaded.clock.now(), "test").generation == 1


def test_a_restart_does_not_resurrect_a_voided_record(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    sequence = line.parts.jaw.state().committed_sequence
    runtime.stream.tombstone(sequence, runtime.clock.now(), "operator", "wrong line")

    reloaded = reload_runtime(runtime)

    assert reloaded.stream.voided_sequences() == {sequence: "wrong line"}
    assert reloaded.line(DEFAULT_UNIT).parts.jaw.committed() is False
    assert reloaded.health()["ok"] is True


def test_a_restart_keeps_the_line_state_and_its_cycle_number(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    line = bring_up(runtime)
    advance(runtime, 60.0)
    line.stop(runtime.clock.now(), "test")

    reloaded = reload_runtime(runtime)

    assert reloaded.line(DEFAULT_UNIT).state() == "standby"
    assert reloaded.line(DEFAULT_UNIT).parts.machine.cycle() == 1
    assert reloaded.line(DEFAULT_UNIT).parts.machine.stages()
    assert clock_of(reloaded).now() == clock_of(runtime).now()


def test_a_restart_keeps_the_batch_registry_and_the_ledger_totals(tmp_path: Path) -> None:
    runtime = manual_runtime(tmp_path)
    bring_up(runtime)
    code = runtime.issuer.issue("batch", runtime.clock.now())
    runtime.batches.open(code, DEFAULT_UNIT, "production", runtime.clock.now(), "test")
    outcomes = runtime.ledger.outcomes()

    reloaded = reload_runtime(runtime)

    assert reloaded.batches.codes() == [code]
    assert reloaded.ledger.outcomes() == outcomes
    assert reloaded.verdicts.summary()["recorded"] == runtime.verdicts.summary()["recorded"]
