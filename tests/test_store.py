"""The document store and the journal that sit under every component."""

from __future__ import annotations

from pathlib import Path

import pytest

from crushplant.errors import Conflict, InvalidRequest, RecordNotFound, StoreError
from crushplant.store.documents import DocumentStore, safe_doc_id
from crushplant.store.journal import RecordJournal
from tests.support import START


def test_a_document_round_trips_with_its_revision(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path / "state")
    first = store.save("feeder.CP-1.drive", {"running": False}, START)
    second = store.save("feeder.CP-1.drive", {"running": True}, START)

    assert first.revision == 1
    assert second.revision == 2
    assert store.load("feeder.CP-1.drive").payload == {"running": True}
    assert store.revision("feeder.CP-1.drive") == 2


def test_a_compare_and_set_write_is_refused_when_the_document_moved(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path / "state")
    store.save("jaw.CP-1.state", {"revision": 1}, START)

    with pytest.raises(Conflict) as failure:
        store.save_if("jaw.CP-1.state", 0, {"revision": 9}, START)

    assert failure.value.details["actual_revision"] == 1
    assert store.save_if("jaw.CP-1.state", 1, {"revision": 2}, START).revision == 2


def test_a_document_identifier_cannot_escape_the_state_directory() -> None:
    assert safe_doc_id("  jaw.CP-1.state ") == "jaw.CP-1.state"
    for bad in ("", "   ", "../escape", "a/b", "a\\b", "..", "x" * 120):
        with pytest.raises(InvalidRequest):
            safe_doc_id(bad)


def test_a_missing_document_is_reported_and_can_be_deleted_once(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path / "state")
    assert store.try_load("nothing.here") is None
    with pytest.raises(RecordNotFound):
        store.load("nothing.here")

    store.save("belt.CP-1.drive", {"running": True}, START)
    assert store.delete("belt.CP-1.drive") is True
    assert store.delete("belt.CP-1.drive") is False


def test_the_store_reports_which_documents_it_holds(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path / "state")
    store.save("belt.CP-1.drive", {"running": True}, START)
    store.save("screen.CP-1.drive", {"running": True}, START)

    stats = store.stats()

    assert stats["documents"] == 2
    assert stats["document_ids"] == ["belt.CP-1.drive.json", "screen.CP-1.drive.json"]
    assert Path(stats["root"]).is_dir()


def test_an_unreadable_document_is_reported_as_a_store_error(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path / "state")
    store.path_for("chute.CP-1.state").write_text("{not json", encoding="utf-8")

    with pytest.raises(StoreError):
        store.load("chute.CP-1.state")


def test_the_journal_refuses_a_reference_on_a_record_that_is_not_a_void(tmp_path: Path) -> None:
    journal = RecordJournal(tmp_path / "records" / "operations.jsonl", fsync=False)
    journal.append("audit", START, payload={"action": "one"})

    with pytest.raises(InvalidRequest):
        journal.append("audit", START, payload={"action": "two"}, ref=1)
    with pytest.raises(InvalidRequest):
        journal.append("void", START, payload={"reason": "why"})
    with pytest.raises(InvalidRequest):
        journal.append("   ", START)

    assert journal.count() == 1
    assert journal.count("audit") == 1
    assert journal.kinds() == ["audit"]
    assert [record.sequence for record in journal.for_unit("CP-1")] == []
    assert journal.tail(1)[0].sequence == 1
    assert journal.after(0) == journal.records()
    assert journal.upto(1) == journal.records()
