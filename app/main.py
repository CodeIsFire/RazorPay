import asyncio
import contextlib
import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import config
from app.analytics import compute_daily_reconciliation, compute_exception_intelligence
from app.assistant import build_messages, check_rate_limit
from app.auth import live_credentials_configured, require_auth
from app.groq_client import GroqError, chat, injection_score
from app.classify import classify_and_persist_from_db
from app.demo import reset_demo_data
from app.db import fetch_audit_log, fetch_exception_detail, fetch_exceptions, get_connection, init_db
from app.funnel import compute_funnel
from app.ingest import SOURCES as UPLOAD_SOURCES
from app.ingest import (
    IngestValidationError,
    build_template,
    parse_csv,
    preview_replace,
    summarize,
)
from app.ingest import replace as replace_source
from app.live_executor import RazorpayXPayoutExecutor, RazorpayXPayoutStatusFetcher
from app.reconcile import ACTUAL_SOURCES
from app.router import (
    MockPayoutExecutor,
    confirm_action,
    sync_payout_statuses,
    preview_route,
    recheck_exception,
    resolve_exception,
    route_open_exceptions,
)
from app.webhooks import WebhookVerificationError, handle_webhook, verify_signature


logger = logging.getLogger(__name__)


async def _reconcile_on_a_timer(interval_seconds: int) -> None:
    """Runs the same match -> classify -> persist pipeline /pipeline/reconcile
    triggers, just on a schedule instead of a click -- see
    config.RECONCILE_INTERVAL_SECONDS. One bad iteration must not kill the
    loop, since a transient DB hiccup shouldn't silence reconciliation for
    the rest of the process's life."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            conn = get_connection()
            try:
                classify_and_persist_from_db(conn)
            finally:
                conn.close()
        except Exception:
            logger.exception("scheduled reconcile pass failed")


async def _sync_payouts_on_a_timer(interval_seconds: int) -> None:
    """Asks RazorpayX what actually happened to every payout still in flight,
    on a schedule instead of a click -- see config.SYNC_PAYOUTS_INTERVAL_SECONDS.

    Webhooks stay the primary path and stay authoritative: this only ever looks
    at actions no webhook has already resolved. It earns its keep when delivery
    fails or no public tunnel is up, which would otherwise leave a payout stuck
    at 'processing' forever with nothing to revisit it.

    Two things this loop must not do. It must not run the sync inline: the
    RazorpayX client is synchronous httpx and issues one request per in-flight
    payout, so calling it directly on the event loop would stall every dashboard
    request for the length of that whole sweep. And it must not die on a bad
    pass, for the same reason the reconcile timer doesn't -- a transient network
    or DB hiccup shouldn't silence status syncing for the rest of the process's
    life.
    """
    def _one_pass() -> dict:
        conn = get_connection()
        try:
            return sync_payout_statuses(conn, RazorpayXPayoutStatusFetcher())
        finally:
            conn.close()

    while True:
        await asyncio.sleep(interval_seconds)
        # Credentials can be absent (tests, a fresh checkout). Skip quietly
        # rather than raising on a timer nobody asked to fail.
        if not (config.RAZORPAYX_KEY_ID and config.RAZORPAYX_KEY_SECRET):
            continue
        try:
            summary = await asyncio.to_thread(_one_pass)
        except Exception:
            logger.exception("scheduled payout sync failed")
            continue
        # Quiet by default: at a 5s cadence, logging every no-op pass would bury
        # the log in "checked 10, confirmed 0" within minutes.
        if summary.get("confirmed") or summary.get("error"):
            logger.info("payout sync: %s", summary)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    tasks = []
    if config.RECONCILE_INTERVAL_SECONDS > 0:
        tasks.append(asyncio.create_task(_reconcile_on_a_timer(config.RECONCILE_INTERVAL_SECONDS)))
    if config.SYNC_PAYOUTS_INTERVAL_SECONDS > 0:
        tasks.append(asyncio.create_task(
            _sync_payouts_on_a_timer(config.SYNC_PAYOUTS_INTERVAL_SECONDS)))
    yield
    # Cancel every timer on shutdown, not just the first -- a task left running
    # past lifespan keeps a DB handle open and logs into a closing process.
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Reconcile -> Recover", lifespan=lifespan)


# This page renders bank references and can dispatch payouts, so it says out
# loud what is allowed to load and where it may talk to. Until now there was no
# CSP at all, which is precisely why the old dashboard refused to load d3 or
# Lenis from a CDN and vendored them same-origin instead.
#
# Each directive, and why it is shaped this way:
#   script-src 'self'    nothing third-party executes here, full stop. The
#                        analytics SDK is bundled into our own JS rather than
#                        loaded from openpanel.dev for exactly this reason.
#   connect-src          our own API, plus OpenPanel's ingest host and nothing
#                        else -- so this is also the list of places data can go.
#   style-src            'unsafe-inline' is load-bearing, not laziness: React
#                        writes inline style attributes throughout (bar widths,
#                        chart geometry), and CSP counts those as inline styles.
#                        Google Fonts serves the Inter/IBM Plex Mono stylesheet.
#   font-src             where those two faces are actually fetched from.
#   img-src data:        inline SVG/data URIs only; no remote images are used.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "connect-src 'self' https://api.openpanel.dev",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data:",
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'none'",
    ]
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    # The dashboard is the only consumer and never frames anything or sniffs
    # types; these two cost nothing and close off clickjacking and MIME
    # confusion.
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


def get_db():
    """Fresh connection per request -- sqlite3 connections aren't safe to
    share across threads, and requests here are cheap enough that pooling
    would be complexity this project doesn't need yet."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def get_payout_executor():
    """Live RazorpayX only activates once KEY_ID, KEY_SECRET and
    ACCOUNT_NUMBER are all configured -- with only a key_id/secret in
    .env (no account_number yet), this correctly stays on the mock rather
    than half-configuring something that would just fail every call."""
    if live_credentials_configured():
        return RazorpayXPayoutExecutor()
    return MockPayoutExecutor()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/integration/status")
