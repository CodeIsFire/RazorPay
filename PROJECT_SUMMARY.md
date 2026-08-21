# Reconcile → Recover — project summary

Razorpay buildathon project. Reconciliation engine matches a synthetic ledger
against RazorpayX test-mode transactions, classifies exceptions by cause, then
an automated recovery agent acts on the RazorpayX Payouts API and confirms via
webhooks. Minimal dashboard on top. Built milestone-by-milestone (M0–M7), all
complete.

**Stack:** Python + FastAPI + SQLite (stdlib `sqlite3`, no ORM). No migrations —
schema changes require deleting the dev DB file. `httpx` for direct RazorpayX
REST calls (not the official SDK — it lacks Payout/Contact resources).

## Repo layout

```
recon-recover/
  schema.sql              # single source of truth for the DB schema
  requirements.txt        # fastapi, uvicorn, pydantic, python-dotenv, httpx, pytest
  .env / .env.example     # secrets (gitignored) / template (committed)
  app/                    # application code
  scripts/                # CLI helpers
  tests/                  # pytest suite (61 tests)
  data/                   # sqlite db + generated fixtures (db gitignored)
```

## `app/` — file by file

- **config.py** — reads env vars via `python-dotenv`. `DB_PATH`,
  `RAZORPAYX_KEY_ID/KEY_SECRET/ACCOUNT_NUMBER/WEBHOOK_SECRET/PAYOUT_MODE`,
  `RAZORPAYX_FUND_ACCOUNT_MAP_PATH`, `FUZZY_AMOUNT_TOLERANCE_PAISE=100`,
  `FUZZY_TIME_WINDOW_HOURS=48`, `MAX_RETRY_COUNT=3`, `MAX_EXCEPTION_AGE_DAYS=7`.

- **db.py** — `get_connection()` (reads `config.DB_PATH` at call time so tests
  can monkeypatch it; `check_same_thread=False` because FastAPI's async
  webhook endpoint and sync `get_db` dependency can hand the connection across
  threads within one request). `init_db()` runs `schema.sql`, idempotent.
  `log_audit()` / `fetch_audit_log()` — every stage writes through this one
  audit_log table; nothing summarized away.

- **fixtures.py** — `generate_dataset()`, deterministic (seed=42) synthetic
  ledger + gateway rows, `CASE_PLAN` covering each exception cause on purpose.

- **load_fixtures.py** — loads generated fixtures into the `transactions` table.

- **reconcile.py** — `reconcile(ledger_rows, gateway_rows)`: two-tier matching,
  exact then fuzzy (amount tolerance + time window from config).
  `reconcile_from_db(conn)` wraps it against live DB data.

- **classify.py** — `CAUSES` = failed_payment, fee_mismatch, timing_lag
  (ledger-side) + duplicate, unexplained (gateway-side). `classify()` assigns
  cause per unmatched row. `exception_key` is a deterministic id
  (cause, ledger_ref, gateway_ref) — the idempotency primitive the whole
  exception↔payout linkage is built on. `persist_exceptions()` uses
  `INSERT ... ON CONFLICT DO NOTHING` so re-running never duplicates.

