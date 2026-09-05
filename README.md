# Reconcile → Recover

Payout reconciliation and automated recovery for RazorpayX.

The app takes three views of the same money — your **ledger** (what you meant
to pay), the **gateway** record (what RazorpayX says happened) and a **bank
statement** (what actually left the account) — matches them against each
other, explains every row that doesn't line up, and then works the difference
by dispatching real payouts through the RazorpayX Payouts API. A payout is
only ever marked recovered when a **webhook says so** — never because the
dispatch call returned 200.

**Stack:** FastAPI + SQLite (stdlib `sqlite3`, no ORM) · React 19 + Vite +
Tailwind v4 + shadcn/Radix · `httpx` for direct RazorpayX REST calls. The
frontend builds into `app/static/dist` and FastAPI serves it, so the whole
thing runs as one process on one port.

---

## 1. Install

Requirements: **Python 3.9+** (verified on 3.9.6) and **Node 20.19+ / 22.12+** (Vite 8's
requirement; verified on 22.16). No database server — SQLite is a file under `data/`.

```bash
git clone https://github.com/CodeIsFire/RazorPay.git
cd RazorPay

# Python side
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# Frontend side — the build output is gitignored, so a fresh clone has no UI
# until you run this once.
npm --prefix frontend ci
npm --prefix frontend run build

# Config: every key is optional. With none set, the app runs fully offline
# against a simulated payout executor.
cp .env.example .env
```

## 2. Run it

```bash
.venv/bin/python -m uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000/**. You land on the entry screen; *Open
dashboard* takes you in.

> Use `python -m uvicorn` rather than the bare `uvicorn` binary — it works
> regardless of whether the venv's script shebangs are intact.

**Working on the frontend?** Run the two halves separately —
`.venv/bin/python -m uvicorn app.main:app` on `:8000`, and
`npm --prefix frontend run dev` on `:5173`. Vite proxies every API route to
`:8000` (see `frontend/vite.config.ts`), so the browser still talks to one
origin and you get hot reload.

## 3. Get data in

Three ways, in order of effort:

**a. Seed the demo dataset** (66 ledger rows, 67 gateway, 68 bank — every one
of the eight causes represented):

```bash
.venv/bin/python -m scripts.load_demo_data
```

It refuses to load on top of an existing table; delete
`data/recon_recover.db` (and its `-wal`/`-shm` sidecars) for a clean reload.
Then reconcile both sources from the dashboard's **Run** menu, or:

```bash
curl -X POST '127.0.0.1:8000/pipeline/reconcile'
curl -X POST '127.0.0.1:8000/pipeline/reconcile?actual_source=bank_statement'
```

**b. Reload demo data from the UI** — the ↻ button at the bottom of the left
rail. It clears everything, reseeds and reconciles both sources in one go. It
never routes, because no button labelled "reload data" should move money.

**c. Upload your own** — the **Data** tab takes a ledger and a bank statement
as CSV. Download the template first (`GET /data/templates/{source}`) so the
columns aren't guesswork; each upload replaces that source entirely and
clears the exceptions and actions derived from it, and tells you the blast
radius before you commit. There is no upload for `gateway`: that side is
RazorpayX's own record, and it arrives over the API or a webhook.

## 4. Using the dashboard

| Tab | What it answers |
|---|---|
| **Overview** | How big is the gap, and what has the system done lately? Funnel, expected-vs-settled hero, recent activity. |
| **Needs attention** | Which rows are unexplained, why, and what's the evidence? Filter by cause/status, expand for the ledger-vs-gateway comparison, export CSV. |
| **Insights** | Where is the backlog concentrated — by cause, by age, by counterparty — and how does the daily reconciled/outstanding split trend? |
| **Activity log** | The unfiltered audit trail. Every stage writes to it; this reads it back verbatim. |
| **Data** | Row counts per source, CSV upload, and a prompt you can hand to an LLM to reshape your own export into the template. |

The **Run** menu drives the pipeline by hand: *Run reconcile*, *Route
exceptions* (confirms first, and shows a preview of exactly what would
dispatch), and *Sync payout status*.

---

## 5. How the logic works

```
transactions (ledger / gateway / bank_statement)
   │
   ├─ reconcile.py   exact → fuzzy → split (1 ledger → N gateway)
   │                                → batch (N ledger → 1 gateway)
   ├─ classify.py    every unmatched row → exactly one of 8 causes
   ├─ router.py      cause → action, dispatched at most once per cause type
   └─ webhooks.py    the ONLY place a payout reaches a terminal state