def integration_status(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """What's actually configured right now, without leaking secrets --
    useful for confirming which executor /pipeline/route will use before
    it does something with real (test-mode) money."""
    live_ready = live_credentials_configured()
    # Payees are no longer provisioned out of band -- a ledger row carries
    # its own fund account, so the useful readiness signal is how many
    # ledger rows are actually dispatchable, not how many entries somebody
    # remembered to put in a side-car file.
    payable = conn.execute(
        """SELECT COUNT(*) FROM transactions
           WHERE source='ledger' AND fund_account_type IS NOT NULL"""
    ).fetchone()[0]
    ledger_total = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE source='ledger'"
    ).fetchone()[0]

    return {
        "executor": "live" if live_ready else "mock",
        "key_configured": bool(config.RAZORPAYX_KEY_ID),
        "account_number_configured": bool(config.RAZORPAYX_ACCOUNT_NUMBER),
        "webhook_secret_configured": bool(config.RAZORPAYX_WEBHOOK_SECRET),
        # Whether the state-changing endpoints sit behind a bearer token
        # (app/auth.py). False alongside executor "live" is the one
        # combination that refuses those endpoints outright rather than
        # serving them open.
        "api_token_configured": bool(config.API_TOKEN),
        # bool() only, never the key -- the dashboard uses this to decide whether
        # the help button opens the assistant or falls back to its blurb.
        "assistant_configured": bool(config.GROQ_API_KEY),
        "payable_ledger_rows": payable,
        "ledger_rows": ledger_total,
    }


@app.post("/pipeline/reconcile", dependencies=[Depends(require_auth)])
def run_reconcile_pipeline(actual_source: str = "gateway",
                            conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """Runs match -> classify -> persist against whatever is currently in
    the transactions table. Idempotent: re-POSTing after nothing new has
    been ingested creates no new exceptions (see app/classify.py).

    actual_source picks which non-ledger source to reconcile against --
    'gateway' (default, RazorpayX test-mode transactions) or
    'bank_statement' (real bank statement rows loaded with
    source='bank_statement'). Reconciling against both means POSTing here
    twice, once per source -- see reconcile.py's ACTUAL_SOURCES."""
    if actual_source not in ACTUAL_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"actual_source must be one of {ACTUAL_SOURCES}, got {actual_source!r}",
        )
    new_exception_keys = classify_and_persist_from_db(conn, actual_source)
    return {"new_exceptions": new_exception_keys, "count": len(new_exception_keys),
            "actual_source": actual_source}


