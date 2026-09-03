"""`transactions.origin` -- where a row came from.

This exists for exactly one reason: a payout instruction that arrived in a
CSV is not the same as one the fixture generator produced, and the dispatch
path has to be able to tell them apart to refuse the former on production
credentials. Everything else it enables (badges, filters) is incidental.

The migration matters as much as the column. schema.sql is applied with
CREATE TABLE IF NOT EXISTS, so adding a column there is a no-op against every
database that already exists -- including the developer's. Without the
ALTER TABLE below, the first query naming `origin` fails with
"no such column" on exactly the machines that have real data.
"""
import sqlite3
from pathlib import Path

from app import config
from app.db import init_db
from app.ingest import parse_csv, replace
from app.load_fixtures import load_transactions

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"

LEDGER_CSV = (
    "external_ref,amount_paise,occurred_at,counterparty,narration\n"
    "ORD-1,150000,2026-09-01T10:00:00+00:00,Acme Traders,Payout\n"
)


def _fresh_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text())
    return conn


def _columns(conn):
    return {r[1] for r in conn.execute("PRAGMA table_info(transactions)")}


def test_the_schema_defines_an_origin_column():
    assert "origin" in _columns(_fresh_db())


def test_rows_loaded_without_an_origin_are_generated():
    # Every fixture row, and every hand-built test row that predates this
    # column, must keep loading unchanged.
    conn = _fresh_db()
    load_transactions(conn, [{
        "source": "ledger", "external_ref": "LED-1", "amount_paise": 100,
        "occurred_at": "2026-09-01T10:00:00+00:00",
    }])
    conn.commit()
    assert conn.execute("SELECT origin FROM transactions").fetchone()[0] == "generated"


def test_an_uploaded_row_is_marked_as_uploaded():
    conn = _fresh_db()
    replace(conn, "ledger", parse_csv("ledger", LEDGER_CSV))
    assert conn.execute("SELECT origin FROM transactions").fetchone()[0] == "upload"


def test_replacing_a_source_does_not_leak_the_marker_to_other_sources(isolated_db):
    conn = _fresh_db()
    load_transactions(conn, [{
        "source": "gateway", "external_ref": "PAY-1", "amount_paise": 100,
        "occurred_at": "2026-09-01T10:00:00+00:00",
    }])
    conn.commit()
    replace(conn, "ledger", parse_csv("ledger", LEDGER_CSV))

    origins = dict(conn.execute("SELECT source, origin FROM transactions"))
    assert origins == {"gateway": "generated", "ledger": "upload"}


def test_init_db_adds_the_column_to_a_database_that_predates_it(tmp_path, monkeypatch):
    """The migration. Without it, an existing dev database silently lacks the
    column and every query naming it fails."""
    db_path = tmp_path / "old.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))

    # A database shaped the way it was before this column existed.
    old = sqlite3.connect(str(db_path))
    old.executescript(SCHEMA_PATH.read_text().replace(
        "origin          TEXT NOT NULL DEFAULT 'generated'\n"
        "                    CHECK (origin IN ('generated', 'upload')),\n", ""))
    old.execute("""INSERT INTO transactions (source, external_ref, amount_paise, occurred_at)
                   VALUES ('ledger','OLD-1',100,'2026-09-01T10:00:00+00:00')""")
    old.commit()
    assert "origin" not in _columns(old)
    old.close()

    init_db()

    migrated = sqlite3.connect(str(db_path))
    assert "origin" in _columns(migrated)
    # A pre-existing row is 'generated': it certainly did not arrive by upload.
    assert migrated.execute(
        "SELECT origin FROM transactions WHERE external_ref='OLD-1'").fetchone()[0] == "generated"


def test_init_db_is_still_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "twice.db"))
    init_db()
    init_db()  # must not raise "duplicate column name: origin"
    conn = sqlite3.connect(str(tmp_path / "twice.db"))
    assert "origin" in _columns(conn)
