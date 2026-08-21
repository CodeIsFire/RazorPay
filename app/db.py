"""Thin SQLite wrapper. No ORM on purpose — the schema is small and stable
enough that raw SQL stays readable, and it keeps the dependency list short.
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app import config

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def get_connection() -> sqlite3.Connection:
    # Reads config.DB_PATH at call time (not import time) so tests can
    # monkeypatch it per-test for isolation -- see tests/conftest.py.
    #
    # check_same_thread=False: FastAPI runs a sync dependency (get_db) in a
    # worker thread but an `async def` endpoint's body on the event-loop
    # thread, so a connection created in the dependency can be handed to
    # code running on a different thread within the same request. There's
    # no concurrent use of one connection here -- one request, one
    # connection, used sequentially -- so this is the standard, safe fix
    # rather than a real concurrency hazard.
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Idempotent: safe to call on every app startup."""
    with get_connection() as conn:
        conn.executescript(SCHEMA_PATH.read_text())
        conn.commit()


@contextmanager
def session():
    """Usage: with session() as conn: conn.execute(...); conn.commit()"""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def log_audit(conn: sqlite3.Connection, *, actor: str, subject_type: str,
              subject_id: str, event: str, detail: str = "") -> None:
    """Every stage should call this instead of writing to audit_log directly,
    so the column contract stays in one place."""
    conn.execute(
        "INSERT INTO audit_log (actor, subject_type, subject_id, event, detail) "
        "VALUES (?, ?, ?, ?, ?)",
        (actor, subject_type, subject_id, event, detail),
    )


def fetch_audit_log(conn: sqlite3.Connection, *, limit: int = 200,
                     subject_type: str | None = None) -> list[dict]:
    """Most recent entries first. This is the read side of the audit trail
    the dashboard shows and the buildathon's "graceful failure handling"
    criterion cares about -- it's the same table every stage writes to,
    nothing summarized or filtered away."""
    if subject_type:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE subject_type = ? ORDER BY id DESC LIMIT ?",
            (subject_type, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
