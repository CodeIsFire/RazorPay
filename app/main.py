import sqlite3
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI

from app.classify import classify_and_persist_from_db
from app.db import fetch_audit_log, get_connection, init_db
from app.funnel import compute_funnel


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


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


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
