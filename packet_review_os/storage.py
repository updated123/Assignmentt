from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import ROOT, settings
from .privacy import scrub_payload, scrub_text
from .schemas import ReviewRecord, ReviewResult

logger = logging.getLogger("packet_review_os.storage")

SCHEMA_VERSION = 2

_init_lock = threading.Lock()
_initialized_for: str | None = None


def _db_path() -> Path:
    path = Path(settings().database_path)
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Open a connection that is always closed, with concurrency pragmas set.

    v1.0 used a bare sqlite3 connection as a context manager, which commits but
    never closes, leaking a handle per request. WAL plus a busy timeout keeps the
    web app and a concurrent CLI run from tripping "database is locked".
    """
    conn = sqlite3.connect(_db_path(), timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run statements in one immediate transaction so read-modify-write is atomic."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def init_db(force: bool = False) -> None:
    """Create the schema once per process/database. Safe to call repeatedly."""
    global _initialized_for
    target = str(_db_path())
    if not force and _initialized_for == target:
        return
    with _init_lock:
        if not force and _initialized_for == target:
            return
        with connect() as conn, transaction(conn):
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reviews (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    role_id TEXT NOT NULL,
                    candidate_name TEXT,
                    next_action TEXT,
                    confidence REAL,
                    overall_score REAL,
                    engine_mode TEXT,
                    approved INTEGER,
                    approver_note TEXT,
                    approved_at TEXT,
                    latency_ms INTEGER,
                    warnings_json TEXT,
                    result_json TEXT NOT NULL,
                    packet_redacted TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_reviews_created_at ON reviews (created_at DESC)")
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(reviews)")}
            if "approved_at" not in cols:  # migrate a v1 database in place
                conn.execute("ALTER TABLE reviews ADD COLUMN approved_at TEXT")
                logger.info("Migrated reviews table: added approved_at.")
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        _initialized_for = target
        logger.info("Review store ready at %s (schema v%d).", target, SCHEMA_VERSION)


def save_review(result: ReviewResult, packet_redacted: str = "") -> None:
    """Persist a review. Contact details are redacted before the row is written.

    Uses an upsert that preserves an existing approval decision, so re-saving a
    run_id cannot silently discard the fact that a human already signed off.
    """
    init_db()
    payload = scrub_payload(json.loads(result.model_dump_json()))
    with connect() as conn, transaction(conn):
        conn.execute(
            """
            INSERT INTO reviews (
                run_id, created_at, role_id, candidate_name, next_action,
                confidence, overall_score, engine_mode, approved, approver_note,
                approved_at, latency_ms, warnings_json, result_json, packet_redacted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, '', NULL, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                created_at = excluded.created_at,
                role_id = excluded.role_id,
                candidate_name = excluded.candidate_name,
                next_action = excluded.next_action,
                confidence = excluded.confidence,
                overall_score = excluded.overall_score,
                engine_mode = excluded.engine_mode,
                latency_ms = excluded.latency_ms,
                warnings_json = excluded.warnings_json,
                result_json = excluded.result_json,
                packet_redacted = excluded.packet_redacted
            """,
            (
                result.run_id,
                result.created_at,
                result.role_id,
                result.candidate_name,
                result.next_action.value,
                result.confidence,
                result.overall_score,
                result.engine_mode.value,
                result.latency_ms,
                json.dumps([scrub_text(w) for w in result.warnings]),
                json.dumps(payload),
                scrub_text(packet_redacted),
            ),
        )


def approve_review(run_id: str, approved: bool, note: str = "") -> dict[str, Any] | None:
    """Record a human decision atomically. Returns the updated review, or None."""
    init_db()
    decided_at = datetime.now(UTC).isoformat()
    clean_note = scrub_text(note or "")[:2000]
    with connect() as conn, transaction(conn):
        row = conn.execute("SELECT result_json FROM reviews WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            return None
        result = json.loads(row["result_json"])
        result["approved"] = approved
        result["approver_note"] = clean_note
        result["approved_at"] = decided_at
        conn.execute(
            """
            UPDATE reviews
               SET approved = ?, approver_note = ?, approved_at = ?, result_json = ?
             WHERE run_id = ?
            """,
            (1 if approved else 0, clean_note, decided_at, json.dumps(result), run_id),
        )
    logger.info("Review %s marked %s by operator.", run_id, "approved" if approved else "sent back")
    return result


def list_reviews(limit: int = 25) -> list[ReviewRecord]:
    init_db()
    limit = max(1, min(int(limit), 500))
    with connect() as conn, closing(conn.cursor()) as cur:
        rows = cur.execute(
            "SELECT * FROM reviews ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    records = []
    for row in rows:
        records.append(
            ReviewRecord(
                run_id=row["run_id"],
                created_at=row["created_at"],
                role_id=row["role_id"],
                candidate_name=row["candidate_name"] or "Unknown candidate",
                next_action=row["next_action"] or "",
                confidence=row["confidence"] or 0,
                overall_score=row["overall_score"] or 0,
                engine_mode=row["engine_mode"] or "",
                approved=None if row["approved"] is None else bool(row["approved"]),
                approver_note=row["approver_note"] or "",
                latency_ms=row["latency_ms"] or 0,
                warnings=json.loads(row["warnings_json"] or "[]"),
            )
        )
    return records


def get_review(run_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT result_json, approved, approver_note, approved_at FROM reviews WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    if not row:
        return None
    data = json.loads(row["result_json"])
    data["approved"] = None if row["approved"] is None else bool(row["approved"])
    data["approver_note"] = row["approver_note"] or ""
    data["approved_at"] = row["approved_at"]
    return data


def count_reviews() -> int:
    init_db()
    with connect() as conn:
        return int(conn.execute("SELECT COUNT(*) AS n FROM reviews").fetchone()["n"])
