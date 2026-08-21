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

# Maps our internal counterparty identifier (ledger_ref) -> a RazorpayX
# fund_account_id, since the synthetic ledger has no real bank details.
# See scripts/setup_test_payee.py for how to populate this file.
RAZORPAYX_FUND_ACCOUNT_MAP_PATH = os.getenv(
    "RR_FUND_ACCOUNT_MAP_PATH", str(DATA_DIR / "fund_account_map.json")
)

# Reconciliation tuning — deliberately named + centralized so M2's fuzzy
# match tolerance isn't a magic number buried in matching code.
FUZZY_AMOUNT_TOLERANCE_PAISE = int(os.getenv("RR_FUZZY_AMOUNT_TOLERANCE_PAISE", "100"))
FUZZY_TIME_WINDOW_HOURS = int(os.getenv("RR_FUZZY_TIME_WINDOW_HOURS", "48"))

# Recovery agent bounds — M5 reads these so limits live in one place.
MAX_RETRY_COUNT = int(os.getenv("RR_MAX_RETRY_COUNT", "3"))
MAX_EXCEPTION_AGE_DAYS = int(os.getenv("RR_MAX_EXCEPTION_AGE_DAYS", "7"))
