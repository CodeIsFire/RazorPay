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
  refund_unmatched an unmatched gateway row typed 'refund' (transactions
                   .transaction_type) with no corresponding ledger entry
                   yet -- called out on its own rather than folded into
                   unexplained, since a refund against a known original
                   transaction is a recognized event, not a mystery
  chargeback       an unmatched gateway row typed 'chargeback' -- same
                   reasoning as refund_unmatched, but routed to a human
                   dispute-note action instead of a review flag, since a
                   chargeback typically has a bank-side response deadline

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

`classify()` takes a `source` name (see reconcile.py's `ACTUAL_SOURCES`,
default 'gateway') so the same rules apply whether the non-ledger side
being reconciled is RazorpayX or a real bank statement -- every Exception
records which one produced it in `matched_source`. The one place `source`
also has to appear inside `exception_key` itself is `failed_payment`: it's
the only cause with no real non-ledger ref to make the key unique, so
reconciling the same unmatched ledger row against two different sources
must not collide into a single row.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import sqlite3

from app.config import (
    FUZZY_AMOUNT_TOLERANCE_PAISE,
    FUZZY_TIME_WINDOW_HOURS,
    KNOWN_FEE_TOLERANCE_PAISE,
)
from app.db import log_audit
from app.reconcile import MatchResult, reconcile_from_db

CAUSES = ("failed_payment", "fee_mismatch", "duplicate", "timing_lag", "unexplained",
          "refund_unmatched", "chargeback", "partial_payment")


@dataclass
class Exception_:
    exception_key: str
    cause: str
    ledger_ref: str | None
    gateway_ref: str | None
    amount_paise: int
    detail: str
    # Which non-ledger source (reconcile.py's ACTUAL_SOURCES) this exception
    # was found reconciling against -- 'gateway' by default, so existing
    # callers that never pass classify()'s source param behave exactly as
    # before.
    matched_source: str = "gateway"
    # Set when classify() already has enough evidence to close this out
    # without a human or a router action -- e.g. a fee_mismatch delta that
    # matches RazorpayX's known fee range. persist_exceptions() resolves
    # these immediately after insert, audited under actor="classifier"
    # rather than "human" or "router" since no one dispatched anything.
    auto_resolve: bool = False


def _parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s)


