import pytest

from app import config


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Points app.config.DB_PATH at a throwaway file for the duration of one
    test, so API-level tests (TestClient) don't read or write the shared
    dev DB and can't leak state into each other."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    return db_path
