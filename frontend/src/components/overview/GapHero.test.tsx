import { render, screen, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { GapHero } from './GapHero'
import type { DailyRow, Funnel } from '@/lib/types'

const funnel: Funnel = {
  ingested: 66,
  matched: 27,
  exceptions: 19,
  recovered: 20,
  match_rate: 0.4091,
  amount_recovered_paise: 62377100,
  gateway_side_anomalies: 16,
}

const days: DailyRow[] = [
  { day: '2026-08-20', total_paise: 1000, reconciled_paise: 600, outstanding_paise: 400, count: 2 },
]

const settledSeg = (c: HTMLElement) => c.querySelector<HTMLElement>('.seg-settled')!
const gapBar = (c: HTMLElement) => c.querySelector<HTMLElement>('.gap-bar')!

afterEach(() => vi.unstubAllGlobals())

describe('GapHero', () => {
  it('sweeps the settled fill out from zero on first paint', async () => {
    // React applies the final width at mount, so nothing would transition
    // without an explicit start-at-zero. This is the regression test for that:
    // the segment must be 0% before it is 60%.
    const { container } = render(
      <GapHero funnel={funnel} days={days} onOpenExceptions={() => {}} />,
    )
    expect(settledSeg(container).style.width).toBe('0%')
    expect(gapBar(container).className).not.toContain('shown')

    await waitFor(() => expect(settledSeg(container).style.width).toBe('60%'))
    expect(gapBar(container).className).toContain('shown')
  })

  it('still sweeps under StrictMode, whose double-invoke cancels the first schedule', async () => {
    // main.tsx renders the app inside <StrictMode>, which runs every effect
    // twice: mount, cleanup, mount. The cleanup cancels the rAF chain and the
    // timeout the first pass scheduled -- and the `seen` guard, already set to
    // this key by that pass, makes the second pass return before it can
    // reschedule. The reveal then never fires and the hero renders a
    // permanently empty track. Guarding on the key must not outlive the work
    // it was meant to dedupe.
    const { container } = render(
      <StrictMode>
        <GapHero funnel={funnel} days={days} onOpenExceptions={() => {}} />
      </StrictMode>,
    )
    await waitFor(() => expect(settledSeg(container).style.width).toBe('60%'))
    expect(gapBar(container).className).toContain('shown')
  })

  it('skips the sweep when the tab is not being composited', async () => {
    // A grow-from-zero started in a hidden tab can sit frozen at 0, because
    // neither CSS transitions nor rAF advance there -- the hero would read as
    // a broken empty bar. Hidden tabs go straight to the final state.
    vi.stubGlobal('document', document)
    const spy = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')

    const { container } = render(
      <GapHero funnel={funnel} days={days} onOpenExceptions={() => {}} />,
    )
    await waitFor(() => expect(settledSeg(container).style.width).toBe('60%'))
    expect(gapBar(container).className).toContain('shown')
    spy.mockRestore()
  })

  it('leaves the bar alone when a poll returns the same figures', async () => {
    const { container, rerender } = render(
      <GapHero funnel={funnel} days={days} onOpenExceptions={() => {}} />,
    )
    await waitFor(() => expect(settledSeg(container).style.width).toBe('60%'))

    // Same numbers arriving as a fresh array, which is what the 15s poll
    // produces. The bar must not blink back to empty.
    rerender(<GapHero funnel={{ ...funnel }} days={[{ ...days[0] }]} onOpenExceptions={() => {}} />)
    expect(settledSeg(container).style.width).toBe('60%')
    expect(gapBar(container).className).toContain('shown')
  })

  it('names the shortfall and the records still being worked', () => {
    render(<GapHero funnel={funnel} days={days} onOpenExceptions={() => {}} />)
    expect(screen.getByText('₹4')).toBeInTheDocument()
    expect(screen.getByText('19 records the recovery agent is still working.')).toBeInTheDocument()
  })

  it('invites a first run instead of showing a zero when nothing is ingested', () => {
    render(<GapHero funnel={funnel} days={[]} onOpenExceptions={() => {}} />)
    // The figure and both bar amounts all read as an em dash with no ledger.
    expect(screen.getAllByText('–')).toHaveLength(3)
    expect(
      screen.getByText('No ledger activity yet — open Run and start with Reconcile.'),
    ).toBeInTheDocument()
  })
})