def classify(result: MatchResult, source: str = "gateway") -> list[Exception_]:
    exceptions: list[Exception_] = []
    source_label = source.replace("_", " ")

    matched_ledger_refs = {led["external_ref"] for led, _gw, _tier in result.matches}

    unmatched_gw_by_ref: dict[str | None, list[dict]] = {}
    for gw in result.unmatched_gateway:
        unmatched_gw_by_ref.setdefault(gw["reference_id"], []).append(gw)

    paired_gateway_refs: set[str] = set()

    for led in result.unmatched_ledger:
        candidates = unmatched_gw_by_ref.get(led["reference_id"], [])
        if not candidates:
            exceptions.append(Exception_(
                # source-qualified: unlike every other cause below, this key
                # has no real non-ledger ref to make it unique on its own --
                # without `source` here, the same unmatched ledger row would
                # collide into one row across two separate reconcile passes
                # (e.g. gateway then bank_statement) instead of surfacing both.
                exception_key=f"failed_payment:{source}:{led['external_ref']}:-",
                cause="failed_payment",
                ledger_ref=led["external_ref"], gateway_ref=None,
                amount_paise=led["amount_paise"],
                detail=f"No {source_label} transaction found for this reference_id.",
                matched_source=source,
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

        auto_resolve = False
        if amount_diff > FUZZY_AMOUNT_TOLERANCE_PAISE:
            cause = "fee_mismatch"
            # KNOWN_FEE_TOLERANCE_PAISE encodes RazorpayX's own fee
            # schedule specifically -- it has no meaning against a bank
            # statement, where a small delta is just as likely a real
            # error as an explained fee. Auto-resolution only ever applies
            # to the 'gateway' source; every other source's fee_mismatch
            # always needs a human, however small the delta.
            if source == "gateway" and amount_diff <= KNOWN_FEE_TOLERANCE_PAISE:
                # Small enough to plausibly be RazorpayX's own processing
                # fee rather than a real discrepancy -- explained, not
                # unexplained. No dispute note, no human, no API call:
                # this closes itself out right here.
                auto_resolve = True
                detail = (f"{source_label.capitalize()} amount differs by {amount_diff} paise "
                           f"(ledger={led['amount_paise']}, {source_label}={gw['amount_paise']}) -- "
                           f"within the known RazorpayX fee range (<= {KNOWN_FEE_TOLERANCE_PAISE} "
                           f"paise), auto-resolved as an explained processing fee.")
            else:
                detail = (f"{source_label.capitalize()} amount differs by {amount_diff} paise "
                           f"(ledger={led['amount_paise']}, {source_label}={gw['amount_paise']}) -- "
                           f"exceeds the known fee range, needs review.")
        elif time_diff_hours > FUZZY_TIME_WINDOW_HOURS:
            cause = "timing_lag"
            detail = (f"{source_label.capitalize()} transaction landed {time_diff_hours:.1f}h after "
                       f"expected, outside the {FUZZY_TIME_WINDOW_HOURS}h window.")
        else:
            # Within tolerance on both axes -- the matcher should have
            # matched this. Flag it loudly rather than misfile it under a
            # cause that doesn't actually explain what happened.
            cause = "unexplained"
            detail = "Within tolerance on both amount and timing, but the matcher left it unmatched."

        exceptions.append(Exception_(
            exception_key=f"{cause}:{led['external_ref']}:{gw['external_ref']}",
            cause=cause, ledger_ref=led["external_ref"], gateway_ref=gw["external_ref"],
            amount_paise=led["amount_paise"], detail=detail, auto_resolve=auto_resolve,
            matched_source=source,
        ))

    # Remaining unmatched gateway rows: either surplus against an
    # already-matched ledger row (duplicate) or genuinely unexplained.
    for gw in result.unmatched_gateway:
        if gw["external_ref"] in paired_gateway_refs:
            continue  # already accounted for above as the fee/timing partner

        gw_type = gw.get("transaction_type", "payment")
        if gw_type in ("refund", "chargeback"):
            # Called out from 'unexplained' on purpose -- a refund/chargeback
            # is a recognized event with a known origin (original_ref), not
            # an unexplained transaction, even before that origin is
            # confirmed matched. See classify.py's module docstring.
            original = gw.get("original_ref")
            exceptions.append(Exception_(
                exception_key=f"{gw_type}:{original or '-'}:{gw['external_ref']}",
                cause="chargeback" if gw_type == "chargeback" else "refund_unmatched",
                ledger_ref=original, gateway_ref=gw["external_ref"],
                amount_paise=gw["amount_paise"],
                detail=(f"{source_label.capitalize()} {gw_type} against "
                        f"{original or 'an unrecorded original transaction'} "
                        f"has no matching ledger entry yet."),
                matched_source=source,
            ))
            continue

        if gw["reference_id"] in matched_ledger_refs:
            exceptions.append(Exception_(
                exception_key=f"duplicate:{gw['reference_id']}:{gw['external_ref']}",
                cause="duplicate",
                ledger_ref=gw["reference_id"], gateway_ref=gw["external_ref"],
                amount_paise=gw["amount_paise"],
                detail=f"Extra {source_label} transaction beyond the one already matched to {gw['reference_id']}.",
                matched_source=source,
            ))
        else:
            exceptions.append(Exception_(
                exception_key=f"unexplained:-:{gw['external_ref']}",
                cause="unexplained",
                ledger_ref=None, gateway_ref=gw["external_ref"],
                amount_paise=gw["amount_paise"],
                detail=f"{source_label.capitalize()} transaction has no correlated ledger entry, matched or unmatched.",
                matched_source=source,
            ))

    # split_matches/batch_matches are full successes -- nothing to classify.
    # partial_groups are the ones still short (or overshooting): one
    # 'partial_payment' exception per group, keyed on a stable anchor (the
    # ledger ref for a split, the settlement_batch_id for a batch) rather
    # than the variable set of pieces seen so far -- see reconcile.py and
    # this module's docstring for why that anchor has to stay fixed while
    # the group's membership grows across reconcile passes.
    for group in result.partial_groups:
        ledger_refs = sorted(l["external_ref"] for l in group["ledgers"])
        gateway_refs = sorted(g["external_ref"] for g in group["gateways"])
        expected_total = sum(l["amount_paise"] for l in group["ledgers"])
        actual_total = sum(g["amount_paise"] for g in group["gateways"])
        delta = expected_total - actual_total

        if group["kind"] == "split":
            anchor = group["ledgers"][0]["external_ref"]
        else:
            anchor = group["gateways"][0]["reference_id"]

        short_or_over = "short of" if delta > 0 else "over"
        exceptions.append(Exception_(
            exception_key=f"partial_payment:{group['kind']}:{anchor}:-",
            cause="partial_payment",
            ledger_ref=",".join(ledger_refs), gateway_ref=",".join(gateway_refs),
            amount_paise=expected_total,
            detail=(f"{len(gateway_refs)} {source_label} transaction(s) found "
                    f"({','.join(gateway_refs)}) summing to {actual_total} paise -- "
                    f"{short_or_over} the expected {expected_total} paise by {abs(delta)} paise."),
            matched_source=source,
        ))

    return exceptions


def persist_exceptions(conn: sqlite3.Connection, exceptions: list[Exception_]) -> list[str]:
    """Idempotent insert keyed on exception_key. Returns the keys that were
    actually new this call (empty list on a pure re-run) and audit-logs
    only those -- so re-running classification doesn't spam the trail.

    'partial_payment' is the one deliberate exception to "insert once, never
    touch again": its group's membership grows across reconcile passes (1
    of 3 pieces today, 3rd next week), so an already-open row's detail/
    amount must refresh under the same stable key rather than freezing at
    first detection. Only while still open/pending -- a resolved or
    abandoned row (a human's decision, or this same cause's own
    auto-resolution once its group completes -- see
    classify_and_persist_from_db) is never silently reopened or overwritten."""
    new_keys = []
    for exc in exceptions:
        if exc.cause == "partial_payment":
            cur = conn.execute(
                """UPDATE exceptions SET gateway_ref=?, ledger_ref=?, amount_paise=?, detail=?,
                       updated_at=datetime('now')
                   WHERE exception_key=? AND status NOT IN ('resolved', 'abandoned')""",
                (exc.gateway_ref, exc.ledger_ref, exc.amount_paise, exc.detail, exc.exception_key),
            )
            if cur.rowcount:
                log_audit(
                    conn, actor="classifier", subject_type="exception",
                    subject_id=exc.exception_key, event="partial_payment_updated",
                    detail=exc.detail,
                )
                continue  # updated an existing row -- not "new"
            # else: either brand new (falls through to the INSERT below) or
            # resolved/abandoned (falls through to ON CONFLICT DO NOTHING,
            # correctly leaving it untouched).

        cur = conn.execute(
            """
            INSERT INTO exceptions
                (exception_key, cause, ledger_ref, gateway_ref, matched_source, amount_paise, detail)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(exception_key) DO NOTHING
            """,
            (exc.exception_key, exc.cause, exc.ledger_ref, exc.gateway_ref,
             exc.matched_source, exc.amount_paise, exc.detail),
        )
        if cur.rowcount:
            new_keys.append(exc.exception_key)
            log_audit(
                conn, actor="classifier", subject_type="exception",
                subject_id=exc.exception_key, event="exception_created",
                detail=f"cause={exc.cause} {exc.detail}",
            )
            if exc.auto_resolve:
                conn.execute(
                    "UPDATE exceptions SET status='resolved', updated_at=datetime('now') "
                    "WHERE exception_key=?",
                    (exc.exception_key,),
                )
                log_audit(
                    conn, actor="classifier", subject_type="exception",
                    subject_id=exc.exception_key, event="exception_auto_resolved",
                    detail=exc.detail,
                )
    return new_keys


def _resolve_completed_partial_payments(conn: sqlite3.Connection, exceptions: list[Exception_],
                                          actual_source: str) -> None:
    """A partial_payment exception has no ordinary path to 'resolved' --
    unlike fee_mismatch/timing_lag, nothing ever short-circuits it at
    creation time or through a human recheck. Its group either stays short
    (persist_exceptions refreshes it in place, see above) or, this pass,
    stops appearing in classify()'s output entirely because reconcile.py's
    split/batch pass now sums it into a full match. That's graduation:
    still-open partial_payment rows not present in this pass's output are
    auto-resolved here, the same "classifier decided, not a human" pattern
    as fee_mismatch's auto_resolve, just applied after the fact instead of
    at creation.

    Scoped to `actual_source`, and that scoping is load-bearing. Graduation is
    inferred from ABSENCE -- a row missing from this pass's output is taken to
    have completed -- so it may only be applied to rows this pass could have
    produced. Split/batch matching is a gateway-side concept, so a
    'bank_statement' pass emits no partial_payment rows at all; without the
    filter, every open gateway-side partial payment looks absent and gets
    graduated. Reconciling a bank statement would then silently mark money as
    reconciled that is still demonstrably short -- the exception's own detail
    still reading "short of the expected" while its status says resolved.
    """
    still_partial_keys = {e.exception_key for e in exceptions if e.cause == "partial_payment"}
    open_rows = conn.execute(
        "SELECT exception_key FROM exceptions "
        "WHERE cause='partial_payment' AND matched_source=? "
        "  AND status NOT IN ('resolved', 'abandoned')",
        (actual_source,),
    ).fetchall()
    for row in open_rows:
        key = row["exception_key"]
        if key in still_partial_keys:
            continue
        conn.execute(
            "UPDATE exceptions SET status='resolved', updated_at=datetime('now') WHERE exception_key=?",
            (key,),
        )
        log_audit(
            conn, actor="classifier", subject_type="exception", subject_id=key,
            event="partial_payment_completed",
            detail="All expected pieces reconciled -- group graduated to a full split/batch match.",
        )


def classify_and_persist_from_db(conn: sqlite3.Connection, actual_source: str = "gateway") -> list[str]:
    result = reconcile_from_db(conn, actual_source)
    exceptions = classify(result, source=actual_source)
    new_keys = persist_exceptions(conn, exceptions)
    _resolve_completed_partial_payments(conn, exceptions, actual_source)
    conn.commit()
    return new_keys
