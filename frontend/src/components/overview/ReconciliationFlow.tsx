import { useState } from 'react'
import { IconRefresh } from '@/components/icons'
import { useFirstPaintReveal } from '@/hooks/useFirstPaintReveal'
import { flowAccountsForAll, flowSegments, flowSignature, type FlowKey } from '@/lib/flow'
import { fmtPaise, fmtPct } from '@/lib/format'
import type { Funnel } from '@/lib/types'

/* The reconciliation identity, drawn.

   /funnel guarantees matched + recovered + exceptions == ingested, and until
   now that fact reached the page as a run of 11px text at the foot of the hero
   ("66 ingested · 27 auto-matched · ..."). It is the single truest sentence
   this product can say about itself, so it gets to be the picture: three
   segments whose widths sum to the track, where seeing them fill it IS the
   proof that nothing went unaccounted for.

   Colour carries meaning rather than category. Auto-matched wears the
   de-emphasis grey because it needed no one's attention; recovered wears green
   because the agent closed it; the remainder wears the same orange the hero
   spends on its gap, because it is the same idea counted a different way. */

const HINTS: Record<FlowKey, string> = {
  matched: 'Settled against the ledger with nothing raised.',
  recovered: 'Raised, then closed — retried, disputed or confirmed.',
  exceptions: 'Still open. The recovery agent is working these.',
}

export function ReconciliationFlow({
  funnel,
  onOpenExceptions,
  onRefresh,
}: {
  funnel: Funnel | undefined
  onOpenExceptions: () => void
  onRefresh: () => void
}) {
  const [active, setActive] = useState<FlowKey | null>(null)
  const segments = flowSegments(funnel)
  const revealed = useFirstPaintReveal(flowSignature(segments))

  // Drawing a bar whose parts do not sum to the whole would assert something
  // false. If the invariant ever fails, report the counts plainly instead.
  const honest = flowAccountsForAll(funnel)

  return (
    <div className="card">
      <div className="card-head">
        <div>
          <div className="title">Where the ledger went</div>
          <div className="sub">
            Every row that came in ends in one of three states, and the three account for all
            of them. Counted in records — the figure above is counted in rupees.
          </div>
        </div>
        <button className="btn icon-btn" title="Refresh" aria-label="Refresh" onClick={onRefresh}>
          <IconRefresh />
        </button>
      </div>

      <div className="card-body">
        {segments.length === 0 || !honest ? (
          <div className="flow-plain">
            {funnel
              ? `${funnel.ingested} ingested · ${funnel.matched} auto-matched · ${funnel.recovered} recovered · ${funnel.exceptions} need attention`
              : 'No ledger activity yet — open Run and start with Reconcile.'}
          </div>
        ) : (
          <>
            <div className="flow-total">
              <span className="flow-total-value">{funnel?.ingested}</span>
              <span className="flow-total-label">rows ingested</span>
            </div>

            {/* Revealed with a clip-path wipe rather than by animating the
                segment widths: the geometry stays exact throughout, so the
                1px seams between segments never stretch, and clip-path runs
                on the compositor instead of forcing layout every frame. */}
            <div
              className={`flow-track${revealed ? ' shown' : ''}`}
              onMouseLeave={() => setActive(null)}
            >
              {segments.map((s) => (
                <div
                  key={s.key}
                  className="flow-seg"
                  data-seg={s.key}
                  data-dim={active !== null && active !== s.key ? '' : undefined}
                  style={{ width: `${s.pct}%` }}
                  onMouseEnter={() => setActive(s.key)}
                />
              ))}
            </div>

            <div className="flow-legend">
              {segments.map((s) => {
                const isAction = s.key === 'exceptions'
                const Tag = isAction ? 'button' : 'div'
                return (
                  <Tag
                    key={s.key}
                    className={`flow-item${isAction ? ' clickable' : ''}`}
                    data-seg={s.key}
                    data-dim={active !== null && active !== s.key ? '' : undefined}
                    title={HINTS[s.key]}
                    onMouseEnter={() => setActive(s.key)}
                    onMouseLeave={() => setActive(null)}
                    {...(isAction
                      ? { onClick: onOpenExceptions, type: 'button' as const }
                      : {})}
                  >
                    <span className="flow-swatch" />
                    <span className="flow-count">{s.count}</span>
                    <span className="flow-label">{s.label}</span>
                    <span className="flow-pct">{s.pct}%</span>
                  </Tag>
                )
              })}
            </div>
          </>
        )}

        {/* Deliberately outside the bar. These rows have no ledger row at all,
            so they are not part of the identity above -- folding them in as a
            fourth segment would stop the widths summing to the whole. */}
        {funnel && funnel.gateway_side_anomalies > 0 && (
          <p className="flow-aside">
            <strong>{funnel.gateway_side_anomalies}</strong> gateway-side records sit outside
            this total — they have no ledger row to account for.
          </p>
        )}

        <div className="flow-footer">
          <div className="flow-stat">
            <span className="flow-stat-label">Match rate</span>
            <span className="flow-stat-value">{fmtPct(funnel?.match_rate)}</span>
          </div>
          <div className="flow-stat">
            <span className="flow-stat-label">Amount resolved</span>
            <span className="flow-stat-value">
              {funnel ? fmtPaise(funnel.amount_recovered_paise) : '–'}
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}
