"""The tag namespace every addressable object has to be registered in."""

from __future__ import annotations

import pytest

from crushplant.errors import InvalidRequest, NameConflict, RecordNotFound
from crushplant.ns.ids import parse_code, site_code
from crushplant.ns.namespace import KIND_JAW, KIND_UNIT, SignalSpec, TagNamespace
from crushplant.runtime import build_namespace
from crushplant.defaults import DEFAULT_UNIT, default_config


def test_registering_the_same_tag_twice_is_refused() -> None:
    namespace = TagNamespace()
    namespace.register(SignalSpec("CP-1.jaw", KIND_JAW, "A", "primary crusher draw", "crushing", DEFAULT_UNIT))

    with pytest.raises(NameConflict):
        namespace.register(SignalSpec("CP-1.jaw", KIND_JAW, "A", "again", "crushing", DEFAULT_UNIT))
    with pytest.raises(InvalidRequest):
        namespace.register(SignalSpec("   ", KIND_JAW, "A", "blank", "crushing"))


def test_tags_can_be_listed_by_kind_and_grouped_by_area() -> None:
    namespace = TagNamespace()
    namespace.register_all(
        [
            SignalSpec("CP-1", KIND_UNIT, "", "line CP-1", "site", DEFAULT_UNIT),
            SignalSpec("CP-1.jaw", KIND_JAW, "A", "primary crusher draw", "crushing", DEFAULT_UNIT),
            SignalSpec("CP-1.cone", KIND_JAW, "A", "secondary crusher draw", "crushing", DEFAULT_UNIT),
        ]
    )

    assert namespace.tags() == ["CP-1", "CP-1.cone", "CP-1.jaw"]
    assert namespace.tags(KIND_JAW) == ["CP-1.cone", "CP-1.jaw"]
    assert namespace.areas() == ["crushing", "site"]
    assert namespace.by_area()["crushing"] == ["CP-1.cone", "CP-1.jaw"]
    assert namespace.counts_by_kind() == {KIND_UNIT: 1, KIND_JAW: 2}
    assert namespace.summary()["tags"] == 3


def test_an_unknown_tag_is_reported_rather_than_invented() -> None:
    namespace = TagNamespace()

    assert namespace.has("CP-1.jaw") is False
    assert namespace.describe("CP-1.jaw") == {"tag": "CP-1.jaw", "registered": False, "spec": None}
    with pytest.raises(RecordNotFound):
        namespace.get("CP-1.jaw")


def test_the_site_namespace_registers_a_tag_for_every_line_component() -> None:
    namespace = build_namespace(default_config())

    for tag in ("CP-1", "CP-1.jaw", "CP-1.jaw-state", "CP-1.cone", "CP-1.belt-scale", "CP-2.feed", "line.batches"):
        assert namespace.has(tag) is True
    assert namespace.get("CP-1.deck.D1").unit_of_measure == "t/h"
    assert namespace.specs(KIND_UNIT)[0].unit == "CP-1"


def test_a_code_is_read_back_with_its_own_parts() -> None:
    parsed = parse_code("northpit-btch-20260406-0007")

    assert parsed.site == "NORTHPIT"
    assert parsed.kind == "BTCH"
    assert parsed.serial == 7
    assert parsed.text == "NORTHPIT-BTCH-20260406-0007"
    assert site_code("north pit") == "NORTHPIT"
    with pytest.raises(InvalidRequest):
        parse_code("NORTHPIT-20260406-0007")
    with pytest.raises(InvalidRequest):
        site_code("x")
