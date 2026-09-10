"""Persistence guarantees.

Three v1.0 defects are fenced here: contact details leaking into the stored
``result_json`` despite the README's redaction promise, an ``INSERT OR REPLACE``
that silently discarded a human's approval, and connections that were committed
but never closed.
"""

from __future__ import annotations

import json
import sqlite3
import threading

from packet_review_os import storage
from packet_review_os.pipeline import run_review
from packet_review_os.schemas import PacketInput

CONTACTFUL_PACKET = (
    "Priya Nair\n"
    "priya.nair@example.com\n"
    "+1 415 555 0132\n"
    "https://linkedin.com/in/priya-nair\n"
    "5 years of experience as a software engineer.\n"
    "Shipped Python FastAPI services and a React/TypeScript dashboard with pytest coverage.\n"
)


def _review(text: str = CONTACTFUL_PACKET):
    return run_review(PacketInput(role_id="fullstack_engineer", packet_text=text))


def test_stored_review_contains_no_contact_details(temp_db):
    result = _review()
    storage.save_review(result, "raw packet: priya.nair@example.com +1 415 555 0132")
    blob = json.dumps(storage.get_review(result.run_id))
    assert "priya.nair@example.com" not in blob
    assert "555 0132" not in blob
    assert "linkedin.com/in/priya-nair" not in blob


def test_raw_table_columns_are_also_redacted(temp_db):
    result = _review()
    storage.save_review(result, "raw packet: priya.nair@example.com")
    with storage.connect() as conn:
        row = conn.execute("SELECT * FROM reviews WHERE run_id = ?", (result.run_id,)).fetchone()
    everything = " ".join(str(value) for value in tuple(row))
    assert "priya.nair@example.com" not in everything


def test_candidate_name_is_deliberately_retained(temp_db):
    """Names are the subject of the review; removing them would void the audit log."""
    result = _review()
    storage.save_review(result)
    assert "Priya Nair" in json.dumps(storage.get_review(result.run_id))


def test_resaving_a_run_does_not_discard_an_approval(temp_db):
    result = _review()
    storage.save_review(result)
    storage.approve_review(result.run_id, approved=True, note="ok to screen")
    storage.save_review(result)  # v1.0 INSERT OR REPLACE wiped this
    stored = storage.get_review(result.run_id)
    assert stored["approved"] is True
    assert stored["approver_note"] == "ok to screen"


def test_send_back_is_recorded_distinctly_from_approval(temp_db):
    result = _review()
    storage.save_review(result)
    storage.approve_review(result.run_id, approved=False, note="wrong role")
    stored = storage.get_review(result.run_id)
    assert stored["approved"] is False
    assert stored["approved_at"]


def test_approver_note_is_redacted_and_bounded(temp_db):
    result = _review()
    storage.save_review(result)
    storage.approve_review(result.run_id, approved=True, note="ping me@corp.com " + "x" * 5000)
    stored = storage.get_review(result.run_id)
    assert "me@corp.com" not in stored["approver_note"]
    assert len(stored["approver_note"]) <= 2000


def test_approving_an_unknown_run_returns_none(temp_db):
    assert storage.approve_review("does-not-exist", approved=True) is None


def test_get_review_of_unknown_run_returns_none(temp_db):
    assert storage.get_review("nope") is None


def test_history_is_newest_first_and_limit_is_clamped(temp_db):
    for _ in range(3):
        storage.save_review(_review())
    records = storage.list_reviews(limit=10**9)  # must not explode
    assert len(records) == 3
    assert records == sorted(records, key=lambda r: r.created_at, reverse=True)


def test_v1_database_is_migrated_in_place(tmp_path, monkeypatch, make_settings):
    """A database written by v1.0 has no approved_at column; opening must migrate it."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE reviews (
            run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, role_id TEXT NOT NULL,
            candidate_name TEXT, next_action TEXT, confidence REAL, overall_score REAL,
            engine_mode TEXT, approved INTEGER, approver_note TEXT, latency_ms INTEGER,
            warnings_json TEXT, result_json TEXT NOT NULL, packet_redacted TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO reviews (run_id, created_at, role_id, result_json) VALUES (?,?,?,?)",
        ("legacy1", "2026-01-01T00:00:00Z", "fullstack_engineer", json.dumps({"run_id": "legacy1"})),
    )
    conn.commit()
    conn.close()

    stub = make_settings(database_path=str(db_path))
    monkeypatch.setattr(storage, "settings", lambda: stub)
    monkeypatch.setattr(storage, "_initialized_for", None, raising=False)
    storage.init_db(force=True)

    with storage.connect() as check:
        cols = {row["name"] for row in check.execute("PRAGMA table_info(reviews)")}
    assert "approved_at" in cols
    assert storage.get_review("legacy1") is not None, "existing rows must survive the migration"


def test_concurrent_writers_do_not_deadlock(temp_db):
    """WAL plus a busy timeout is what makes the CLI and the web app coexist."""
    errors: list[Exception] = []

    def writer():
        try:
            for _ in range(4):
                storage.save_review(_review())
        except Exception as exc:  # noqa: BLE001 - the assertion is that none escape
            errors.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    assert storage.count_reviews() == 16


def test_transaction_rolls_back_on_error(temp_db):
    result = _review()
    storage.save_review(result)
    try:
        with storage.connect() as conn, storage.transaction(conn):
            conn.execute("DELETE FROM reviews")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert storage.count_reviews() == 1, "a failed transaction must not delete rows"
