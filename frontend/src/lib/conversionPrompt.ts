/* The prompt shown on the Data tab, for converting someone's own export into
   a CSV this app will accept.

   IT ENCODES app/ingest.py's VALIDATION RULES. The column lists, the paise
   requirement, the accepted date formats and the reversal rule are all
   restatements of what parse_csv() enforces -- so a change to the schema
   table or the validators there needs a matching edit here, or this prompt
   starts producing files the app rejects. app/ingest.py carries a pointer
   back to this file for that reason.

   Generating it from the backend was considered and rejected: it is one
   string, and an endpoint plus a fetch plus a loading state to keep two
   things in step that change perhaps twice a year is more machinery than the
   drift it prevents. PromptTerminal.test.tsx pins the load-bearing lines. */
export const CONVERSION_PROMPT = `You are converting my raw financial data into a CSV for a reconciliation tool.
Output ONLY the CSV — no prose, no explanation, no markdown fences.

WHICH FILE: <ledger | bank_statement>       ← set this
MY RAW DATA: <paste export, or attach the file>

═══ THE TWO FILE TYPES ═══

ledger = what I EXPECTED to pay (from my accounting system / payout register).
  Header, exactly:
  external_ref,amount_paise,occurred_at,counterparty,narration,fund_account_type,fund_account_name,fund_account_ifsc,fund_account_number,fund_account_vpa,contact_type,contact_email,contact_mobile,payout_purpose,payout_mode

  The ten payout columns are OPTIONAL. Leave them all empty and the row is
  reconciled but never paid. Fill them and the row becomes dispatchable --
  see PAYOUT INSTRUCTION below.

bank_statement = what ACTUALLY left the account (from my bank export).
  Header, exactly:
  external_ref,amount_paise,occurred_at,counterparty,narration,reference_id,transaction_type,original_ref

═══ COLUMNS ═══

external_ref   REQUIRED. Unique within the file — no duplicates, ever.
               ledger: my own order/payout id (e.g. ORD-1001).
               bank_statement: the BANK's transaction id / UTR.
amount_paise   REQUIRED. Integer PAISE = rupees × 100. Positive, no decimals,
               no commas, no currency symbol. ₹1,500.00 → 150000. This is the
               single most common mistake — "1500.00" is rejected outright.
               Use the absolute value; do not write negatives for debits.
occurred_at    REQUIRED. ISO 8601 only:
                 2026-09-01T10:00:00+00:00   ✓ preferred
                 2026-09-01T10:00:00Z        ✓
                 2026-09-01 10:00:00         ✓ (read as UTC)
                 2026-09-01                  ✓ (read as 00:00 UTC)
                 01/09/2026, 1-Sep-26, 09/01/2026   ✗ REJECTED
               If my data is DD/MM/YYYY, convert it — and if a date is
               genuinely ambiguous, stop and ask me rather than guessing.
counterparty   optional. Vendor/payee name.
narration      optional. Description or bank narration text.

bank_statement only:
reference_id   optional but IMPORTANT. This is the correlation key: put the
               LEDGER's external_ref here (not the bank's id) whenever the
               statement row references it. Blank forces fuzzy matching on
               amount + time window, which is far less reliable. If the bank
               narration embeds my order id, extract it into this column.
transaction_type  optional. One of: payment | refund | chargeback.
               Blank = payment. Money leaving = payment. Money coming back =
               refund (my reversal) or chargeback (forced by the bank).
original_ref   REQUIRED when transaction_type is refund or chargeback: the
               external_ref of the payment being reversed. Blank for payments.

═══ PAYOUT INSTRUCTION (ledger only, optional) ═══

Fill these ONLY if I want the row to be dispatchable. All-or-nothing per row:
either leave every one blank, or give a complete instruction of ONE shape.

fund_account_type  bank_account | vpa   — required if any other column here is set
  bank_account  ->  fund_account_ifsc AND fund_account_number required,
                    fund_account_vpa MUST be empty
  vpa           ->  fund_account_vpa required,
                    fund_account_ifsc and fund_account_number MUST be empty
  The two shapes are mutually exclusive. A row carrying both is rejected.
fund_account_name  the payee's name as their bank holds it.
contact_type       vendor | customer | employee | self
contact_email      contact_mobile     optional.
payout_purpose     refund | cashback | payout | salary | utility bill | vendor bill
payout_mode        NEFT | RTGS | IMPS | UPI | card
                   A vpa fund account settles over UPI; pair them.

Never invent an account number, IFSC or VPA. If I have not given you real
ones, leave all ten blank and tell me the rows are reconcile-only.

═══ HARD RULES ═══

1. First line is the header, spelled exactly as above, in that order.
2. One row per transaction. Do not invent, merge, split, or estimate rows.
3. All-or-nothing: one bad row rejects the entire file. Be strict.
4. Leave optional cells EMPTY (nothing between the commas) when unknown —
   never "N/A", "null", "-", or "0".
5. Quote any field containing a comma; strip newlines out of narrations.
6. Do NOT add columns beyond the header above -- extra columns are ignored.
7. Max 5000 rows, max 2 MB, UTF-8, CSV only (not .xlsx).
8. If my data is missing something REQUIRED, or a value is ambiguous, stop
   and ask me. Do not fill a gap with a plausible-looking value.

Before the CSV, do nothing. After it, do nothing. Just the CSV.
`