```

### Matching (`app/reconcile.py`)

Pure functions over plain dicts — no SQLite, so it is testable without a
database. `reconcile_from_db(conn, actual_source)` reconciles the ledger
against `'gateway'` **or** `'bank_statement'` independently; each pass tags
its exceptions with `matched_source`.

1. **Exact** — same `reference_id`, same amount, inside the time window.
2. **Fuzzy** — reference match (or missing reference + same counterparty),
   amount within a rounding tolerance, still inside the window.
3. **Split pass** — one ledger row against N unclaimed gateway rows sharing
   its reference whose amounts sum to it. This is what tells a legitimate
   split disbursement apart from a duplicate: a real duplicate repeats the
   *full* amount and gets claimed at tier 1, leaving the surplus row behind.
4. **Batch pass** — N ledger rows sharing a `settlement_batch_id`, settled by
   one gateway row whose reference is that batch id.

Both tiers are gated by the same time window on purpose, so "matched" means
*same transaction and it behaved normally* — not merely "same identity,
eventually". A real fee deduction or a multi-day settlement delay is meant to
surface as an exception, not be silently absorbed.

### Classification (`app/classify.py`)

Every unmatched row becomes exactly one `Exception_` with one cause:

| Cause | Meaning |
|---|---|
| `failed_payment` | The ledger expected it; nothing settled. |
| `fee_mismatch` | Settled short by roughly a known fee. |
| `timing_lag` | Settled, but outside the window. |
| `duplicate` | The gateway paid twice; the ledger says once. |
| `partial_payment` | Settled short of the expected amount. |
| `unexplained` | Gateway-side row with no ledger instruction behind it. |
| `refund_unmatched` | A refund the ledger hasn't caught up to. |
| `chargeback` | A chargeback debit on the bank side. |

Rules do the classifying. An optional LLM classifier
(`app/ai_classifier.py`, Gemini or Anthropic) can be scored head-to-head
against those rules, but it is **never** allowed to choose or dispatch an
action, and a cause below `RR_AI_CLASSIFIER_MIN_CONFIDENCE` is handed back to
the rules — a confidently wrong cause is worse than no cause, because the
cause is what picks the action.

### Routing (`app/router.py`)

`ACTION_MAP` is the whole policy:

| Cause | Action |
|---|---|
| `failed_payment` | `retry_payout` |
| `fee_mismatch`, `chargeback` | `draft_dispute_note` |
| `timing_lag`, `partial_payment` | `send_reminder` |
| `duplicate`, `unexplained`, `refund_unmatched` | `flag_for_review` |

**Only `retry_payout` moves money, and only it is retried** — bounded by
`MAX_RETRY_COUNT` (3), `MAX_EXCEPTION_AGE_DAYS` (7), and the terminal state
`abandoned`. A retry additionally requires the previous attempt to be
*confirmed failed*: any exception whose latest `retry_payout` is still
`queued`/`processing` is skipped, logged as `action_skipped_in_flight`.
Without that guard, re-running `/pipeline/route` paid every payee again —
dispatch deliberately leaves the exception `open` (dispatch ≠ success), and
`open` is exactly what the router selects.

Everything else dispatches once. `flag_for_review` / `draft_dispute_note`
leave the exception `open` for a human to resolve; `timing_lag` /
`partial_payment` move it to `pending`, resolved by a recheck or the
classifier's own graduation check — never by redispatching.

**Exception lifecycle:** `open` → `pending` → terminal `resolved` /
`abandoned`.

### Confirmation (`app/webhooks.py`)

A dispatch response is not an outcome. `payout.processed` /
`payout.reversed` webhooks — HMAC-verified — are the only path to a terminal
action state. `POST /pipeline/sync-payouts` is the fallback for a webhook
that never arrived: it asks RazorpayX for the status of payouts still
recorded in flight and applies any terminal answer, but only ever looks at
actions no webhook has already settled, so it cannot overwrite a real
outcome. It runs on a timer too (`RR_SYNC_PAYOUTS_INTERVAL_SECONDS`, default
5s; `0` = manual only).

### Reporting (`app/funnel.py`, `app/analytics.py`)

`matched + exceptions + recovered == ingested`, always — asserted in
`tests/test_funnel.py` so a bug here fails loudly instead of quietly skewing
the numbers. Gateway-side anomalies are reported *outside* that funnel,
because a duplicate's ledger row matched fine and a refund is a real
gateway-originated event, not a ledger row failing to reconcile.

Two things the analytics do deliberately: exception **age comes from the
transaction's `occurred_at`**, never `exceptions.created_at` (a fresh load
creates every exception in the same second, which would put the entire
backlog in one bucket), and an exception is anchored to a transaction by
**ledger ref *or* gateway ref**, because `unexplained` rows have no ledger
ref and batch rows carry a comma-joined list.

`/analytics/daily`'s outstanding total is *smaller* than
`/analytics/exceptions`' value at risk, on purpose: the difference is exactly
the gateway-side orphans, which have no ledger row and therefore no business
day. Two questions, two totals — both asserted in tests.

### Design rules worth knowing before you change anything

- **Idempotency via business keys**, not autoincrement ids. `exception_key`
  and RazorpayX idempotency headers make re-running the pipeline and
  replaying webhooks safe.
- **The audit log is the single source of truth** for why something did or
  didn't happen. Every stage writes to it; the dashboard reads it unfiltered.
- **The executor pattern keeps `router.py` network-free.** Only
  `live_executor.py` touches `httpx`, which is why the router is fully
  testable offline.
- **Anything inferring meaning from a row's *absence* must filter on
  `matched_source`.** Unscoped, reconciling a bank statement silently marked
  every gateway-side partial payment resolved. Regression-tested in
  `tests/test_partial_payment.py`.
- **The payout sync path is frozen.** Four of its properties are
  load-bearing and easy to break by accident. `scripts/check_sync_frozen.py`
  hashes that surface against a recorded baseline — a tripwire, not a lock.
  If you mean to change it, update the baseline in the same commit.

---

## 6. Going live with RazorpayX

Everything below is optional; with no keys the app uses `MockPayoutExecutor`
and stays entirely offline.

| Variable | Effect |
|---|---|
| `RAZORPAYX_KEY_ID` / `_KEY_SECRET` / `_ACCOUNT_NUMBER` | All three present ⇒ the live executor activates. Two of three keeps the mock. |
| `RAZORPAYX_WEBHOOK_SECRET` | HMAC verification for incoming webhooks. |
| `RAZORPAYX_PAYOUT_MODE` | `NEFT` \| `RTGS` \| `IMPS` (default `IMPS`). |
| `GROQ_API_KEY` | Turns on the in-app assistant. Server-side only, never sent to the browser. |
| `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` | The optional cause classifier, for evaluation only. |

`GET /integration/status` reports which executor is actually live and which
credentials it found.

Payouts are sent as a **composite call** (contact + fund account + payout in
one request) built from the ledger row's own fields, so adding a payee is a
data change, not a setup step. Fixture IFSC codes are real and verified —
RazorpayX validates IFSC when creating the fund account, so well-formed
inventions get rejected.

Webhook delivery needs a public URL, registered under **Banking+ → Developer
Controls** (not the Payments product's webhook settings). Note that
test-mode payouts don't advance on their own — someone has to nudge one from
the RazorpayX dashboard before `payout.processed` ever fires — and that a
payout only updates this dashboard if this database holds its `actions` row,
so reloading fixtures orphans payouts already created at RazorpayX.

## 7. API

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/health`, `/integration/status` | Liveness; executor + credential wiring. |
| `POST` | `/pipeline/reconcile?actual_source=` | Match + classify against `gateway` (default) or `bank_statement`. |
| `POST` | `/pipeline/route` | Dispatch actions for open exceptions. |
| `GET` | `/pipeline/route/preview` | What routing *would* do. Read-only. |
| `POST` | `/pipeline/sync-payouts` | Ask RazorpayX about in-flight payouts. |
| `GET` | `/funnel`, `/analytics/exceptions`, `/analytics/daily` | Dashboard numbers. |
| `GET` | `/exceptions`, `/exceptions/{key}/detail`, `/audit` | Backlog and trail. |
| `POST` | `/exceptions/{key}/resolve`, `/{key}/recheck`, `/actions/{id}/confirm` | Human resolution paths. |
| `POST` | `/webhooks/razorpayx` | Signed payout events. The only terminal-state path. |
| `GET`/`POST` | `/data/summary`, `/data/templates/{source}`, `/data/upload/{source}` | Your own CSV data (`?dry_run=true` previews). |
| `POST` | `/demo/reset` | Clear, reseed and reconcile the demo dataset. Never routes. |
| `POST` | `/assistant/chat` | The in-app assistant, if a Groq key is set. |