@app.get("/funnel")
def get_funnel(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return compute_funnel(conn)


@app.get("/analytics/exceptions")
def get_exception_intelligence(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """The backlog broken down by cause, age and counterparty -- "what should
    I chase first?", where /funnel only answers "how much is outstanding?".
    Ages come from when the money moved, not when the pipeline last ran; see
    app/analytics.py."""
    return compute_exception_intelligence(conn)


@app.get("/analytics/daily")
def get_daily_reconciliation(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """Ledger value per business day, split reconciled vs still outstanding --
    "which days' payouts are still stuck?". Days come from when the payout
    occurred, so this is a real timeline rather than one column for whenever
    the pipeline last ran. Its total deliberately differs from
    /analytics/exceptions: gateway-side orphans have no ledger row and so no
    business day. See app/analytics.py."""
    days = compute_daily_reconciliation(conn)
    return {"count": len(days), "days": days}


@app.get("/audit")
def get_audit(limit: int = 200, subject_type: Optional[str] = None,
              conn: sqlite3.Connection = Depends(get_db)) -> dict:
    entries = fetch_audit_log(conn, limit=limit, subject_type=subject_type)
    return {"count": len(entries), "entries": entries}


@app.get("/exceptions")
def get_exceptions(status: Optional[str] = None, cause: Optional[str] = None,
                    limit: int = 500, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    entries = fetch_exceptions(conn, status=status, cause=cause, limit=limit)
    return {"count": len(entries), "entries": entries}


@app.get("/exceptions/{exception_key}/detail")
def get_exception_detail(exception_key: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """The conflict itself -- every raw ledger/actual-side transaction row
    behind this exception, so a human reviewing it (or deciding whether to
    resolve it) isn't working from refs and a summary alone. See
    app/db.py's fetch_exception_detail for why 'duplicate' needs the
    reference_id correlation, not just the stored refs."""
    detail = fetch_exception_detail(conn, exception_key)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"no such exception: {exception_key}")
    return detail


@app.post("/pipeline/sync-payouts", dependencies=[Depends(require_auth)])
def run_payout_sync(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """Ask RazorpayX what actually happened to every payout still recorded as
    in flight, and apply any terminal answer.

    Webhooks remain the primary path -- this only ever looks at actions a
    webhook has NOT already resolved. It exists because webhook delivery
    isn't guaranteed: a payout advanced while the tunnel was down would
    otherwise sit at 'processing' forever with nothing to revisit it."""
    if not (config.RAZORPAYX_KEY_ID and config.RAZORPAYX_KEY_SECRET):
        raise HTTPException(status_code=409,
                            detail="RazorpayX credentials aren't configured, so there is nothing to sync against")
    return sync_payout_statuses(conn, RazorpayXPayoutStatusFetcher())


@app.post("/pipeline/route", dependencies=[Depends(require_auth)])
def run_route_pipeline(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """Dispatches (at most) one action per still-open exception, bounded by
    retry/age limits. Safe to call repeatedly. Uses the live RazorpayX
    executor once configured (see GET /integration/status), the mock
    otherwise."""
    return route_open_exceptions(conn, executor=get_payout_executor())


@app.get("/pipeline/route/preview")
def preview_route_pipeline(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """What POST /pipeline/route would do, without doing it. Read-only: the
    real decision path runs inside a savepoint that is always rolled back, and
    no executor is ever reached, so this never moves money and never touches
    the network. Backs the confirmation step in the dashboard's Run menu --
    dispatching payouts should say how many first."""
    return preview_route(conn)


class ConfirmActionBody(BaseModel):
    outcome: str  # 'processed' | 'reversed'


@app.post("/actions/{action_id}/confirm", dependencies=[Depends(require_auth)])
def confirm_action_endpoint(action_id: int, body: ConfirmActionBody,
                             conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """Manual stand-in for the payout.processed / payout.reversed webhook --
    still useful for local testing even with the real webhook endpoint
    below wired up, since test-mode payouts don't auto-advance either way."""
    try:
        confirm_action(conn, action_id, body.outcome)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"action_id": action_id, "outcome": body.outcome}


class ResolveExceptionBody(BaseModel):
    note: str = ""


@app.post("/exceptions/{exception_key}/resolve", dependencies=[Depends(require_auth)])
def resolve_exception_endpoint(exception_key: str, body: ResolveExceptionBody,
                                conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """Human-in-the-loop close-out for exceptions the router itself never
    resolves (fee_mismatch/timing_lag/duplicate/unexplained) -- see
    router.resolve_exception's docstring for why those need this."""
    try:
        resolve_exception(conn, exception_key, body.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"exception_key": exception_key, "status": "resolved"}


@app.post("/exceptions/{exception_key}/recheck", dependencies=[Depends(require_auth)])
def recheck_exception_endpoint(exception_key: str, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """Closes out a 'pending' exception (currently timing_lag only) once
    its evidence is reaffirmed -- see router.recheck_exception."""
    try:
        recheck_exception(conn, exception_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"exception_key": exception_key, "status": "resolved"}


@app.post("/webhooks/razorpayx")
async def razorpayx_webhook(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """Real webhook receiver. Needs RAZORPAYX_WEBHOOK_SECRET configured and
    (for RazorpayX to reach it at all) a public URL -- this sandbox has
    neither; see the M6 notes for how to test this locally with a tunnel.
    """
    if not config.RAZORPAYX_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="RAZORPAYX_WEBHOOK_SECRET not configured")

    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    try:
        verify_signature(raw_body, signature, config.RAZORPAYX_WEBHOOK_SECRET)
    except WebhookVerificationError:
        raise HTTPException(status_code=400, detail="invalid webhook signature")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    try:
        return handle_webhook(conn, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- User-uploaded data ----------------------------------------------------
# The real-data counterpart to the fixture generator. scripts/load_demo_data.py
# deliberately keeps fixture-seeding off the HTTP surface ("seeding fake data is
# a dev-time concern") and that still holds -- these routes are not another way
# to load fixtures, they are the path for data a user actually has.


def _check_uploadable(source: str) -> None:
    """'gateway' is RazorpayX's own record, sourced from the live API or
    webhooks -- there is no file anyone holds for it, so it is not a 404
    (the route exists) but a 400 (that source can't work this way)."""
    if source not in UPLOAD_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"source must be one of {UPLOAD_SOURCES}, got {source!r}",
        )


def _validation_response(errors: list) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": f"{len(errors)} problem(s) in the uploaded file",
                 "errors": errors},
    )


@app.get("/data/templates/{source}")
def get_upload_template(source: str) -> Response:
    """The exact columns an upload of this source expects, so nobody has to
    guess them -- generated from the same schema table the parser reads."""
    _check_uploadable(source)
    return Response(
        content=build_template(source),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{source}_template.csv"'},
    )


@app.post("/demo/reset", dependencies=[Depends(require_auth)])
def reset_demo(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """Clear everything and reload the deterministic demo dataset, then
    reconcile both actual sources.

    Destructive by definition -- it deletes whatever was uploaded. The
    dashboard confirms before calling this; see app/demo.py for why this is
    an endpoint at all when scripts/load_demo_data.py deliberately is not."""
    return reset_demo_data(conn)


@app.get("/data/summary")
def get_data_summary(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return summarize(conn)


@app.post("/data/upload/{source}", dependencies=[Depends(require_auth)])
async def upload_data(source: str, dry_run: bool = False,
                      file: UploadFile = File(...),
                      conn: sqlite3.Connection = Depends(get_db)):
    """Load a user's own ledger / bank statement CSV, replacing whatever
    that source currently holds.

    dry_run=true answers "what would this destroy" without destroying it,
    and dry_run=false re-validates from scratch rather than trusting that
    an earlier dry run described this same file -- nothing about the
    preview is cached or carried over, because the only thing tying the
    two calls together is the user's word that it's the same file.
    """
    _check_uploadable(source)

    raw = await file.read()
    if len(raw) > config.UPLOAD_MAX_BYTES:
        # Read-then-measure rather than a streaming cap: at a 2MB ceiling
        # the body is already in memory by the time any handler runs, so a
        # streaming check would buy nothing an ASGI-level body limit
        # wouldn't do better.
        raise HTTPException(
            status_code=413,
            detail=f"file is larger than {config.UPLOAD_MAX_BYTES} bytes",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # A CSV saved as latin-1 out of an older accounting package is the
        # user's problem to fix, and saying so beats a 500 to interpret.
        return _validation_response(
            [{"row_number": 0, "column": "", "message": "file must be UTF-8 encoded text"}])

    try:
        rows = parse_csv(source, text, max_rows=config.UPLOAD_MAX_ROWS)
    except IngestValidationError as e:
        return _validation_response(e.errors)

    if dry_run:
        return preview_replace(conn, source, rows)
    return replace_source(conn, source, rows)


class AssistantChatBody(BaseModel):
    message: str
    history: list = []  # [{"role": "user"|"assistant", "content": str}], newest last


@app.post("/assistant/chat", dependencies=[Depends(require_auth)])
def assistant_chat(body: AssistantChatBody, request: Request,
                   conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """Server-side proxy to Groq for the dashboard's help assistant.

    This endpoint exists so the API key stays on the server. Calling Groq from
    the page would publish the key to anyone who opens devtools, and this app
    sets no CSP to constrain what a third-party script could then do with it.

    It is also an unauthenticated proxy to a metered API, which is why the
    checks below run before anything is spent, cheapest first.
    """
    if not config.GROQ_API_KEY:
        raise HTTPException(status_code=409,
                            detail="The assistant isn't configured -- set GROQ_API_KEY to enable it")

    limited = check_rate_limit(request.client.host if request.client else "unknown")
    if limited:
        raise HTTPException(status_code=429, detail=limited)

    message = (body.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Ask a question first")
    if len(message) > config.ASSISTANT_MAX_INPUT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"That question is too long -- keep it under {config.ASSISTANT_MAX_INPUT_CHARS} characters",
        )

    # Classifier screen. Costs a request but zero tokens, so it runs before the
    # expensive call rather than after. Defence in depth only -- the system
    # prompt's own constraints are the primary control, and injection_score()
    # fails open by design.
    if injection_score(message) >= config.ASSISTANT_INJECTION_CUTOFF:
        raise HTTPException(
            status_code=400,
            detail="That looks like an attempt to change how the assistant works, so I didn't send it. "
                   "Ask about the reconciliation data instead.",
        )

    try:
        reply = chat(build_messages(conn, message, body.history))
    except GroqError as e:
        # First place in this app that translates an upstream client error.
        # Anything else would surface as a 500 and read like our bug.
        if e.status_code == 429:
            raise HTTPException(status_code=502,
                                detail="The assistant's upstream quota is exhausted. Try again in a minute.")
        logging.warning("groq call failed: %s", e.status_code)
        raise HTTPException(status_code=502, detail="The assistant is unavailable right now.")
    return {"reply": reply}


# Dashboard UI. Mounted last, after every explicit API route above, so those
# routes always match first -- this mount is purely a catch-all serving the
# built dashboard at "/" and its hashed assets underneath.
#
# check_dir=False because this directory is a BUILD OUTPUT (frontend/,
# `npm run build`) and is gitignored: on a fresh clone it does not exist yet.
# StaticFiles checks for it at import time by default, so leaving that on made
# `from app.main import app` raise -- taking the API and the entire test suite
# down with it over a missing frontend build. Now an unbuilt checkout serves
# 404 at "/" and a fully working API everywhere else.
app.mount(
    "/",
    StaticFiles(directory="app/static/dist", html=True, check_dir=False),
    name="dashboard",
)
