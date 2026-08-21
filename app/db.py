"""Thin SQLite wrapper. No ORM on purpose — the schema is small and stable
enough that raw SQL stays readable, and it keeps the dependency list short.
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import DB_PATH

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
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