## 8. Tests

```bash
.venv/bin/python -m pytest tests/ -q     # 326 passed
npm --prefix frontend run test           # 178 passed, 22 files
.venv/bin/python -m scripts.check_sync_frozen   # the frozen-sync tripwire
```

The classifier can also be scored against held-out data — same generators,
a different RNG stream, which is the closest thing to unseen data a synthetic
corpus offers:

```bash
.venv/bin/python -m scripts.evaluate --seed 7
```

Every rule in `classify.py` was written while looking at the seed-0 fixture,
so seed 0 measures only that the rules do what their author intended. A
different offset is the honest number to quote. The rules-side evaluation
runs with no API keys at all — a requirement, not a convenience, since anyone
cloning this repo has none.

## 9. Deploying

`vercel.json` builds the frontend, then Vercel's zero-config Python runtime
wraps `app/main.py`; FastAPI serves both the API and the built dashboard from
one origin.

⚠️ **SQLite on Vercel is a degraded mode, not persistence.** The function
filesystem is read-only outside `/tmp`, so `config.py` puts the database
there — and `/tmp` can be empty on the next cold start. Point `RR_DB_PATH` at
something durable for anything beyond a preview.

⚠️ **Set `RR_API_TOKEN` on any deployment.** A deployed URL is reachable by
anyone who finds it, and the state-changing endpoints — `/pipeline/route`,
`/actions/{id}/confirm`, `/demo/reset`, `/data/upload/{source}`,
`/assistant/chat` — move money, destroy data and spend metered API credit.
`app/auth.py` puts them behind a bearer token:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"   # then set RR_API_TOKEN
```

Leaving it blank is only safe with no live RazorpayX credentials configured,
which is the local and test case — the app can reach nothing but the mock
executor there. Blank *with* live credentials present is refused outright
(503 on those endpoints) rather than served open. `GET /integration/status`
reports `api_token_configured` so you can confirm which state you are in.

The dashboard sends the token from `localStorage['rr_api_token']`; there is no
login UI, so seed it once in the browser console on a deployment that sets one:

```js
localStorage.setItem('rr_api_token', '<the same value as RR_API_TOKEN>')
```

Read-only endpoints are deliberately not behind the token — the dashboard
fetches them on load, so a credential it would have to ship to every visitor
would protect nothing. `/webhooks/razorpayx` is exempt too: RazorpayX signs
its deliveries, and that HMAC check (`app/webhooks.py`) is its authentication.

## 10. Repo layout

```
schema.sql            single source of truth for the DB schema
app/
  config.py             env-driven knobs (tolerances, bounds, secrets)
  db.py                 connection + audit/exception read-write helpers
  fixtures.py           deterministic synthetic data generators
  reconcile.py          pure matcher: ledger vs one actual source
  classify.py           MatchResult → Exception_, one cause each
  router.py             cause → action, bounds, confirm/resolve/recheck
  auth.py               bearer-token guard on the state-changing endpoints
  webhooks.py           HMAC verification, payout.processed/reversed
  funnel.py             ingested/matched/exceptions/recovered + invariant
  analytics.py          backlog by cause/age/counterparty
  ingest.py             user CSV upload, validation, replace
  demo.py               POST /demo/reset — reseed + reconcile, never route
  live_executor.py      the real PayoutExecutor (mock swaps in unconfigured)
  razorpayx_client.py   httpx REST client (Basic Auth)
  main.py               FastAPI app, routes, background timers
  static/dist/          dashboard build output (gitignored)
