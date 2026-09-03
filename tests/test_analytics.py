import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.analytics import compute_exception_intelligence
from app.config import MAX_EXCEPTION_AGE_DAYS

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def _fresh_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text())
    return conn


def _txn(conn, *, ref, source="ledger", days_ago=0.0, counterparty="Acme Traders",
          amount=100_000):
    conn.execute(
        """INSERT INTO transactions (source, external_ref, reference_id, amount_paise,
                                      currency, counterparty, occurred_at)
           VALUES (?, ?, ?, ?, 'INR', ?, ?)""",
        (source, ref, ref, amount, counterparty,
         (NOW - timedelta(days=days_ago)).isoformat()),
    )
    conn.commit()


def _exc(conn, *, key, cause="failed_payment", ledger_ref=None, gateway_ref=None,
          amount=100_000, status="open"):
    conn.execute(
        """INSERT INTO exceptions (exception_key, cause, ledger_ref, gateway_ref,
                                    amount_paise, status)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (key, cause, ledger_ref, gateway_ref, amount, status),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Invariants -- the same habit app/funnel.py has: the parts must sum to the
# whole, or the breakdown is quietly lying about how much money is at risk.
# ---------------------------------------------------------------------------

def _assert_invariants(result):
    assert sum(c["amount_paise"] for c in result["by_cause"]) == result["value_at_risk_paise"]
    assert sum(c["count"] for c in result["by_cause"]) == result["exception_count"]
    assert (sum(b["amount_paise"] for b in result["by_age"]) + result["undated_paise"]
            == result["value_at_risk_paise"])
    assert (sum(b["count"] for b in result["by_age"]) + result["undated_count"]
            == result["exception_count"])


def test_breakdowns_sum_to_the_total():
    conn = _fresh_db()
    _txn(conn, ref="LED-1", days_ago=0.5)
    _txn(conn, ref="LED-2", days_ago=4)
    _txn(conn, ref="LED-3", days_ago=30)
    _exc(conn, key="a", ledger_ref="LED-1", amount=10_000)
    _exc(conn, key="b", ledger_ref="LED-2", amount=20_000, cause="duplicate")
    _exc(conn, key="c", ledger_ref="LED-3", amount=30_000, cause="timing_lag", status="pending")

    result = compute_exception_intelligence(conn, now=NOW)
    assert result["exception_count"] == 3
    assert result["value_at_risk_paise"] == 60_000
    _assert_invariants(result)


def test_terminal_exceptions_are_excluded_from_the_backlog():
    conn = _fresh_db()
    _txn(conn, ref="LED-1", days_ago=2)
    _txn(conn, ref="LED-2", days_ago=2)
    _txn(conn, ref="LED-3", days_ago=2)
    _exc(conn, key="open", ledger_ref="LED-1", amount=10_000, status="open")
    _exc(conn, key="done", ledger_ref="LED-2", amount=99_000, status="resolved")
    _exc(conn, key="gone", ledger_ref="LED-3", amount=99_000, status="abandoned")

    result = compute_exception_intelligence(conn, now=NOW)
    assert result["exception_count"] == 1
    assert result["value_at_risk_paise"] == 10_000


# ---------------------------------------------------------------------------
# The two join hazards. A plain `JOIN transactions ON ledger_ref` drops both
# of these on the floor and understates value at risk.
# ---------------------------------------------------------------------------

def test_gateway_only_orphans_are_counted_via_gateway_ref():
    # 'unexplained' is a transaction the ledger never expected -- it has no
    # ledger_ref at all, and is pure money-at-risk if the join loses it.
    conn = _fresh_db()
    _txn(conn, ref="pout_orphan", source="gateway", days_ago=2, counterparty="Ghost Vendor")
    _exc(conn, key="unexplained:-:pout_orphan", cause="unexplained",
         ledger_ref=None, gateway_ref="pout_orphan", amount=91_325)

    result = compute_exception_intelligence(conn, now=NOW)
    assert result["exception_count"] == 1
    assert result["value_at_risk_paise"] == 91_325
    assert result["undated_count"] == 0, "the orphan must still get an age"
    assert result["top_counterparties"][0]["counterparty"] == "Ghost Vendor"
    _assert_invariants(result)


def test_batch_exceptions_anchor_to_their_first_ledger_ref():
    # A batch-kind ledger_ref is a comma-joined list and matches no single
    # external_ref -- see app/classify.py.
    conn = _fresh_db()
    _txn(conn, ref="LED-1", days_ago=5, counterparty="Batch Vendor")
    _exc(conn, key="partial_payment:batch", cause="partial_payment",
         ledger_ref="LED-1,LED-2,LED-3", amount=50_000, status="pending")

    result = compute_exception_intelligence(conn, now=NOW)
    assert result["undated_count"] == 0
    assert result["top_counterparties"][0]["counterparty"] == "Batch Vendor"
    assert result["by_age"][2]["count"] == 1  # the 3-7d bucket
    _assert_invariants(result)


def test_an_exception_with_no_matching_transaction_is_undated_not_invented():
    # Rather than folding it into the oldest bucket and inventing an age.
    conn = _fresh_db()
    _exc(conn, key="dangling", ledger_ref="LED-NOPE", amount=7_000)

    result = compute_exception_intelligence(conn, now=NOW)
    assert result["undated_count"] == 1
    assert result["undated_paise"] == 7_000
    assert sum(b["count"] for b in result["by_age"]) == 0
    _assert_invariants(result)


# ---------------------------------------------------------------------------
# Age comes from when the money moved, not when the pipeline ran.
# ---------------------------------------------------------------------------

def test_age_buckets_use_transaction_time_not_exception_created_at():
    # Every exception row here is created in the same instant (as happens on
    # any fresh load), but the transactions behind them span weeks. Bucketing
    # on created_at would put all four in 'under 1d' and say nothing.
    conn = _fresh_db()
    for i, days in enumerate([0.2, 2.0, 5.0, 30.0]):
        _txn(conn, ref=f"LED-{i}", days_ago=days)
        _exc(conn, key=f"e{i}", ledger_ref=f"LED-{i}", amount=1_000)

    result = compute_exception_intelligence(conn, now=NOW)
    assert [b["count"] for b in result["by_age"]] == [1, 1, 1, 1]
    assert [b["bucket"] for b in result["by_age"]] == [
        "under 1d", "1-3d", f"3-{MAX_EXCEPTION_AGE_DAYS}d", f"over {MAX_EXCEPTION_AGE_DAYS}d"
    ]


def test_only_the_oldest_bucket_is_flagged_past_the_routers_bound():
    conn = _fresh_db()
    _txn(conn, ref="LED-1", days_ago=MAX_EXCEPTION_AGE_DAYS + 1)
    _exc(conn, key="old", ledger_ref="LED-1", amount=1_000)

    result = compute_exception_intelligence(conn, now=NOW)
    flagged = [b for b in result["by_age"] if b["past_bound"]]
    assert len(flagged) == 1
    assert flagged[0]["count"] == 1
    # the bound the UI shows must be the one router.py actually abandons on
    assert result["max_exception_age_days"] == MAX_EXCEPTION_AGE_DAYS


def test_a_transaction_exactly_on_a_boundary_falls_in_the_older_bucket():
    conn = _fresh_db()
    _txn(conn, ref="LED-1", days_ago=1.0)  # exactly 1 day
    _exc(conn, key="edge", ledger_ref="LED-1", amount=1_000)

    result = compute_exception_intelligence(conn, now=NOW)
    assert result["by_age"][0]["count"] == 0    # not 'under 1d'
    assert result["by_age"][1]["count"] == 1    # '1-3d'


# ---------------------------------------------------------------------------
# Ordering, shares, and the empty case
# ---------------------------------------------------------------------------

def test_causes_and_counterparties_are_ranked_by_value_not_count():
    # Six small nuisances matter less than one large one -- ranking by count
    # would bury the money.
    conn = _fresh_db()
    _txn(conn, ref="BIG", days_ago=2, counterparty="Whale Ltd")
    _exc(conn, key="big", cause="failed_payment", ledger_ref="BIG", amount=500_000)
    for i in range(6):
        _txn(conn, ref=f"SMALL-{i}", days_ago=2, counterparty="Minnow Ltd")
        _exc(conn, key=f"small{i}", cause="duplicate", ledger_ref=f"SMALL-{i}", amount=1_000)

    result = compute_exception_intelligence(conn, now=NOW)
    assert result["by_cause"][0]["cause"] == "failed_payment"
    assert result["by_cause"][0]["count"] == 1
    assert result["by_cause"][1]["count"] == 6
    assert result["top_counterparties"][0]["counterparty"] == "Whale Ltd"
    assert round(sum(c["share"] for c in result["by_cause"]), 2) == 1.0


def test_empty_backlog_does_not_divide_by_zero():
    result = compute_exception_intelligence(_fresh_db(), now=NOW)
    assert result["exception_count"] == 0
    assert result["value_at_risk_paise"] == 0
    assert all(b["share"] == 0.0 for b in result["by_age"])
    assert result["by_cause"] == []
    assert result["top_counterparties"] == []
    _assert_invariants(result)


def test_top_counterparties_is_capped():
    from app.analytics import TOP_COUNTERPARTY_LIMIT

    conn = _fresh_db()
    for i in range(TOP_COUNTERPARTY_LIMIT + 5):
        _txn(conn, ref=f"LED-{i}", days_ago=2, counterparty=f"Vendor {i:02d}")
        _exc(conn, key=f"e{i}", ledger_ref=f"LED-{i}", amount=1_000 * (i + 1))

    result = compute_exception_intelligence(conn, now=NOW)
    assert len(result["top_counterparties"]) == TOP_COUNTERPARTY_LIMIT
    # still the largest ones, not the first ones found
    assert result["top_counterparties"][0]["amount_paise"] == 1_000 * (TOP_COUNTERPARTY_LIMIT + 5)


# ---------------------------------------------------------------------------
# Daily reconciliation -- the timeline behind the Insights chart.
# ---------------------------------------------------------------------------

def test_daily_split_always_sums_to_the_days_total():
    from app.analytics import compute_daily_reconciliation

    conn = _fresh_db()
    _txn(conn, ref="LED-1", days_ago=1, amount=10_000)
    _txn(conn, ref="LED-2", days_ago=1, amount=20_000)
    _txn(conn, ref="LED-3", days_ago=2, amount=30_000)
    _exc(conn, key="a", ledger_ref="LED-1", amount=10_000)

    days = compute_daily_reconciliation(conn)
    assert len(days) == 2
    for day in days:
        assert day["reconciled_paise"] + day["outstanding_paise"] == day["total_paise"]

    recent = days[-1]
    assert recent["total_paise"] == 30_000
    assert recent["outstanding_paise"] == 10_000
    assert recent["reconciled_paise"] == 20_000


def test_days_are_returned_in_chronological_order():
    from app.analytics import compute_daily_reconciliation

    conn = _fresh_db()
    for days_ago in (1, 9, 5):
        _txn(conn, ref=f"LED-{days_ago}", days_ago=days_ago, amount=1_000)

    days = compute_daily_reconciliation(conn)
    assert [d["day"] for d in days] == sorted(d["day"] for d in days)


def test_every_row_of_a_batch_exception_counts_as_outstanding():
    # _first_ref is the wrong tool here: a batch holds up ALL its ledger
    # rows, not just the one the exception key happens to lead with.
    from app.analytics import compute_daily_reconciliation

    conn = _fresh_db()
    _txn(conn, ref="LED-1", days_ago=1, amount=10_000)
    _txn(conn, ref="LED-2", days_ago=1, amount=10_000)
    _txn(conn, ref="LED-3", days_ago=1, amount=10_000)
    _exc(conn, key="batch", cause="partial_payment", ledger_ref="LED-1,LED-2,LED-3",
         amount=30_000, status="pending")

    day = compute_daily_reconciliation(conn)[0]
    assert day["outstanding_paise"] == 30_000
    assert day["reconciled_paise"] == 0


def test_resolved_exceptions_stop_counting_against_their_day():
    from app.analytics import compute_daily_reconciliation

    conn = _fresh_db()
    _txn(conn, ref="LED-1", days_ago=1, amount=10_000)
    _exc(conn, key="done", ledger_ref="LED-1", amount=10_000, status="resolved")

    day = compute_daily_reconciliation(conn)[0]
    assert day["outstanding_paise"] == 0
    assert day["reconciled_paise"] == 10_000


def test_gateway_rows_never_appear_as_business_days():
    # Only the ledger has business days; the gateway side is an observation
    # of the same money and would double-count it.
    from app.analytics import compute_daily_reconciliation

    conn = _fresh_db()
    _txn(conn, ref="LED-1", days_ago=1, amount=10_000)
    _txn(conn, ref="pout_x", source="gateway", days_ago=1, amount=10_000)

    days = compute_daily_reconciliation(conn)
    assert len(days) == 1
    assert days[0]["total_paise"] == 10_000
    assert days[0]["count"] == 1


def test_no_transactions_yields_no_days():
    from app.analytics import compute_daily_reconciliation
    assert compute_daily_reconciliation(_fresh_db()) == []


def test_a_gateway_side_duplicate_does_not_hold_its_ledger_row_outstanding():
    """The hero and the funnel must not disagree about the same row.

    funnel.py splits causes deliberately: a 'duplicate' is gateway-side, and
    its ledger row "matched fine" (see funnel.py's docstring) -- so the funnel
    counts such a row as recovered once its own ledger-side exception is
    resolved. compute_daily_reconciliation swept up *any* non-terminal
    exception regardless of cause, so a still-open duplicate pinned the row as
    outstanding and the Overview hero held money the funnel directly below it
    had already released.

    Observed live: LED-0043's failed_payment was confirmed processed by a real
    RazorpayX webhook, its exception resolved, and Rs 5,439 stayed in the
    outstanding total because a gateway-side duplicate was still open.
    """
    from app.analytics import compute_daily_reconciliation

    conn = _fresh_db()
    _txn(conn, ref="LED-1", amount=5_439)
    # The ledger-side problem, recovered -- a payout was dispatched and confirmed.
    _exc(conn, key="fp", cause="failed_payment", ledger_ref="LED-1",
         amount=5_439, status="resolved")
    # A surplus gateway row. Nothing to do with whether LED-1 itself settled.
    _exc(conn, key="dup", cause="duplicate", ledger_ref="LED-1",
         gateway_ref="PAY-9", amount=5_439, status="open")

    day = compute_daily_reconciliation(conn)[0]
    assert day["outstanding_paise"] == 0
    assert day["reconciled_paise"] == 5_439


def test_an_open_ledger_side_cause_still_holds_the_row_outstanding():
    """The other half of the rule -- this is the case that must NOT change.

    A timing_lag is ledger-side: the row genuinely has not settled, so
    resolving some other exception on it does not release the money.
    """
    from app.analytics import compute_daily_reconciliation

    conn = _fresh_db()
    _txn(conn, ref="LED-2", amount=13_151)
    _exc(conn, key="fp2", cause="failed_payment", ledger_ref="LED-2",
         amount=13_151, status="resolved")
    _exc(conn, key="lag", cause="timing_lag", ledger_ref="LED-2",
         amount=13_151, status="pending")

    day = compute_daily_reconciliation(conn)[0]
    assert day["outstanding_paise"] == 13_151
    assert day["reconciled_paise"] == 0