- **funnel.py** — `compute_funnel(conn)`: ingested → matched → exceptions →
  recovered, plus `match_rate`, `amount_recovered_paise`, and
  `gateway_side_anomalies` as an honest side-channel (duplicates/unexplained
  don't fit the 4-stage ledger funnel). Invariant
  `matched + exceptions + recovered == ingested` is asserted in tests.

- **router.py** — `ACTION_MAP` (cause → action type). `PayoutExecutor`
  Protocol + `MockPayoutExecutor` — business logic never touches the network
  directly, swaps to the live executor via DI. `route_exception()` dispatches
  one action per exception, bounded by `MAX_RETRY_COUNT` /
  `MAX_EXCEPTION_AGE_DAYS`; bound hits are logged as `_abandon()`, not
  silently skipped. Executor failures are caught and logged as
  `action_dispatch_failed` (decision="error") rather than crashing the batch.
  `route_open_exceptions()` runs this over all open exceptions, returns
  `{"dispatched", "abandoned", "skipped", "error"}` counts. `confirm_action()`
  is the manual stand-in for a payout.processed/reversed webhook.

- **razorpayx_client.py** — direct REST client via `httpx`, HTTP Basic Auth
  (key_id, key_secret). `create_contact()`, `create_fund_account_bank()`,
  `create_payout()`, `fetch_payout()`. `sanitize_idempotency_key()` SHA-256
  hashes+truncates internal keys to fit RazorpayX's `X-Payout-Idempotency`
  charset (4–36 chars, alnum/hyphen/underscore/space only).

- **live_executor.py** — `RazorpayXPayoutExecutor`, the real
  `PayoutExecutor` implementation. `load_fund_account_map()` reads the
  ledger-ref → fund-account mapping from `RAZORPAYX_FUND_ACCOUNT_MAP_PATH`.

- **webhooks.py** — `verify_signature()` (HMAC-SHA256 of raw body,
  `hmac.compare_digest`), `EVENT_TO_OUTCOME`, `handle_webhook()`. Test-mode
  RazorpayX only fires 5 events (payout.queued/initiated/processed/reversed,
  transaction.created); payouts don't auto-advance in test mode.

- **main.py** — FastAPI app, lifespan calls `init_db()` on startup.
  Endpoints: `GET /health`, `GET /integration/status` (mock vs live, what's
  configured — no secrets leaked), `POST /pipeline/reconcile`,
  `GET /funnel`, `GET /audit`, `POST /pipeline/route`,
  `POST /actions/{id}/confirm`, `POST /webhooks/razorpayx`.
  `get_payout_executor()` picks live executor only once KEY_ID + KEY_SECRET +
  ACCOUNT_NUMBER are all set, else mock. `StaticFiles` mounted at `/` **last**
  (after all API routes) to serve the dashboard as catch-all.

- **static/index.html** — M7 dashboard. Single file, vanilla JS, no build
  step, no external CDN. Polls `/integration/status`, `/funnel`, `/audit`
  every 15s; buttons POST to `/pipeline/reconcile` and `/pipeline/route`.
  KPI row (ingested/matched/exceptions/recovered/anomalies), headline tiles
  (match rate, amount recovered), scrollable audit table. Styled per the
  `dataviz` skill's reference palette — fixed status hues, icon+label (never
  color alone), tabular-nums, light/dark via `prefers-color-scheme`.

## `scripts/`

- **generate_fixtures.py** — regenerates `data/fixtures/*.json` (deterministic).
- **load_demo_data.py** — loads fixtures into the DB, prints a case summary.
- **setup_test_payee.py** — one-off helper for creating a RazorpayX test
  contact/fund-account for live-mode smoke testing.

## `tests/`

61 tests across `test_health`, `test_fixtures`, `test_reconcile`,
`test_classify`, `test_funnel`, `test_router`, `test_razorpayx_client`,
`test_webhooks`. `conftest.py` provides an `isolated_db` fixture
(`monkeypatch.setattr(config, "DB_PATH", tmp_path/...)`) so tests never touch
the dev DB.

## Key conventions

- **Idempotency everywhere**: business keys, not autoincrement ids, drive
  `ON CONFLICT DO NOTHING` inserts (transactions, exceptions) and RazorpayX
  idempotency headers (actions).
- **Audit log is the single source of truth** for "why did/didn't something
  happen" — every stage writes to it, dashboard reads it unfiltered.
- **Executor pattern** keeps `router.py` network-free and fully testable;
  only `live_executor.py` touches `httpx`.
- **No ORM, no migrations, minimal deps** — deliberate, per the project's
  "keep it minimal" instruction.

## Known limitations / what's untested live

- This sandbox has no network route to `api.razorpay.com` (blocked at
  egress) — `razorpayx_client.py` and `live_executor.py` are only tested
  against mocked HTTP, not the real API. Must be smoke-tested on your own
  machine.
- `RAZORPAYX_ACCOUNT_NUMBER` is not yet set in `.env`, so `/integration/status`
  and `get_payout_executor()` currently stay on the mock executor by design.
- `RAZORPAYX_WEBHOOK_SECRET` is empty — the webhook receiver itself was
  live-tested end-to-end (real HTTP POST + real HMAC signature against a
  running server), but receiving a real webhook from RazorpayX needs a public
  URL (tunnel) plus that secret configured.

## Milestone history (git log, oldest → newest)

M0 project skeleton → M1 fixtures + loader → M2 reconciliation engine →
M3 exception classifier → M4 funnel + audit API + demo loader →
M5 action router (mocked payouts) → M6 real RazorpayX REST client + webhook
receiver → M7 dashboard UI.
