import { fmtPaise, fmtTs, formatDetail } from '@/lib/format'
import { useExceptionDetail } from '@/lib/queries'
import type { ExceptionRecord, Transaction } from '@/lib/types'

/* Two ledgers that should agree, face to face. When one side is empty that
   absence IS the finding, so it gets named rather than left blank. */

function TxnTable({ rows, flagged }: { rows: Transaction[]; flagged?: Set<string> }) {
  return (
    <table className="conflict-table">
      <thead>
        <tr>
          <th>Ref</th>
          <th>Amount</th>
          <th>Occurred</th>
          <th>Narration</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((t) => (
          <tr key={t.id}>
            <td className="mono">
              {t.external_ref}
              {flagged?.has(t.external_ref) && <span className="conflict-flag">flagged</span>}
            </td>
            <td className="amount">{fmtPaise(t.amount_paise)}</td>
            <td className="mono">{fmtTs(t.occurred_at)}</td>
            <td>{t.narration || t.counterparty || ''}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Side({
  kind,
  rows,
  sourceWord,
  flagged,
}: {
  kind: 'ledger' | 'actual'
  rows: Transaction[]
  sourceWord: string
  flagged?: Set<string>
}) {
  if (rows.length) return <TxnTable rows={rows} flagged={flagged} />
  return (
    <div className="conflict-empty">
      {kind === 'ledger'
        ? `No ledger entry for this — it appears only on the ${sourceWord} side.`
        : `No matching transaction on the ${sourceWord} side. That absence is the finding.`}
    </div>
  )
}

export function ConflictDetail({ exception }: { exception: ExceptionRecord }) {
  // staleTime: Infinity on this query is what makes re-expanding a row free.
  const { data, isPending, error } = useExceptionDetail(exception.exception_key)

  if (isPending) return <div className="conflict-loading">Loading transaction detail…</div>
  if (error)
    return (
      <div className="conflict-error">Couldn't load transaction detail: {String(error.message)}</div>
    )

  const led = data.ledger_transactions ?? []
  const act = data.actual_transactions ?? []
  const sourceWord = exception.matched_source === 'bank_statement' ? 'bank statement' : 'gateway'
  // A batch row's gateway_ref is a comma-joined list, so every ref in it is
  // one of the transactions this exception is actually about.
  const gatewayRefs = new Set((exception.gateway_ref || '').split(',').filter(Boolean))

  const sum = (rows: Transaction[]) => rows.reduce((a, t) => a + (t.amount_paise || 0), 0)
  // Only meaningful when both sides exist -- against an empty side the
  // "difference" is just the other side's total restated.
  const delta = led.length && act.length ? sum(led) - sum(act) : 0

  return (
    <>
      <div className="conflict-summary">{formatDetail(exception.detail)}</div>
      <div className="conflict-sections">
        <div className="conflict-section ledger">
          <div className="heading">Ledger says</div>
          <Side kind="ledger" rows={led} sourceWord={sourceWord} />
        </div>
        <div className="conflict-section actual">
          <div className="heading">
            {sourceWord === 'bank statement' ? 'Bank statement says' : 'Gateway says'}
          </div>
          <Side kind="actual" rows={act} sourceWord={sourceWord} flagged={gatewayRefs} />
          {delta !== 0 && (
            <span className="conflict-delta">
              {delta > 0 ? 'Short by' : 'Over by'} {fmtPaise(Math.abs(delta))}
            </span>
          )}
        </div>
      </div>
    </>
  )
}
