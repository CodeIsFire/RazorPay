"""Central config. Everything reads from env vars with sane local defaults
so the app runs with zero setup until M6 needs real RazorpayX credentials."""
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# override=True, deliberately. python-dotenv defaults to letting an existing
# environment variable win, and that cost real debugging time here: a stale
# `export GEMINI_API_KEY=AIzaSyYourKeyHere` left in a developer's ~/.zshrc
# silently shadowed the real key in .env, and the only symptom was the provider
# replying "API key not valid" -- which reads as a bad key, not as the wrong
# key being sent. Nothing distinguishes the two from the error alone.
#
# Every value in this project's .env is project-scoped, and writing one there
# is a deliberate statement about THIS app; an inherited shell export is
# ambient and, as demonstrated, frequently stale. So the file wins. Anyone
# genuinely wanting a one-off override can edit .env or pass the value at the
# call site, both of which are visible rather than invisible.
load_dotenv(override=True)


def _int_env(name: str, default: str) -> int:
    """int(os.getenv(name, default)), except a var that is SET but blank
    doesn't fall through to the default -- os.getenv only substitutes the
    default when the var is absent, not when it's present-but-empty. A host
    whose env UI writes an unset field as "" rather than omitting it (Vercel's
    does) turned that gap into int("") crashing the app at import, before a
    single route existed to report why. Blank is now treated the same as
    absent, for every numeric setting below."""
    value = os.getenv(name)
    return int(value) if value else int(default)


def _float_env(name: str, default: str) -> float:
    value = os.getenv(name)
    return float(value) if value else float(default)


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

def _resolve_db_path(explicit: Optional[str], on_vercel: bool, data_dir: Path) -> str:
    """Vercel's function filesystem is read-only outside /tmp, so the
    repo-relative default would fail the moment init_db() tried to create the
    file there. /tmp is writable but not persistent -- it can be empty on the
    next cold start -- so this is a deliberate degraded mode for a preview
    deployment, not a fix for real persistence. An explicit path always wins,
    on Vercel or off it, for whoever points this at something that actually
    persists.

    A plain function rather than inline module-level branching so it's
    testable without reloading this module -- reloading would re-run
    load_dotenv(override=True) above and repollute the real process
    environment for every test that runs afterward."""
    if explicit:
        return explicit
    return "/tmp/recon_recover.db" if on_vercel else str(data_dir / "recon_recover.db")


DB_PATH = _resolve_db_path(os.getenv("RR_DB_PATH"), bool(os.getenv("VERCEL")), DATA_DIR)

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
FUZZY_AMOUNT_TOLERANCE_PAISE = _int_env("RR_FUZZY_AMOUNT_TOLERANCE_PAISE", "100")
FUZZY_TIME_WINDOW_HOURS = _int_env("RR_FUZZY_TIME_WINDOW_HOURS", "48")

# A fee_mismatch delta at or below this is treated as an explained RazorpayX
# processing fee, not a real discrepancy -- auto-resolved at classification
# time instead of raised as a dispute for a human. Above it, something big
# enough to not plausibly be "just the fee" is going on, and that one still
# needs a human. RazorpayX's own IMPS/NEFT fees run a few rupees per payout
# in practice, so the default gives real headroom above FUZZY_AMOUNT_TOLERANCE
# without waving through a genuinely wrong amount.
KNOWN_FEE_TOLERANCE_PAISE = _int_env("RR_KNOWN_FEE_TOLERANCE_PAISE", "1500")

# Recovery agent bounds — M5 reads these so limits live in one place.
MAX_RETRY_COUNT = _int_env("RR_MAX_RETRY_COUNT", "3")
MAX_EXCEPTION_AGE_DAYS = _int_env("RR_MAX_EXCEPTION_AGE_DAYS", "7")

