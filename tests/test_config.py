"""_int_env / _float_env / _resolve_db_path.

These exist because a blank (not absent) numeric env var crashed the app at
import time on a real deployment, before a single route could report why --
Vercel's env-var UI writes an unconfigured field as "" rather than omitting
it, and os.getenv(name, default) only substitutes the default when the var is
absent.

Tested as plain functions, not by reloading app.config: this file's
load_dotenv(override=True) mutates the real process environment from .env on
every import, by design (see the comment above it) -- reloading the module in
a test would repollute os.environ for every test that runs afterward, which
is exactly the isolation tests/conftest.py's isolated_db fixture exists to
guarantee. A pure function needs no such reload.
"""
from pathlib import Path

from app.config import _float_env, _int_env, _resolve_db_path


class TestIntEnv:
    def test_blank_falls_back_to_the_default(self, monkeypatch):
        # The exact failure mode seen in production: RR_RECONCILE_INTERVAL_SECONDS
        # was SET to "", and int("") raised ValueError at import, before FastAPI
        # ever got a chance to serve a single route.
        monkeypatch.setenv("RR_RECONCILE_INTERVAL_SECONDS", "")
        assert _int_env("RR_RECONCILE_INTERVAL_SECONDS", "0") == 0

    def test_absent_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.delenv("RR_SYNC_PAYOUTS_INTERVAL_SECONDS", raising=False)
        assert _int_env("RR_SYNC_PAYOUTS_INTERVAL_SECONDS", "5") == 5

    def test_a_real_value_is_used_and_parsed_as_int(self, monkeypatch):
        monkeypatch.setenv("RR_SYNC_PAYOUTS_INTERVAL_SECONDS", "30")
        result = _int_env("RR_SYNC_PAYOUTS_INTERVAL_SECONDS", "5")
        assert result == 30
        assert isinstance(result, int)

    def test_a_genuinely_invalid_value_still_raises(self, monkeypatch):
        # Blank is tolerated because it means "unset". A typo is not the same
        # thing and should still fail loudly rather than silently default.
        monkeypatch.setenv("RR_SYNC_PAYOUTS_INTERVAL_SECONDS", "soon")
        try:
            _int_env("RR_SYNC_PAYOUTS_INTERVAL_SECONDS", "5")
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for a non-numeric value")


class TestFloatEnv:
    def test_blank_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv("RR_ASSISTANT_INJECTION_CUTOFF", "")
        assert _float_env("RR_ASSISTANT_INJECTION_CUTOFF", "0.8") == 0.8

    def test_a_real_value_is_used_and_parsed_as_float(self, monkeypatch):
        monkeypatch.setenv("RR_ASSISTANT_INJECTION_CUTOFF", "0.5")
        assert _float_env("RR_ASSISTANT_INJECTION_CUTOFF", "0.8") == 0.5


class TestResolveDbPath:
    DATA_DIR = Path("/repo/data")

    def test_defaults_under_data_dir_when_not_on_vercel(self):
        assert _resolve_db_path(None, on_vercel=False, data_dir=self.DATA_DIR) == \
            "/repo/data/recon_recover.db"

    def test_defaults_to_tmp_on_vercel(self):
        # data/ ships in the repo but is read-only outside /tmp on Vercel's
        # function filesystem -- init_db() would fail to create the file there.
        assert _resolve_db_path(None, on_vercel=True, data_dir=self.DATA_DIR) == \
            "/tmp/recon_recover.db"

    def test_explicit_path_wins_on_vercel_too(self):
        # A real persistent target should never be silently overridden by the
        # /tmp fallback just because the app happens to be running on Vercel.
        result = _resolve_db_path("/data/custom.db", on_vercel=True, data_dir=self.DATA_DIR)
        assert result == "/data/custom.db"

    def test_explicit_path_wins_locally(self):
        result = _resolve_db_path("/tmp/wherever.db", on_vercel=False, data_dir=self.DATA_DIR)
        assert result == "/tmp/wherever.db"
