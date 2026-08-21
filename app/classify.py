"""Rule-based cause classification for reconciliation exceptions.

Takes a reconcile.MatchResult and turns every unmatched row into an
Exception with one of five causes:

  failed_payment  ledger expected a transaction; gateway has nothing
                   correlated to it at all
  fee_mismatch     a correlated gateway row exists, amount is off by more
                   than rounding tolerance
  timing_lag       a correlated gateway row exists, amount matches, but it
                   landed outside the matcher's time window
  duplicate        an unmatched gateway row whose reference_id belongs to
                   a ledger row that's already matched to a different
                   gateway row
  unexplained      an unmatched gateway row that correlates to nothing at
                   all -- not one of the four documented causes, but
                   silently dropping it would hide a real discrepancy
                   rather than surface it

When a correlated pair violates both the amount tolerance and the time
window at once, amount wins the classification (a real money discrepancy
is more actionable than a timing artifact) -- noted in the code, not just
here, since it's a real judgment call.

Every Exception gets a deterministic `exception_key`
(f"{cause}:{ledger_ref}:{gateway_ref}") derived purely from the
transaction identifiers involved, not a DB autoincrement id. That is the
exception<->payout linking primitive the rest of the system builds on:
classification is idempotent (re-running it never creates a duplicate row
for the same underlying situation -- see tests/test_classify.py), and M5's
retry/idempotency-key convention for RazorpayX payouts derives from this
same key instead of inventing a second identifier scheme.
"""
from dataclasses import dataclass
from datetime import datetime
import sqlite3

from app.config import FUZZY_AMOUNT_TOLERANCE_PAISE, FUZZY_TIME_WINDOW_HOURS
from app.db import log_audit
from app.reconcile import MatchResult, reconcile_from_db

CAUSES = ("failed_payment", "fee_mismatch", "duplicate", "timing_lag", "unexplained")


@dataclass
class Exception_:
    exception_key: str
    cause: str
    ledger_ref: str | None
    gateway_ref: str | None
    amount_paise: int
    detail: str


def _parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s)


def classify(result: MatchResult) -> list[Exception_]:
    exceptions: list[Exception_] = []

    matched_ledger_refs = {led["external_ref"] for led, _gw, _tier in result.matches}

    unmatched_gw_by_ref: dict[str | None, list[dict]] = {}
    for gw in result.unmatched_gateway:
        unmatched_gw_by_ref.setdefault(gw["reference_id"], []).append(gw)

    paired_gateway_refs: set[str] = set()

    for led in result.unmatched_ledger:
        candidates = unmatched_gw_by_ref.get(led["reference_id"], [])
        if not candidates:
            exceptions.append(Exception_(
                exception_key=f"failed_payment:{led['external_ref']}:-",
                cause="failed_payment",
                ledger_ref=led["external_ref"], gateway_ref=None,
                amount_paise=led["amount_paise"],
                detail="No gateway transaction found for this reference_id.",
            ))
            continue

        # Deterministic pick when more than one candidate shares the
        # reference_id (rare in practice; not exercised by the M1 fixture).
        gw = min(candidates, key=lambda g: g["external_ref"])
        paired_gateway_refs.add(gw["external_ref"])

        amount_diff = abs(gw["amount_paise"] - led["amount_paise"])
        time_diff_hours = abs(
            (_parse_time(gw["occurred_at"]) - _parse_time(led["occurred_at"])).total_seconds()
        ) / 3600

        if amount_diff > FUZZY_AMOUNT_TOLERANCE_PAISE:
            cause = "fee_mismatch"
            detail = (f"Gateway amount differs by {amount_diff} paise "
                       f"(ledger={led['amount_paise']}, gateway={gw['amount_paise']}).")
        elif time_diff_hours > FUZZY_TIME_WINDOW_HOURS:
            cause = "timing_lag"
            detail = (f"Gateway transaction landed {time_diff_hours:.1f}h after expected, "
                       f"outside the {FUZZY_TIME_WINDOW_HOURS}h window.")
        else:
            # Within tolerance on both axes -- the matcher should have
            # matched this. Flag it loudly rather than misfile it under a
            # cause that doesn't actually explain what happened.
            cause = "unexplained"
            detail = "Within tolerance on both amount and timing, but the matcher left it unmatched."

        exceptions.append(Exception_(
            exception_key=f"{cause}:{led['external_ref']}:{gw['external_ref']}",
            cause=cause, ledger_ref=led["external_ref"], gateway_ref=gw["external_ref"],
            amount_paise=led["amount_paise"], detail=detail,
        ))

    # Remaining unmatched gateway rows: either surplus against an
    # already-matched ledger row (duplicate) or genuinely unexplained.
    for gw in result.unmatched_gateway:
        if gw["external_ref"] in paired_gateway_refs:
            continue  # already accounted for above as the fee/timing partner

        if gw["reference_id"] in matched_ledger_refs:
            exceptions.append(Exception_(
                exception_key=f"duplicate:{gw['reference_id']}:{gw['external_ref']}",
                cause="duplicate",
                ledger_ref=gw["reference_id"], gateway_ref=gw["external_ref"],
                amount_paise=gw["amount_paise"],
                detail=f"Extra gateway transaction beyond the one already matched to {gw['reference_id']}.",
            ))
        else:
            exceptions.append(Exception_(
                exception_key=f"unexplained:-:{gw['external_ref']}",
                cause="unexplained",
                ledger_ref=None, gateway_ref=gw["external_ref"],
                amount_paise=gw["amount_paise"],
                detail="Gateway transaction has no correlated ledger entry, matched or unmatched.",
            ))

    return exceptions


def persist_exceptions(conn: sqlite3.Connection, exceptions: list[Exception_]) -> list[str]:
    """Idempotent insert keyed on exception_key. Returns the keys that were
    actually new this call (empty list on a pure re-run) and audit-logs
    only those -- so re-running classification doesn't spam the trail."""
    new_keys = []
    for exc in exceptions:
        cur = conn.execute(
            """
            INSERT INTO exceptions
                (exception_key, cause, ledger_ref, gateway_ref, amount_paise, detail)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(exception_key) DO NOTHING
            """,
            (exc.exception_key, exc.cause, exc.ledger_ref, exc.gateway_ref,
             exc.amount_paise, exc.detail),
        )
        if cur.rowcount:
            new_keys.append(exc.exception_key)
            log_audit(
                conn, actor="classifier", subject_type="exception",
                subject_id=exc.exception_key, event="exception_created",
                detail=f"cause={exc.cause} {exc.detail}",
            )
    return new_keys


def classify_and_persist_from_db(conn: sqlite3.Connection) -> list[str]:
    result = reconcile_from_db(conn)
    exceptions = classify(result)
    new_keys = persist_exceptions(conn, exceptions)
    conn.commit()
    return new_keys
