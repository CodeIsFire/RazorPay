# Reconcile → Recover — project summary

Reconciles a synthetic ledger against RazorpayX test-mode transactions and a
bank statement, classifies mismatches by cause, and runs an automated
recovery agent over the real RazorpayX Payouts API — confirmed only by real
webhooks, never assumed from a dispatch response. Dashboard on top, themed to
match RazorpayX's own product (own branding, not a copy of their logo).

**Stack:** Python + FastAPI + SQLite (stdlib `sqlite3`, no ORM, no
migrations — schema changes mean deleting the dev DB file and reloading
fixtures). `httpx` for direct RazorpayX REST calls (the official SDK lacks
Payout/Contact resources). Dashboard is React + Vite + Tailwind v4 + shadcn
(`frontend/`), built into `app/static/dist` and served by FastAPI itself.

**Running it:** `npm --prefix frontend run build` once, then
`uvicorn app.main:app`. The build output is gitignored, so a fresh clone has
no UI until you build — the API and the test suite work regardless.

## Pipeline

```
transactions (ledger / gateway / bank_statement)
  → reconcile.py   match: exact → fuzzy → split (1 ledger→N gateway)
                          → batch (N ledger→1 gateway)
  → classify.py    unmatched rows → one of 9 causes
  → router.py      cause → action, dispatched at most once per cause type
  → webhook        only place a payout action reaches a terminal state
```

**Causes** (`app/classify.py:CAUSES`): `failed_payment`, `fee_mismatch`,
`timing_lag`, `duplicate`, `unexplained`, `refund_unmatched`, `chargeback`,
`partial_payment`. Each maps to one action in `router.py:ACTION_MAP`.

**Only `retry_payout` (failed_payment) is retried/bounded** —
`MAX_RETRY_COUNT`, `MAX_EXCEPTION_AGE_DAYS`, terminal state `abandoned`.
A retry additionally requires the previous attempt to be **confirmed failed**:
`route_exception` skips any exception whose latest `retry_payout` action is
still `queued`/`processing`, logging `action_skipped_in_flight`. Without that
guard, re-running `/pipeline/route` paid every payee again — dispatch leaves
the exception `open` on purpose (dispatch ≠ success), and `open` is exactly
what `route_open_exceptions` selects, so an unconfirmed attempt looked
identical to a failed one. Only `confirm_action('reversed')` reopens for a
genuine retry.
Every other action dispatches once: `flag_for_review`/`draft_dispute_note`
causes stay `open` afterward (human resolves via `resolve_exception`);
`timing_lag`/`partial_payment` move to `pending` (resolved by
`recheck_exception()` or the classifier's own graduation check, never by
redispatching).

**Exception status** (4 states): `open` → `pending` (timing_lag/
partial_payment only) or terminal `resolved`/`abandoned`.

**Backlog analytics** (`app/analytics.py`, `GET /analytics/exceptions`, Insights
tab): non-terminal exceptions broken down by cause, age and counterparty. Two
things it does deliberately, both with tests — **age comes from the
transaction's `occurred_at`, never `exceptions.created_at`** (a fresh load
creates every exception in the same second, so `created_at` puts the whole
backlog in one bucket), and an exception is anchored to a transaction by
**ledger ref *or* gateway ref**, because `unexplained` rows have no ledger_ref
at all and batch rows carry a comma-joined list — a plain join on `ledger_ref`
silently understates the money at risk.

`GET /analytics/daily` is the timeline behind the Insights column chart: ledger
value per business day, split reconciled vs still outstanding. Its outstanding
total is **deliberately smaller** than `/analytics/exceptions`' value at risk —
the difference is exactly the gateway-side orphans, which have no ledger row and
therefore no business day. Two questions, two totals; both are asserted.

## Repo layout

```
schema.sql          single source of truth for the DB schema
app/
  config.py          env-driven tuning knobs (tolerances, bounds, secrets)
  db.py              connection + audit_log/exceptions read/write helpers
  fixtures.py         generate_dataset() / generate_bank_statement() /
                       generate_split_and_batch_cases() — deterministic synthetic data
  load_fixtures.py    loads generated rows into `transactions`
  reconcile.py         pure matcher: ledger vs one actual source at a time
  classify.py          MatchResult → Exception_ rows, one cause each
  funnel.py            ingested/matched/exceptions/recovered + invariant
  analytics.py         backlog by cause/age/counterparty (the Insights tab)
  router.py             cause → action dispatch, bounds, confirm/resolve/recheck
  razorpayx_client.py    httpx REST client (Basic Auth)
  live_executor.py       real PayoutExecutor impl (mock swaps in when unconfigured)
  webhooks.py            HMAC signature verification, payout.processed/reversed
  main.py                 FastAPI app + all routes
  static/dist/            dashboard build output, served at "/" (gitignored)
frontend/               dashboard source: React + Vite + Tailwind v4 + shadcn,
                          with Bklit (charts), Kokonut UI and Motion.
                          `npm run build` writes app/static/dist.
                          `npm run dev` serves :5173 and proxies the API to :8000.
scripts/               fixture regen, demo data loader, live test-payee setup
tests/                  109 tests, pytest
data/                   sqlite db + generated fixtures (db gitignored)
```

## Key conventions