# Reconcile automatically on a timer instead of only on a manual
# POST /pipeline/reconcile click. Opt-in, same pattern as the live executor:
# 0 (default) means off, nothing runs until someone sets this deliberately.
# Only reconciles (detects exceptions) on the schedule -- routing/dispatch
# (which moves money, even in test mode) stays manual-trigger-only.
RECONCILE_INTERVAL_SECONDS = _int_env("RR_RECONCILE_INTERVAL_SECONDS", "0")

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
SYNC_PAYOUTS_INTERVAL_SECONDS = _int_env("RR_SYNC_PAYOUTS_INTERVAL_SECONDS", "5")

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
ASSISTANT_MAX_INPUT_CHARS = _int_env("RR_ASSISTANT_MAX_INPUT_CHARS", "1000")
ASSISTANT_HISTORY_TURNS = _int_env("RR_ASSISTANT_HISTORY_TURNS", "3")
ASSISTANT_MAX_TOKENS = _int_env("RR_ASSISTANT_MAX_TOKENS", "700")
ASSISTANT_RECORD_LIMIT = _int_env("RR_ASSISTANT_RECORD_LIMIT", "60")
ASSISTANT_RATE_PER_IP = _int_env("RR_ASSISTANT_RATE_PER_IP", "3")
ASSISTANT_RATE_GLOBAL = _int_env("RR_ASSISTANT_RATE_GLOBAL", "6")
# llama-prompt-guard-2 returns P(injection). Measured separation is wide --
# ~0.0004 for an ordinary question, ~0.9995 for "ignore all previous
# instructions" -- so anything in the middle is genuinely ambiguous and 0.8
# keeps normal phrasing well clear of the cutoff.
ASSISTANT_INJECTION_CUTOFF = _float_env("RR_ASSISTANT_INJECTION_CUTOFF", "0.8")

# How far back the payout sync keeps re-reading already-settled payouts, looking
# for a late reversal. 'processed' is not terminal in practice: a beneficiary
# bank can return funds days later and RazorpayX flips the payout to 'reversed'.
# Watching only in-flight payouts missed those entirely and left the dashboard
# counting returned money as recovered.
#
# It is a window rather than "forever" because the sweep costs one API call per
# watched payout on every pass, so an unbounded set would grow the per-pass cost
# with every settlement. 0 restores the old behaviour: in-flight payouts only.
SYNC_REVERSAL_WINDOW_DAYS = _int_env("RR_SYNC_REVERSAL_WINDOW_DAYS", "7")

# --- AI cause classifier ---------------------------------------------------
# A second model, on the Anthropic Messages API rather than Groq, used only to
# classify exception causes -- never to choose or dispatch an action. It is
# measured head-to-head against the deterministic rules in classify.py by
# app/evaluation.py; see app/stress_fixtures.py for why the comparison is run
# against adversarial cases rather than the standard fixture.
#
# Absent key = the classifier is off and every rules-side number still
# reproduces, the same opt-in shape as GROQ_API_KEY and the live executor. That
# is a requirement, not a convenience: the submission repo is public and a
# reviewer with no keys at all has to be able to run the evaluation.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5")
# Overridable so the same client works against api.anthropic.com or a
# compatible gateway without a code change.
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
# Below this the model's own stated confidence is treated as "don't know" and
# the row is left to the rules. Abstaining is the behaviour we want from a
# classifier that is wrong in a different way than the rules are -- a confident
# wrong cause is worse than no cause, because a cause is what picks the action.
AI_CLASSIFIER_MIN_CONFIDENCE = _float_env("RR_AI_CLASSIFIER_MIN_CONFIDENCE", "0.6")

# Gemini, the classifier's current provider. Same opt-in contract as every
# other credential here: absent key = the classifier is off and every
# rules-side number still reproduces.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")

# Which provider the classifier asks. Named rather than inferred from whichever
# key happens to be set, because two are set in this project and silently
# picking one would make an evaluation result depend on environment ordering --
# the reported number has to say which model produced it.
AI_CLASSIFIER_PROVIDER = os.getenv("RR_AI_CLASSIFIER_PROVIDER", "gemini")

# --- User-uploaded data (app/ingest.py) ------------------------------------
# Bounds on a CSV upload, checked before any parsing so a hostile or
# accidental 500MB file costs a length check rather than a full parse.
#
# The row ceiling is the one that actually bites: parse_csv() holds every
# row in memory and reports every bad one at once (all-or-nothing is the
# whole point), so the error list itself is bounded by this too. 5000 rows
# is comfortably more than a month of payouts for the scale this app
# targets, and the byte ceiling is roughly what 5000 rows of the widest
# template weighs with room to spare.
UPLOAD_MAX_ROWS = _int_env("RR_UPLOAD_MAX_ROWS", "5000")
UPLOAD_MAX_BYTES = _int_env("RR_UPLOAD_MAX_BYTES", "2000000")
