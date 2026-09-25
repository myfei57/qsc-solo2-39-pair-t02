"""The HTTP console: routes, refusals and the pages it renders."""

from __future__ import annotations

from pathlib import Path

from crushplant.console import ControlApp, Request
from tests.support import DEFAULT_UNIT, manual_runtime


def app_for(tmp_path: Path) -> ControlApp:
    return ControlApp(manual_runtime(tmp_path))


def get(app: ControlApp, path: str, **query: str):
    return app.handle(Request(method="GET", path=path, query=query))


def post(app: ControlApp, path: str, body: dict):
    return app.handle(Request(method="POST", path=path, body=body))


def test_the_health_endpoint_reports_the_watermark_and_the_lines(tmp_path: Path) -> None:
    response = get(app_for(tmp_path), "/healthz")

    assert response.status == 200
    assert response.payload["ok"] is True
    assert response.payload["units"] == ["CP-1", "CP-2"]
    assert response.payload["states"] == {"CP-1": "standby", "CP-2": "standby"}
    assert response.payload["watermark"]["sequence"] == 0
    assert response.payload["routes"] > 20


def test_the_state_endpoint_returns_every_component_of_every_line(tmp_path: Path) -> None:
    response = get(app_for(tmp_path), "/api/state")

    assert response.status == 200
    assert set(response.payload) == {"CP-1", "CP-2"}
    assert set(response.payload[DEFAULT_UNIT]) >= {"machine", "feeder", "jaw", "belt", "magnet", "chute", "gate"}
    assert response.payload[DEFAULT_UNIT]["machine"]["state"] == "standby"


def test_an_unknown_endpoint_and_a_wrong_method_are_reported_separately(tmp_path: Path) -> None:
    app = app_for(tmp_path)

    assert get(app, "/api/nothing").status == 404
    assert post(app, "/api/status", {}).status == 405
    assert get(app, "/api/routes").payload["count"] == len(app.router.routes())
    assert "/healthz" in app.router.paths()


def test_a_refused_write_keeps_its_status_and_is_written_down(tmp_path: Path) -> None:
    app = app_for(tmp_path)

    post(app, "/api/units/CP-1/begin", {})
    post(app, "/api/units/CP-1/stage", {"stage": "jaw-state"})
    post(app, "/api/units/CP-1/stage", {"stage": "magnet-ready"})
    post(app, "/api/magnet/trip", {"unit": DEFAULT_UNIT, "reason": "overbelt detector fault"})

    response = post(app, "/api/belt/start", {"unit": DEFAULT_UNIT})

    assert response.status == 409
    assert response.payload["error"] == "interlock"
    blocked = app.runtime.ledger.entries(outcome="blocked")
    assert blocked
    assert blocked[-1].unit == DEFAULT_UNIT
    assert "api.post.api/belt/start" == blocked[-1].action


def test_the_pages_render_html_from_the_live_state(tmp_path: Path) -> None:
    app = app_for(tmp_path)

    overview = get(app, "/")
    operations = get(app, "/operations")
    records = get(app, "/records")

    assert overview.content_type.startswith("text/html")
    assert "north-pit overview" in overview.payload
    assert DEFAULT_UNIT in overview.payload
    assert "feed preconditions" not in overview.payload
    assert "outstanding" in operations.payload
    assert "record stream" in records.payload


def test_the_audit_endpoint_applies_its_query_filters(tmp_path: Path) -> None:
    app = app_for(tmp_path)
    post(app, "/api/units/CP-1/level", {"level_pct": 64.0})
    post(app, "/api/units/CP-2/level", {"level_pct": 64.0})

    filtered = get(app, "/api/audit", unit="CP-2", action="bin.load")

    assert filtered.payload["count"] == 1
    assert filtered.payload["entries"][0]["unit"] == "CP-2"
    assert get(app, "/api/audit", outcome="blocked").payload["count"] == 0


def test_the_write_endpoints_drive_a_whole_bring_up(tmp_path: Path) -> None:
    app = app_for(tmp_path)
    post(
        app,
        "/api/units/CP-1/calibrate",
        {
            "kind": "scale",
            "points": [{"raw_counts": 120.0, "kg_per_m": 18.0}, {"raw_counts": 520.0, "kg_per_m": 74.0}],
        },
    )
    post(
        app,
        "/api/units/CP-1/calibrate",
        {
            "kind": "cone",
            "points": [
                {"tonnes_per_hour": 120.0, "amps": 96.0},
                {"tonnes_per_hour": 420.0, "amps": 168.0},
            ],
        },
    )
    post(app, "/api/units/CP-1/level", {"level_pct": 64.0})
    post(app, "/api/units/CP-1/confirm", {"target_tph": 600.0})
    post(app, "/api/units/CP-1/begin", {})
    for stage in ("jaw-state", "magnet-ready", "belt-start", "screen-start", "cone-start"):
        assert post(app, "/api/units/CP-1/stage", {"stage": stage}).status == 201

    fed = post(app, "/api/feeder/start", {"unit": DEFAULT_UNIT, "target_tph": 600.0})

    assert fed.status == 201
    assert fed.payload["state"] == "running"
    assert get(app, "/healthz").payload["states"][DEFAULT_UNIT] == "running"
    assert DEFAULT_UNIT in get(app, "/api/report").payload["totals"]["running"]


def test_the_scenario_endpoint_runs_a_script_and_reports_it(tmp_path: Path) -> None:
    app = app_for(tmp_path)

    response = post(app, "/api/scenarios/run", {"name": "cold-start"})

    assert response.status == 200
    assert response.payload["ok"] is True
    assert response.payload["scenario"] == "cold-start"
    assert post(app, "/api/scenarios/run", {"name": "cold-start"}).payload["ok"] is True
    assert post(app, "/api/scenarios/run", {"name": "no-such-run"}).status == 400


def test_the_records_endpoint_returns_the_watermark_trail(tmp_path: Path) -> None:
    app = app_for(tmp_path)
    post(app, "/api/units/CP-1/level", {"level_pct": 55.0})

    response = get(app, "/api/records", limit="5")

    assert response.payload["summary"]["watermark"]["sequence"] >= 1
    assert response.payload["watermark_history"][-1]["event"] == "commit"
    assert response.payload["history"][-1]["kind"] == "audit"