frontend/               React + Vite + Tailwind v4 + shadcn dashboard
scripts/                fixture regen, demo loader, evaluation, sync tripwire
tests/                  326 pytest tests
data/                   SQLite DB (gitignored) + generated fixtures
docs/superpowers/specs/ design docs written before the code
```

---

## 11. Skills used

This project was built with Claude Code, using **agent skills** — packaged
instruction sets that shape how the model approaches a task. Three groups,
with what each one actually left behind in this repo.

### Workflow (Superpowers plugin, v6.3.0)

| Skill | What it did here | Trace in the repo |
|---|---|---|
| `using-superpowers` | The entry rule: check for a relevant skill before acting, every time. | — |
| `brainstorming` | Turned each feature request into an approved design before any code. Classifies work as spike / bounded / architectural and refuses to start until the design is approved. | `docs/superpowers/specs/2026-08-30-frontend-react-migration-design.md`, `2026-09-02-data-upload-design.md` |
| `writing-plans` | Converted an approved design into a step-by-step implementation plan. | `~/.claude/plans/*.md` (7 plans) |
| `executing-plans` / `subagent-driven-development` | Ran those plans task by task with review checkpoints. | `.claude/settings.local.json` (the `sdd-workspace` permission) |
| `using-git-worktrees` | Isolated feature work from the main checkout. | `.claude/worktrees/data-upload-feature/` |
| `test-driven-development` | Test first, then implementation — 326 backend tests across 24 app modules, plus 178 in the frontend. | `tests/` |
| `verification-before-completion` | No "it works" without the command output to prove it. Every number in this README was re-run before it was written down. | — |
| `systematic-debugging` | Root-cause before fix. Produced the guards documented above — the in-flight retry gate, the `matched_source` scoping fix. | `tests/test_partial_payment.py`, `tests/test_router.py` |
| `requesting-code-review` / `receiving-code-review` | Review passes between implementation and merge. | — |

### Design (Claude Code built-ins)

| Skill | What it did here |
|---|---|
| `dataviz` | Chose the form for each panel and validated the chart palette for contrast in both themes — the `--chart-*` tokens in `frontend/src/index.css` are its output. |
| `frontend-design` | The dashboard's visual direction: type scale, spacing, the pill/notice vocabulary, and staying deliberately un-templated. |

### Installed third-party skills

Pinned by source and content hash in `skills-lock.json` and vendored under
`.agents/skills/`. Both live in the workspace directory *above* this repo, so
they are not part of the clone — 12 of them from
[`emilkowalski/skill`](https://github.com/emilkowalski/skill) and one from
[`Leonxlnx/taste-skill`](https://github.com/Leonxlnx/taste-skill):

`animate` · `animate-expo` · `animation-vocabulary` · `apple-design` ·
`ask-sonner` · `design-taste-frontend` · `emil-design-eng` ·
`find-animation-opportunities` · `improve-animations` · `pick-ui-library` ·
`prototype` · `review-animations` · `write-swift`

The motion work in the dashboard — the gap hero that sweeps once on load and
never re-animates on the 15s poll, tab transitions that unmount cleanly,
everything collapsing to its end state under `prefers-reduced-motion` — came
out of `animate` and `emil-design-eng`. The rest of the bundle installs with
them; `animate-expo`, `write-swift` and `ask-sonner` have no React-web,
Swift or Sonner surface to touch in this project.

The Superpowers workflow skills come from the official Claude Code plugin
marketplace (`/plugin` in Claude Code, `superpowers` v6.3.0).
