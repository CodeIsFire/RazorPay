# Reconcile → Recover: user-uploaded ledger and bank statement data

**Date:** 2026-09-02
**Status:** approved by user, pending implementation plan

## Goal

Let a user load their own ledger and bank statement data into the app as CSV
files, instead of relying only on the synthetic fixture generator
(`app/fixtures.py`), and run the existing reconciliation pipeline against it.
Each upload has a downloadable template so the expected columns are never
guessed.

## Context

- `transactions` (`schema.sql`) already models three sources: `ledger`,
  `gateway`, `bank_statement`. `gateway` is RazorpayX's own record, sourced
  from the live API/webhooks (`app/live_executor.py`, `app/webhooks.py`) or
  the fixture generator — never something a user would have as a file, so it
  is **out of scope** for this feature.
- `app/load_fixtures.py:load_transactions()` is already a generic
  `list[dict]` → `INSERT` helper, reused as-is by the new ingest path.
- `POST /pipeline/reconcile?actual_source=bank_statement` already exists and
  works against whatever is currently in the table — this feature only needs
  to get rows *into* `transactions`; matching/classifying is unchanged.
- `scripts/load_demo_data.py` deliberately keeps fixture-seeding off the HTTP
  API surface ("seeding fake data is a dev-time concern"). This feature does
  not change that script or its reasoning — it adds a *separate*, real
  ingestion path for a user's own data, not another way to load fixtures.
- **Sync logic is frozen**: `app/router.py`, `app/main.py`'s payout-sync
  loop, and their tests are not touched by this design. The only interaction
  is that a replace can delete `actions` rows (see below), which is new code
  in the new ingest module, not a change to the sync path itself.
- `RunMenu.tsx` currently only exposes `Run reconcile` against
  `actual_source='gateway'` — there is no UI trigger for the
  `bank_statement` pass today. This feature does not add one; reconciling
  newly-uploaded data is a separate, later action the user already has
  available via the `gateway` button (for ledger-vs-gateway) or will need a
  follow-up UI change for `bank_statement` — noted here as a known gap, not
  solved by this design.

## Decisions made during brainstorming

1. **Scope: `ledger` and `bank_statement` only.** `gateway` stays
   live-API/fixture-driven.
2. **Replace semantics.** Uploading a file replaces that source's existing
   rows (and cascades to dependent `exceptions`/`actions`), rather than
   appending or blocking on existing data.
3. **CSV only.** No spreadsheet (`.xlsx`) support.
4. **New "Data" tab** in the dashboard (5th tab, alongside Overview / Needs
   attention / Insights / Activity log).
5. **All-or-nothing validation.** A file with any invalid row writes nothing;
   every problem row is reported at once.
6. **Template = reconciliation fields only.** No RazorpayX payout-dispatch
   columns (fund account, contact, payout purpose/mode) — uploaded ledger
   rows can be reconciled but are not dispatchable via `Run route` unless
   those fields are populated some other way. Keeps this feature about
   reconciliation, not about wiring arbitrary uploaded bank details into a
   real payout flow.

## Architecture

```
app/
  ingest.py                 # new: column schemas, CSV parsing/validation,
                             #      template generation, replace+cascade
  main.py                   # + two routes: GET /data/templates/{source},
                             #                POST /data/upload/{source}
  config.py                 # + RR_UPLOAD_MAX_ROWS, RR_UPLOAD_MAX_BYTES
frontend/src/
  components/data/
    DataTab.tsx              # new: two upload cards (ledger, bank_statement)
    UploadCard.tsx            # new: file picker, template link, result/error UI
    UploadConfirm.tsx         # new: impact-preview confirm panel (RouteConfirm-shaped)
  lib/
    labels.ts                 # + 'data' tab id/title
    api.ts                    # + templateUrl(), upload()
    queries.ts                 # + useUpload(), useDataSummary()
  App.tsx                      # + 'data' case in TabContent
```

## Backend

### Column schemas (`app/ingest.py`)

One schema table per source, shared by the parser and the template
generator so they cannot drift apart:

| Column | ledger | bank_statement | Notes |
|---|---|---|---|
| `external_ref` | required, unique in file | required, unique in file | |
| `amount_paise` | required, integer > 0 | required, integer > 0 | |
| `occurred_at` | required, ISO date/datetime | required, ISO date/datetime | date-only (`YYYY-MM-DD`) is accepted and parsed as midnight UTC that day |
| `counterparty` | optional | optional | |
| `narration` | optional | optional | |
| `reference_id` | — (set to `external_ref` automatically) | optional | absent on a bank row forces the matcher's fuzzy fallback, same as `generate_bank_statement()` |
| `transaction_type` | — (always `payment`) | optional, default `payment` | `payment` \| `refund` \| `chargeback` |
| `original_ref` | — | required when `transaction_type != payment` | traces a reversal to what it reverses |

### Endpoints

- `GET /data/templates/{source}` — `source` ∈ `ledger`, `bank_statement`.
  Returns a CSV (`Content-Disposition: attachment`) with the header row from
  the schema table above, plus one example data row.
- `GET /data/summary` — `{ledger: {rows, updated_at}, bank_statement: {...}}`,
  read on `DataTab` mount so the tab isn't blank after a reload.
