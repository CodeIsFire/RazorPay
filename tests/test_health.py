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
