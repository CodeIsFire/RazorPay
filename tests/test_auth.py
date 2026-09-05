"""The bearer-token guard on the state-changing endpoints (app/auth.py).

Three states to pin down, and the middle one is the whole reason the module
exists:

  * a token is configured  -> the right one passes, anything else is 401
  * no token, live creds   -> 503, because money can move and nothing guards it
  * no token, no live creds-> open, which is the test suite and a local checkout

The last of those is what keeps the other ~330 tests in this suite passing
without every one of them learning to send a header, so it is asserted here
rather than left as an accident of the other tests not failing.

/webhooks/razorpayx is exempt on purpose -- RazorpayX signs its deliveries and
cannot be told to send a bearer token. A test below holds that exemption in
place, because "the webhook started 401ing in production" is exactly the kind
of regression this file should catch before a deploy does.
"""
import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app

TOKEN = "s3cret-operator-token"

# One route per protected shape: no body, path param, query param, multipart.
# Every one of these changes state or spends money.
PROTECTED = [
    ("post", "/pipeline/reconcile"),
    ("post", "/pipeline/sync-payouts"),
    ("post", "/pipeline/route"),
    ("post", "/actions/1/confirm?outcome=processed"),
    ("post", "/exceptions/some-key/resolve"),
    ("post", "/exceptions/some-key/recheck"),
    ("post", "/demo/reset"),
    ("post", "/data/upload/ledger"),
    ("post", "/assistant/chat"),
]


@pytest.fixture
def token_configured(isolated_db, monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", TOKEN)
    return isolated_db


@pytest.fixture
def no_token_live_creds(isolated_db, monkeypatch):
    """No RR_API_TOKEN, but the three RazorpayX variables that flip the app
    onto the live executor. isolated_db blanks them, so this puts them back."""
    monkeypatch.setattr(config, "API_TOKEN", "")
    monkeypatch.setattr(config, "RAZORPAYX_KEY_ID", "rzp_live_ABC123")
    monkeypatch.setattr(config, "RAZORPAYX_KEY_SECRET", "shh")
    monkeypatch.setattr(config, "RAZORPAYX_ACCOUNT_NUMBER", "2323230000000000")
    return isolated_db


@pytest.mark.parametrize("method,path", PROTECTED)
def test_protected_routes_reject_a_missing_token(token_configured, method, path):
    with TestClient(app) as client:
        resp = getattr(client, method)(path)
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("method,path", PROTECTED)
def test_protected_routes_reject_a_wrong_token(token_configured, method, path):
    with TestClient(app) as client:
        resp = getattr(client, method)(path, headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_the_right_token_gets_through(token_configured):
    """Not 401 is the assertion -- /pipeline/reconcile's own success shape is
    test_health.py and the pipeline tests' business, not this file's."""
    with TestClient(app) as client:
        resp = client.post("/pipeline/reconcile",
                           headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code != 401


def test_bearer_scheme_is_case_insensitive(token_configured):
    """RFC 7235 says the scheme is case-insensitive; a proxy that rewrites it
    shouldn't lock the operator out."""
    with TestClient(app) as client:
        resp = client.post("/pipeline/reconcile",
                           headers={"Authorization": f"bearer {TOKEN}"})
    assert resp.status_code != 401


def test_a_bare_token_without_the_scheme_is_rejected(token_configured):
    with TestClient(app) as client:
        resp = client.post("/pipeline/reconcile", headers={"Authorization": TOKEN})
    assert resp.status_code == 401


@pytest.mark.parametrize("method,path", PROTECTED)
def test_live_credentials_without_a_token_refuse_the_request(
        no_token_live_creds, method, path):
    """The fail-closed case. Serving these open while a live executor is
    wired up is the state app/auth.py exists to make impossible."""
    with TestClient(app) as client:
        resp = getattr(client, method)(path)
    assert resp.status_code == 503
    assert "RR_API_TOKEN" in resp.json()["detail"]


def test_no_token_and_no_live_credentials_stays_open(isolated_db, monkeypatch):
    """A local checkout on the mock executor is not asked for a credential --
    this is what the rest of the suite relies on."""
    monkeypatch.setattr(config, "API_TOKEN", "")
    with TestClient(app) as client:
        resp = client.post("/pipeline/reconcile")
    assert resp.status_code not in (401, 503)


def test_read_only_routes_are_not_guarded(token_configured):
    """The dashboard fetches these on load with no user gesture behind them,
    and a token it would have to ship to the browser protects nothing."""
    with TestClient(app) as client:
        for path in ("/health", "/integration/status", "/funnel", "/exceptions",
                     "/audit", "/data/summary", "/analytics/daily"):
            assert client.get(path).status_code != 401, path


def test_the_webhook_is_not_behind_the_bearer_token(token_configured, monkeypatch):
    """RazorpayX cannot send one. Its HMAC check is its authentication -- so
    an unsigned delivery must fail on the signature (400), never on a missing
    bearer token (401)."""
    monkeypatch.setattr(config, "RAZORPAYX_WEBHOOK_SECRET", "whsec")
    with TestClient(app) as client:
        resp = client.post("/webhooks/razorpayx", json={"event": "payout.processed"})
    assert resp.status_code == 400


def test_integration_status_reports_whether_a_token_is_configured(token_configured):
    with TestClient(app) as client:
        assert client.get("/integration/status").json()["api_token_configured"] is True
