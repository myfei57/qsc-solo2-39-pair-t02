"""The three operator pages, rendered from the live state and nothing else."""

from __future__ import annotations

import html as html_module
from typing import Any, Callable

from ..report import site_report

STYLE = """
body { font-family: ui-sans-serif, system-ui, sans-serif; margin: 0; background: #10131a; color: #e7ecf3; }
header { padding: 16px 24px; background: #1b2130; border-bottom: 1px solid #2b3446; }
h1 { font-size: 18px; margin: 0 0 4px; }
nav a { color: #8fb7ff; margin-right: 14px; text-decoration: none; }
main { padding: 20px 24px 48px; }
table { border-collapse: collapse; width: 100%; margin-bottom: 24px; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #2b3446; font-size: 13px; }
th { color: #93a2bb; font-weight: 600; }
.ok { color: #58d68d; }
.bad { color: #ff8a80; }
section { margin-bottom: 28px; }
"""


def _document(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{html_module.escape(title)}</title><style>{STYLE}</style></head>"
        "<body><header>"
        f"<h1>{html_module.escape(title)}</h1>"
        "<nav><a href=\"/\">overview</a><a href=\"/operations\">operations</a>"
        "<a href=\"/records\">records</a><a href=\"/api/routes\">api</a></nav>"
        f"</header><main>{body}</main></body></html>"
    )


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{html_module.escape(str(name))}</th>" for name in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html_module.escape(str(cell))}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def overview(runtime: Any) -> str:
    """The board: one row per line with its state, feed, bin and latch."""

    report = site_report(runtime)
    rows = [
        [
            unit["unit"],
            unit["state"],
            unit["cycle"],
            f"{unit['feed_tph']} t/h",
            f"{unit['belt_tph']} t/h",
            unit["bin_level_pct"],
            "open" if unit["gate_ok"] else "blocked",
            unit["latch_reason"] or "-",
        ]
        for unit in report["units"]
    ]
    body = "<section><h2>lines</h2>"
    body += _table(
        ["unit", "state", "cycle", "feed", "belt", "bin %", "gate", "latch"],
        rows,
    )
    body += "</section><section><h2>totals</h2>"
    body += _table(
        ["feed t/h", "tonnage t", "running", "latched"],
        [
            [
                report["totals"]["feed_tph"],
                report["totals"]["tonnage_t"],
                ", ".join(report["totals"]["running"]) or "-",
                ", ".join(report["totals"]["latched"]) or "-",
            ]
        ],
    )
    body += "</section>"
    return _document(f"{runtime.config.site} overview", body)


def operations(runtime: Any) -> str:
    """The per line detail a shift operator reads while the line runs."""

    moment = runtime.clock.now()
    blocks: list[str] = []
    for unit, line in sorted(runtime.lines.items()):
        status = line.status(moment)
        gate = status["gate"]
        checks = [[check["name"], "ok" if check["ok"] else "unmet", check["detail"]] for check in gate["checks"]]
        blocks.append(
            f"<section><h2>{html_module.escape(unit)}</h2>"
            + _table(["check", "state", "detail"], checks)
            + _table(
                ["stage", "sequence"],
                [
                    ["start", ", ".join(status["machine"]["start"]["completed"]) or "-"],
                    ["outstanding", ", ".join(status["machine"]["start"]["outstanding"]) or "-"],
                    ["stop", ", ".join(status["machine"]["stop"]["completed"]) or "-"],
                ],
            )
            + "</section>"
        )
    return _document(f"{runtime.config.site} operations", "".join(blocks))


def records(runtime: Any) -> str:
    """The record stream, its watermark and the refusals the gate wrote down."""

    summary = runtime.stream.summary()
    watermark = summary["watermark"]
    entries = runtime.ledger.tail(25)
    body = "<section><h2>record stream</h2>"
    body += _table(
        ["lines", "watermark", "visible", "pending", "voided"],
        [[summary["journal_lines"], watermark["sequence"], summary["visible"], summary["pending"], summary["voided"]]],
    )
    body += "</section><section><h2>ledger tail</h2>"
    body += _table(
        ["sequence", "at", "unit", "action", "outcome", "actor"],
        [[entry.sequence, entry.at, entry.unit, entry.action, entry.outcome, entry.actor] for entry in entries],
    )
    body += "</section>"
    return _document(f"{runtime.config.site} records", body)


RENDERERS: dict[str, Callable[[Any], str]] = {
    "overview": overview,
    "operations": operations,
    "records": records,
}


def page_names() -> list[tuple[str, str]]:
    """The path each renderer answers on."""

    return [("/", "overview"), ("/operations", "operations"), ("/records", "records")]
