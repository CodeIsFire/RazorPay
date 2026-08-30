"""Central config. Everything reads from env vars with sane local defaults
so the app runs with zero setup until M6 needs real RazorpayX credentials."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = os.getenv("RR_DB_PATH", str(DATA_DIR / "recon_recover.db"))

# RazorpayX -- live executor only activates once KEY_ID/KEY_SECRET/
# ACCOUNT_NUMBER are all present (see app/main.py's executor factory).
# With only KEY_ID/KEY_SECRET set (as of M6), it stays on the mock.
RAZORPAYX_KEY_ID = os.getenv("RAZORPAYX_KEY_ID", "")
RAZORPAYX_KEY_SECRET = os.getenv("RAZORPAYX_KEY_SECRET", "")
RAZORPAYX_ACCOUNT_NUMBER = os.getenv("RAZORPAYX_ACCOUNT_NUMBER", "")
RAZORPAYX_WEBHOOK_SECRET = os.getenv("RAZORPAYX_WEBHOOK_SECRET", "")
RAZORPAYX_PAYOUT_MODE = os.getenv("RAZORPAYX_PAYOUT_MODE", "IMPS")  # NEFT | RTGS | IMPS

# RAZORPAYX_FUND_ACCOUNT_MAP_PATH used to live here: a ledger_ref ->
# fund_account_id side-car, needed back when the synthetic ledger had no
# bank details of its own. `transactions` now carries the RazorpayX Tally
# payout columns, so app/live_executor.py builds a composite payout (contact
# + fund account + payout in one call) straight from the ledger row, and
# there is nothing left to provision out of band.

# Reconciliation tuning — deliberately named + centralized so M2's fuzzy
# match tolerance isn't a magic number buried in matching code.
FUZZY_AMOUNT_TOLERANCE_PAISE = int(os.getenv("RR_FUZZY_AMOUNT_TOLERANCE_PAISE", "100"))
FUZZY_TIME_WINDOW_HOURS = int(os.getenv("RR_FUZZY_TIME_WINDOW_HOURS", "48"))

# A fee_mismatch delta at or below this is treated as an explained RazorpayX
# processing fee, not a real discrepancy -- auto-resolved at classification
# time instead of raised as a dispute for a human. Above it, something big
# enough to not plausibly be "just the fee" is going on, and that one still
# needs a human. RazorpayX's own IMPS/NEFT fees run a few rupees per payout
# in practice, so the default gives real headroom above FUZZY_AMOUNT_TOLERANCE
# without waving through a genuinely wrong amount.
KNOWN_FEE_TOLERANCE_PAISE = int(os.getenv("RR_KNOWN_FEE_TOLERANCE_PAISE", "1500"))

# Recovery agent bounds — M5 reads these so limits live in one place.
MAX_RETRY_COUNT = int(os.getenv("RR_MAX_RETRY_COUNT", "3"))
MAX_EXCEPTION_AGE_DAYS = int(os.getenv("RR_MAX_EXCEPTION_AGE_DAYS", "7"))

# Reconcile automatically on a timer instead of only on a manual
# POST /pipeline/reconcile click. Opt-in, same pattern as the live executor:
# 0 (default) means off, nothing runs until someone sets this deliberately.
# Only reconciles (detects exceptions) on the schedule -- routing/dispatch
# (which moves money, even in test mode) stays manual-trigger-only.
RECONCILE_INTERVAL_SECONDS = int(os.getenv("RR_RECONCILE_INTERVAL_SECONDS", "0"))

# Poll RazorpayX for the real status of payouts still in flight, instead of
# waiting for someone to click Sync payout status. Webhooks remain the primary
# path and stay authoritative; this is the pull fallback that lets a missed
# delivery self-heal -- which matters most when no public tunnel is up and the
# webhook cannot reach us at all.
#
# COST: one API call per in-flight payout, per pass. At 5s with 10 payouts in
# flight that is 120 calls/minute, and it keeps running as long as anything sits
# at 'queued'/'processing'. Raise the interval if RazorpayX starts throttling.
# 0 disables the loop and leaves POST /pipeline/sync-payouts manual-only.
SYNC_PAYOUTS_INTERVAL_SECONDS = int(os.getenv("RR_SYNC_PAYOUTS_INTERVAL_SECONDS", "5"))

# Groq -- powers the in-app assistant behind "Need help?". Opt-in, same shape as
# the live executor: empty (default) means the assistant is off and the endpoint
# answers 409 rather than half-working. Used server-side only; the key is never
# sent to the browser, which is the whole reason POST /assistant/chat exists
# instead of calling api.groq.com from the page.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

# Assistant bounds. These exist because the endpoint is an unauthenticated proxy
# to a metered API, and because Groq's own account ceiling (8000 tokens/min at
# time of writing) is low enough to hit in normal use.
#
# Groq's account ceiling is 8000 tokens/minute, regenerating at roughly 133/sec.
# A call costs about: instructions ~850 + aggregates ~250 + open records ~1300 +
# history ~250 + reply ~150, near 2800 tokens -- so sustained throughput is about
# three questions a minute, and RATE_PER_IP sits at that edge rather than above
# it. This app's limit should bite before the vendor's: a 429 we raise explains
# itself, one Groq raises reads as the app being broken.
#
# ASSISTANT_RECORD_LIMIT is a safety valve for an unreasonably large backlog, not
# a routine trim -- it is set high enough that every open exception is normally
# listed, because an assistant that can only discuss the biggest records is worse
# than one that admits it sees none. Lower it only if the token cost bites.
ASSISTANT_MAX_INPUT_CHARS = int(os.getenv("RR_ASSISTANT_MAX_INPUT_CHARS", "1000"))
ASSISTANT_HISTORY_TURNS = int(os.getenv("RR_ASSISTANT_HISTORY_TURNS", "3"))
ASSISTANT_MAX_TOKENS = int(os.getenv("RR_ASSISTANT_MAX_TOKENS", "700"))
ASSISTANT_RECORD_LIMIT = int(os.getenv("RR_ASSISTANT_RECORD_LIMIT", "60"))
ASSISTANT_RATE_PER_IP = int(os.getenv("RR_ASSISTANT_RATE_PER_IP", "3"))
ASSISTANT_RATE_GLOBAL = int(os.getenv("RR_ASSISTANT_RATE_GLOBAL", "6"))
# llama-prompt-guard-2 returns P(injection). Measured separation is wide --
# ~0.0004 for an ordinary question, ~0.9995 for "ignore all previous
# instructions" -- so anything in the middle is genuinely ambiguous and 0.8
# keeps normal phrasing well clear of the cutoff.
ASSISTANT_INJECTION_CUTOFF = float(os.getenv("RR_ASSISTANT_INJECTION_CUTOFF", "0.8"))

# How far back the payout sync keeps re-reading already-settled payouts, looking
# for a late reversal. 'processed' is not terminal in practice: a beneficiary
# bank can return funds days later and RazorpayX flips the payout to 'reversed'.
# Watching only in-flight payouts missed those entirely and left the dashboard
# counting returned money as recovered.
#
# It is a window rather than "forever" because the sweep costs one API call per
# watched payout on every pass, so an unbounded set would grow the per-pass cost
# with every settlement. 0 restores the old behaviour: in-flight payouts only.
SYNC_REVERSAL_WINDOW_DAYS = int(os.getenv("RR_SYNC_REVERSAL_WINDOW_DAYS", "7"))
