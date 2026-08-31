import NumberFlow from '@number-flow/react'
import { useReducedMotion } from 'motion/react'
import { useState } from 'react'
import {
  OutstandingSparkline,
  type SparkPoint,
} from '@/components/overview/OutstandingSparkline'
import { useFirstPaintReveal } from '@/hooks/useFirstPaintReveal'
import { fmtDay, fmtPaise } from '@/lib/format'
import { gapBarKey, gapBarPercents, gapTotals } from '@/lib/gap'
import type { DailyRow, Funnel } from '@/lib/types'


/* Rupees, animated only where the digits actually differ.

   @number-flow has sat in package.json unused since the migration; this is the
   one place in the product where a figure changes under the reader's cursor, so
   it is where a transition earns its keep -- scrubbing the sparkline should feel
   like moving along one number, not like four separate numbers flashing.

   It is given the pre-formatted string's own parts via `format`, so en-IN
   lakh/crore grouping survives: NumberFlow's default grouping would render
   ₹5,18,638 as ₹518,638. */
function AnimatedAmount({ paise }: { paise: number }) {
  const reduced = useReducedMotion()
  if (reduced) return <>{fmtPaise(paise)}</>
  return (
    <NumberFlow
      value={paise / 100}
      /* Matched to fmtPaise exactly. These were maximumFractionDigits: 0
         while fmtPaise uses 2, so the reduced-motion path and the animated
         path rendered different amounts for the same figure -- ₹40,531 versus
         ₹40,530.90 -- in the most prominent number in the product. INR's
         currency default is 2 minimum digits, so minimum has to be pinned to
         0 as well or whole rupees gain a ".00" fmtPaise never shows. */
      format={{
        style: 'currency',
        currency: 'INR',
        minimumFractionDigits: 0,
        maximumFractionDigits: 2,
      }}
      locales="en-IN"
      // Digits only; the rupee sign and separators must not slide around.
      transformTiming={{ duration: 420, easing: 'cubic-bezier(.22,.61,.36,1)' }}
    />
  )
}

/* The green fill sweeps out from zero on first paint, then the orange gap fades
   in where it stops short (both transitions live in the stylesheet). The reveal
   mechanics -- mount at zero, skip when the tab is not compositing, never
   replay on an unchanged poll -- now live in useFirstPaintReveal, which the
   reconciliation flow needs too. `gapBarKey` supplies this bar's signature:
   the widths rounded to the precision they are actually written at. */
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
  const revealed = useFirstPaintReveal(gapBarKey(settledPct, gapPct))

  /* The funnel counts used to trail the hero as a run of small text
     ("66 ingested · 27 auto-matched · ..."). They now have their own card
     below, where the same numbers are drawn as segments that visibly sum to
     the whole. Repeating them here would say the same thing twice and dilute
     the one figure this card exists to report. */

  /* Scrubbing the sparkline retargets the headline figure to that day. The
     hovered day is transient state only -- `gap` stays the card's real subject,
     so leaving the chart always returns to today's total rather than stranding
     the reader on whatever they last pointed at. */
  const [scrubbed, setScrubbed] = useState<SparkPoint | null>(null)
  const shownPaise = scrubbed ? scrubbed.paise : gap

  const note = !hasActivity
    ? 'No ledger activity yet — open Run and start with Reconcile.'
    : scrubbed
      ? `Outstanding on ${fmtDay(scrubbed.day)}`
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
      <div className="gap-hero-top">
        <div>
          <div className="eyebrow">Unreconciled on the ledger</div>
          {/* The figure keeps its exact text -- fmtPaise, en-IN grouping, the
              rupee sign -- and only the digits that change are animated. Under
              reduced motion it renders that same string with no motion at all,
              so the number is never withheld for the sake of an effect. */}
          <div className="figure mono">
            {hasActivity ? <AnimatedAmount paise={shownPaise} /> : '–'}
          </div>
          <div className="note">{note}</div>
        </div>
        {/* Fills the dead space beside the figure with the one thing the card
            could not otherwise say: which way this number has been moving. */}
        <OutstandingSparkline days={days} onHover={setScrubbed} />
      </div>
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

    </button>
  )
}
