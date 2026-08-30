import { useEffect, useRef, useState } from 'react'
import { fmtPaise } from '@/lib/format'
import { gapBarKey, gapBarPercents, gapTotals } from '@/lib/gap'
import type { DailyRow, Funnel } from '@/lib/types'

/* The green fill sweeps out from zero on first paint, then the orange gap
   fades in where it stops short (both transitions live in the stylesheet).

   In React the reveal has to be explicit. The vanilla version rendered the
   segment into static HTML at width 0 and let a later JS write trigger the
   CSS transition -- its own first-paint branch was in fact unreachable
   (`gapRendered = key` was assigned just above the `if (gapRendered !== null)`
   guard), and the sweep came from that HTML-then-JS ordering instead. React
   mounts the element with its final width already applied, so nothing would
   transition at all. Hence: mount at zero, then write the real width.

   Two guards on that, both load-bearing. A grow-from-zero started while the
   tab is hidden can sit frozen at 0, because neither CSS transitions nor
   rAF advance in a tab that isn't compositing -- so a hidden tab skips
   straight to the final state. And the timeout backs up rAF for the same
   reason; whichever lands first wins and the other is a no-op. Without them
   the hero reads as a broken empty bar rather than an un-animated one. */
function useRevealedWidths(settledPct: number, gapPct: number) {
  const [revealed, setRevealed] = useState(false)
  // Skips the reveal when the numbers merely repeat: the 15s poll must not
  // make the bar restart every tick.
  const seen = useRef<string | null>(null)
  const key = gapBarKey(settledPct, gapPct)

  useEffect(() => {
    if (seen.current === key) return
    const first = seen.current === null
    seen.current = key

    if (!first) return
    if (document.visibilityState !== 'visible') {
      setRevealed(true)
      return
    }
    let raf1 = 0
    let raf2 = 0
    const settle = () => setRevealed(true)
    raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(settle)
    })
    const timer = setTimeout(settle, 120)
    return () => {
      cancelAnimationFrame(raf1)
      cancelAnimationFrame(raf2)
      clearTimeout(timer)
    }
  }, [key])

  return revealed
}

export function GapHero({
  funnel,
  days,
  onOpenExceptions,
}: {
  funnel: Funnel | undefined
  days: DailyRow[]
  onOpenExceptions: () => void
}) {
  const { expected, settled, gap } = gapTotals(days)
  const hasActivity = expected > 0
  const { settledPct, gapPct } = gapBarPercents(expected, settled, gap)
  const revealed = useRevealedWidths(settledPct, gapPct)

  const funnelBits: [number, string][] = funnel
    ? [
        [funnel.ingested, 'ingested'],
        [funnel.matched, 'auto-matched'],
        [funnel.exceptions, 'need attention'],
        [funnel.recovered, 'resolved'],
        [funnel.gateway_side_anomalies, 'gateway-only'],
      ]
    : []

  const note = !hasActivity
    ? 'No ledger activity yet — open Run and start with Reconcile.'
    : gap <= 0
      ? 'Every recorded payout has settled against the ledger.'
      : `${funnel?.exceptions ?? 0} record${funnel?.exceptions === 1 ? '' : 's'} the recovery agent is still working.`

  // No aria-label on the button below. One would REPLACE its contents as the
  // accessible name, and those contents are the headline figure this whole
  // page exists to report -- labelling the button silenced the single most
  // important number in the product. aria-describedby is additive instead.
  return (
    <button
      className="card gap-hero clickable"
      aria-describedby="gap-hero-action"
      onClick={onOpenExceptions}
    >
      <div className="eyebrow">Unreconciled on the ledger</div>
      <div className="figure mono">{hasActivity ? fmtPaise(gap) : '–'}</div>
      <div className="note">{note}</div>
      <span className="sr-only" id="gap-hero-action">
        Opens the records that need attention
      </span>

      <div className={`gap-bar${revealed ? ' shown' : ''}`}>
        <div className="row">
          <span className="lbl">Expected</span>
          <span className="amt">{hasActivity ? fmtPaise(expected) : '–'}</span>
          <div className="track">
            <div className="seg-expected" />
          </div>
        </div>
        <div className="row">
          <span className="lbl">Settled</span>
          <span className="amt">{hasActivity ? fmtPaise(settled) : '–'}</span>
          <div className="track">
            <div
              className="seg-settled"
              style={{ width: revealed ? `${settledPct.toFixed(2)}%` : '0%' }}
            />
            <div className="seg-gap" style={{ width: `${gapPct.toFixed(2)}%` }} />
          </div>
        </div>
      </div>

      <div className="funnel">
        {funnelBits.map(([n, word], i) => (
          <span key={word}>
            {i > 0 && ' · '}
            <b>{n}</b> {word}
          </span>
        ))}
      </div>
    </button>
  )
}
