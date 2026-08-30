from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok():
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_startup_creates_tables():
    """Correctness check for M0: the schema actually gets created, not
    just 'the app boots'."""
    import sqlite3

    from app.config import DB_PATH

    with TestClient(app):
        pass  # triggers startup -> init_db()

    conn = sqlite3.connect(DB_PATH)
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert {"transactions", "audit_log"}.issubset(tables)


def test_connections_are_configured_for_concurrent_access(tmp_path, monkeypatch):
    """Without these, a webhook arriving while the dashboard polls raises
    'database is locked' and the delivery is lost -- and a lost
    payout.processed is the only thing that would ever have moved that
    payout to a terminal state."""
    from app import config, db

    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "concurrency.db"))
    db.init_db()

    conn = db.get_connection()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    conn.close()


def test_a_writer_does_not_lock_out_a_concurrent_reader(tmp_path, monkeypatch):
    from app import config, db

    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "wal.db"))
    db.init_db()

    writer = db.get_connection()
    reader = db.get_connection()
    try:
        # An open write transaction used to block every reader outright.
        writer.execute("BEGIN IMMEDIATE")
        db.log_audit(writer, actor="test", subject_type="x", subject_id="1",
                     event="mid_transaction", detail=None)
        assert reader.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0
        writer.commit()
        assert reader.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 1
    finally:
        writer.close()
        reader.close()
