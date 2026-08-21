import json
import sqlite3
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from app import config
from app.classify import classify_and_persist_from_db
from app.db import fetch_audit_log, get_connection, init_db
from app.funnel import compute_funnel
from app.live_executor import RazorpayXPayoutExecutor, load_fund_account_map
from app.router import MockPayoutExecutor, confirm_action, route_open_exceptions
from app.webhooks import WebhookVerificationError, handle_webhook, verify_signature


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Reconcile -> Recover", lifespan=lifespan)


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
    if config.RAZORPAYX_KEY_ID and config.RAZORPAYX_KEY_SECRET and config.RAZORPAYX_ACCOUNT_NUMBER:
        return RazorpayXPayoutExecutor()
    return MockPayoutExecutor()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/integration/status")
def integration_status() -> dict:
    """What's actually configured right now, without leaking secrets --
    useful for confirming which executor /pipeline/route will use before
    it does something with real (test-mode) money."""
    live_ready = bool(
        config.RAZORPAYX_KEY_ID and config.RAZORPAYX_KEY_SECRET and config.RAZORPAYX_ACCOUNT_NUMBER
    )
    return {
        "executor": "live" if live_ready else "mock",
        "key_configured": bool(config.RAZORPAYX_KEY_ID),
        "account_number_configured": bool(config.RAZORPAYX_ACCOUNT_NUMBER),
        "webhook_secret_configured": bool(config.RAZORPAYX_WEBHOOK_SECRET),
        "fund_accounts_mapped": len(load_fund_account_map()),
    }


@app.post("/pipeline/reconcile")
def run_reconcile_pipeline(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """Runs match -> classify -> persist against whatever is currently in
    the transactions table. Idempotent: re-POSTing after nothing new has
    been ingested creates no new exceptions (see app/classify.py)."""
    new_exception_keys = classify_and_persist_from_db(conn)
    return {"new_exceptions": new_exception_keys, "count": len(new_exception_keys)}


@app.get("/funnel")
def get_funnel(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return compute_funnel(conn)


@app.get("/audit")
def get_audit(limit: int = 200, subject_type: Optional[str] = None,
              conn: sqlite3.Connection = Depends(get_db)) -> dict:
    entries = fetch_audit_log(conn, limit=limit, subject_type=subject_type)
    return {"count": len(entries), "entries": entries}


@app.post("/pipeline/route")
def run_route_pipeline(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    """Dispatches (at most) one action per still-open exception, bounded by
    retry/age limits. Safe to call repeatedly. Uses the live RazorpayX
    executor once configured (see GET /integration/status), the mock
    otherwise."""
    return route_open_exceptions(conn, executor=get_payout_executor())


class ConfirmActionBody(BaseModel):
    outcome: str  # 'processed' | 'reversed'


@app.post("/actions/{action_id}/confirm")
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