- `POST /data/upload/{source}?dry_run={true|false}` — multipart file upload.
  - Parses and validates every row before anything else. Any invalid row →
    `422` with `{errors: [{row_number, column, message}, ...]}` — `row_number`
    is 1-indexed over data rows only (the header is not row 1; the first
    data row is), matching what a spreadsheet's row-minus-one gridline would
    show. No DB writes, regardless of `dry_run`.
  - All rows valid, `dry_run=true` → `200` with an impact preview:
    `{rows_to_load, rows_to_delete, exceptions_to_delete, actions_to_delete,
    live_payouts_affected}` (`live_payouts_affected` counts actions with a
    non-null `gateway_payout_id` among those about to be deleted). No DB
    writes.
  - All rows valid, `dry_run=false` → re-validates (does not trust an
    earlier dry run), then performs the replace (below) in one transaction,
    returns `{rows_loaded, rows_deleted, exceptions_deleted,
    actions_deleted}`.
- Request size/row-count are checked before parsing: over
  `config.RR_UPLOAD_MAX_BYTES` → `413`; over `RR_UPLOAD_MAX_ROWS` (after
  parsing the header) → `422` with a single "too many rows" error.

### Replace + cascade

Inside one `session()` transaction:

1. **`ledger` replace:** delete `actions` where `exception_key IN (SELECT
   exception_key FROM exceptions WHERE ledger_ref IS NOT NULL)`; delete
   those `exceptions`; delete `transactions WHERE source='ledger'`.
2. **`bank_statement` replace:** same shape, scoped to `exceptions WHERE
   matched_source='bank_statement'` — `gateway`-matched exceptions are
   untouched.
3. Insert the new rows via the existing `load_transactions()`.
4. `log_audit(conn, actor='ingest', subject_type='transaction',
   subject_id=source, event='data_replaced', detail=<counts as JSON>)` —
   the Activity log tab picks this up automatically, same as every other
   pipeline stage.

### New config (`app/config.py`, following the existing `_int_env` convention)

- `RR_UPLOAD_MAX_ROWS` (default `5000`)
- `RR_UPLOAD_MAX_BYTES` (default `2_000_000`)

## Frontend

### `DataTab`

Two cards (Ledger, Bank statement), same `.card`/`.card-head`/`.card-body`
shell as every other tab. Each card:

- "Download template" link → `GET /data/templates/{source}` (plain browser
  download, no custom plumbing).
- Current row count + last-updated, from `GET /data/summary`.
- File picker + Upload button.
- Result area: validation errors (table: row / column / message) **or** the
  confirm panel **or** a success toast, depending on upload state.

### Upload flow

1. Pick file → Upload → `POST /data/upload/{source}?dry_run=true`.
2. **Invalid:** render the row-level error table inline in the card. Stop.
3. **Valid:** show `UploadConfirm` — an impact-preview panel shaped like
   `RouteConfirm` (reuse/generalize it rather than duplicate if its shape
   fits) — rows to replace, exceptions to clear, actions to clear, with the
   same visual weight `RouteConfirm` gives a real dispatch when
   `live_payouts_affected > 0`.
4. Confirm → `POST /data/upload/{source}?dry_run=false` (the file is
   re-sent; nothing about the earlier dry run is trusted or cached). Success
   → toast (matching `RunMenu`'s pattern), refresh `/data/summary`, and
   invalidate the funnel/exceptions/insights react-query keys so the rest of
   the dashboard reflects the new data without a manual reload.
5. Cancel → close the confirm panel, no server call.

Network/5xx errors use the existing `ApiError` + toast pattern
(`lib/api.ts`, `lib/queries.ts`) — nothing new there.

## Testing

- **`tests/test_ingest.py`** (new, style matches `tests/test_fixtures.py`):
  - Accepts a well-formed CSV per source.
  - Rejects: missing required column, non-numeric `amount_paise`,
    unparseable `occurred_at`, `refund`/`chargeback` row missing
    `original_ref`, duplicate `external_ref` within one file — each
    asserting the exact `{row_number, column, message}` shape.
  - Template round-trip: generate a template, append one valid row, parse it
    back, get the same row out.
- **Replace/cascade** (`tests/test_main.py` style, `TestClient` +
  `tests/conftest.py`'s per-test DB): seed transactions + exceptions +
  actions, including one action with `gateway_payout_id` set.
  - `dry_run=true` counts match exactly what the real replace will delete,
    and `live_payouts_affected` is 1.
  - `dry_run=false` on a `ledger` replace clears every `ledger_ref IS NOT
    NULL` exception but leaves a pure-orphan (`ledger_ref IS NULL`)
    exception untouched.
  - `dry_run=false` on a `bank_statement` replace clears only
    `matched_source='bank_statement'` exceptions, leaves `gateway`-matched
    ones alone.
- **All-or-nothing:** one bad row among many good ones → `transactions`
  count unchanged after the failed upload.
- **Limits:** a file over `RR_UPLOAD_MAX_ROWS`/`RR_UPLOAD_MAX_BYTES` is
  rejected before parsing, asserted via a monkeypatched low limit.
- **Frontend:** `DataTab.test.tsx` (Vitest + Testing Library, pattern from
  `RouteConfirm.test.tsx`/`RunMenu.test.tsx`) — upload success, validation-
  error rendering, confirm/cancel branches of the impact-preview panel,
  `lib/api.ts` mocked the way existing component tests do.

## Out of scope

- Uploading `gateway` data.
- `.xlsx`/spreadsheet support.
- Payout-dispatch fields in the upload template.
- Adding a `bank_statement` trigger to `RunMenu` (noted as a gap; not solved
  here).
- Any change to `app/router.py`, the payout-sync loop, or their tests.