- **Idempotency via business keys**, not autoincrement ids —
  `exception_key`/RazorpayX idempotency headers make re-running the pipeline
  and replaying webhooks safe.
- **Audit log is the single source of truth** for why something did or
  didn't happen; every stage writes to it, dashboard reads it unfiltered.
- **Executor pattern** keeps `router.py` network-free and testable — only
  `live_executor.py` touches `httpx`.
- **Multi-source reconciliation**: `reconcile_from_db(conn, actual_source)`
  reconciles the ledger against `'gateway'` or `'bank_statement'`
  independently; each pass's exceptions carry `matched_source`.
  **Anything that infers meaning from a row's ABSENCE must filter on
  `matched_source`.** `_resolve_completed_partial_payments` graduates a
  partial payment when it stops appearing in a pass's output — and split/batch
  matching is gateway-side, so a `bank_statement` pass emits none at all.
  Unscoped, reconciling a bank statement silently marked every gateway-side
  partial payment `resolved` while its own detail still read "short of the
  expected". Fixed and regression-tested in `tests/test_partial_payment.py`.
- **`scripts/load_demo_data.py` loads all three generators**, so every one of
  the 8 causes is exercised: `generate_dataset` (6 causes),
  `generate_split_and_batch_cases` (partial_payment),
  `generate_bank_statement` (refund_unmatched, chargeback). It needs two
  reconcile passes — `/pipeline/reconcile` and
  `?actual_source=bank_statement`. `generate_settled_bank_lines` gives the
  split/batch ledger rows a bank-side view they'd otherwise lack; without it
  the bank pass reports 6 failed_payments that are fixture artefacts, not
  findings.
- **A ledger row is a payout instruction.** `transactions` is shaped after
  RazorpayX's Tally batch-payout CSV: alongside the reconciliation fields it
  carries `payout_purpose`/`payout_mode`, the fund account
  (`fund_account_type` + either `_ifsc`/`_number` or `_vpa`, never both), and
  the full contact record. All nullable — only `source='ledger'` rows fill
  them; gateway/bank rows are observations, not instructions. The template's
  reference id, amount (rupees there, `amount_paise` here), date and contact
  name reuse the existing `reference_id`/`amount_paise`/`occurred_at`/
  `counterparty` columns rather than duplicating them.
  Vendor bank/contact details come from `fixtures.vendor_profile(name)`,
  seeded from a SHA-256 of the vendor name — stable across runs and machines,
  and drawing them consumes none of `generate_dataset()`'s RNG stream, so
  adding this data left every pre-existing fixture row byte-identical.

## Live integration status

- `/integration/status` reports `"executor": "live"` — real credentials
  configured, real network egress to `api.razorpay.com` works.
- Webhook delivery needs a public URL. Currently proxied via a `cloudflared`
  quick tunnel — **ephemeral**: restarting it changes the URL, which then
  needs re-saving in RazorpayX's dashboard (**Banking+ → Developer
  Controls**, not the Payments product's webhook settings) — OTP-gated,
  manual, not automatable.
- Test-mode payouts don't auto-advance on their own — someone has to nudge
  a payout forward from the RazorpayX dashboard for `payout.processed`/
  `payout.reversed` to ever fire.
- **A payout only updates the dashboard if this DB has its `actions` row.**
  Deleting `data/recon_recover.db` to reload fixtures orphans every payout
  already created at RazorpayX — the account accumulates them across runs
  (76 at last count vs 9 tracked), and advancing an orphan produces a
  `webhook_unmatched` audit entry and no visible change. That's working as
  intended: there's no exception left to resolve, and inventing one would
  fabricate reconciliation state.
- **`POST /pipeline/sync-payouts`** (Run ⌄ → Sync payout status) is the
  fallback for a webhook that never arrived: it asks RazorpayX for the
  current status of every payout still recorded in flight and applies any
  terminal answer. Webhooks stay authoritative — it only ever looks at
  actions no webhook has settled, so it can't overwrite a real outcome. It
  does not discover untracked payouts, for the reason above.
- **Payees are no longer provisioned out of band.** `live_executor.py` sends a
  **composite payout** (contact + fund account + payout in one call) built from
  the ledger row's own Tally fields, so adding a payee is a data change, not a
  setup step. The old `data/fund_account_map.json` side-car and
  `scripts/setup_test_payee.py` are gone; the JSON file is inert if still on disk.
  Verified live: `/pipeline/route` dispatched 9 real test-mode payouts, 6 to bank
  accounts (NEFT/IMPS) and 3 to VPAs (UPI), 0 errors.
- Fixture IFSC codes are **real**, each verified against `ifsc.razorpay.com`
  (`fixtures.py:IFSC_CODES`). RazorpayX validates IFSC when it creates the fund
  account, so invented-but-well-formed codes get rejected outright.
- The `X-Payout-Idempotency` header hashes the request **body** alongside our
  internal key (`razorpayx_client.idempotency_header_for`). RazorpayX remembers
  keys longer than a dev DB lives, and our key resets to `attempt1` on every
  rebuild — without the body in the hash, a rebuilt DB gets
  `Different request body sent for the same Idempotency Header`. Identical
  redispatches still dedupe. `actions.idempotency_key` in the DB is unchanged.
