"""Bearer-token guard for the endpoints that change state or move money.

Until now nothing authenticated this app -- app/router.py's
_uploaded_on_live_key() says so out loud, and refuses to pay a CSV-supplied
destination on a live key precisely because provenance was the only check
left. This module is the missing half: it puts a credential in front of the
endpoints themselves, so /pipeline/route and /demo/reset stop being things
any caller who can reach the URL may do.

Three states, and the middle one is the point:

  * RR_API_TOKEN set -> require `Authorization: Bearer <token>`. Compared
    with compare_digest, for the same reason webhooks.py does.

  * RR_API_TOKEN unset, live RazorpayX credentials present -> refuse the
    request with 503. Money can move here and nothing is guarding it; serving
    the endpoint open would be strictly worse than serving nothing. This is
    the fail-closed case, and it is deliberately not overridable by
    configuration -- an escape hatch would let a deployment opt back into the
    exact state this module exists to end.

  * RR_API_TOKEN unset, no live credentials -> allow. This is the test suite
    (TestClient hits these endpoints several hundred times) and a local
    checkout driving the mock executor. Nothing here can reach a bank.

The webhook endpoint is deliberately NOT covered: RazorpayX signs its
deliveries and cannot be told to send a bearer token, so its HMAC check in
app/webhooks.py is its authentication. Read-only endpoints are not covered
either -- the dashboard fetches them on load with no user gesture behind it,
and a token that has to be present just to render a page it would have to
ship to the browser anyway is a token that protects nothing.
"""
import hmac

from fastapi import HTTPException, Request

from app import config


def live_credentials_configured() -> bool:
    """The same three-variable test app/main.py's executor factory uses to
    decide between the live and mock executors. Imported from here rather
    than duplicated there so the two can never drift into a state where one
    thinks the app can move money and the other doesn't."""
    return bool(
        config.RAZORPAYX_KEY_ID
        and config.RAZORPAYX_KEY_SECRET
        and config.RAZORPAYX_ACCOUNT_NUMBER
    )


def _bearer(header: str) -> str:
    """The token out of an Authorization header, or "" for anything that
    isn't a well-formed Bearer. Case-insensitive on the scheme, per RFC 7235."""
    scheme, _, token = (header or "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def require_auth(request: Request) -> None:
    """FastAPI dependency. Raises 401/503, or returns None to let the request
    through. Declared with `dependencies=[Depends(require_auth)]` on the
    route, since it injects nothing into the handler."""
    if not config.API_TOKEN:
        if live_credentials_configured():
            raise HTTPException(
                status_code=503,
                detail="This endpoint changes state and RR_API_TOKEN is not set, "
                       "while live RazorpayX credentials are. Set RR_API_TOKEN to "
                       "enable it.",
            )
        return

    presented = _bearer(request.headers.get("Authorization", ""))
    if not presented or not hmac.compare_digest(presented, config.API_TOKEN):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
