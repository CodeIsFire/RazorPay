import pytest

from app import config


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Points app.config.DB_PATH at a throwaway file for the duration of one
    test, so API-level tests (TestClient) don't read or write the shared
    dev DB and can't leak state into each other.

    Also blanks the RazorpayX credential settings so these tests stay
    hermetic regardless of what's actually configured in the developer's
    local .env (e.g. real test-mode credentials set up for manual live
    testing) -- otherwise get_payout_executor() would silently pick the
    live executor mid test-suite. Individual tests that need a specific
    value (e.g. RAZORPAYX_WEBHOOK_SECRET) override it afterward via their
    own monkeypatch.setattr call, which layers on top of this fine since
    it's the same monkeypatch instance for the duration of one test."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(config, "RAZORPAYX_KEY_ID", "")
    monkeypatch.setattr(config, "RAZORPAYX_KEY_SECRET", "")
    monkeypatch.setattr(config, "RAZORPAYX_ACCOUNT_NUMBER", "")
    monkeypatch.setattr(config, "RAZORPAYX_WEBHOOK_SECRET", "")
    return db_path
