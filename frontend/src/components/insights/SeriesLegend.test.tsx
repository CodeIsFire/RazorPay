import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SeriesLegend, type SeriesLegendItem } from './SeriesLegend'

const ITEMS: SeriesLegendItem[] = [
  { label: 'Still outstanding', color: 'var(--chart-bar)', seriesIndex: 1 },
  { label: 'Reconciled', color: 'var(--chart-context)', seriesIndex: 0 },
]

function setup(props: Partial<Parameters<typeof SeriesLegend>[0]> = {}) {
  const onActivate = vi.fn()
  const onToggleSticky = vi.fn()
  render(
    <SeriesLegend
      items={ITEMS}
      active={null}
      sticky={null}
      onActivate={onActivate}
      onToggleSticky={onToggleSticky}
      {...props}
    />,
  )
  return { onActivate, onToggleSticky }
}

describe('SeriesLegend', () => {
  it('activates the series ordinal, not the position in the legend', () => {
    // "Still outstanding" reads first but is the second <Bar>. Sending the
    // reading order here would fade the wrong half of every column.
    const { onActivate } = setup()
    fireEvent.mouseEnter(screen.getByRole('button', { name: 'Still outstanding' }))
    expect(onActivate).toHaveBeenCalledWith(1)
  })

  it('clears the activation when the pointer leaves', () => {
    const { onActivate } = setup()
    fireEvent.mouseLeave(screen.getByRole('button', { name: 'Reconciled' }))
    expect(onActivate).toHaveBeenCalledWith(null)
  })

  it('ignores focus, which has no guaranteed blur to undo it', () => {
    // Regression: driving the transient highlight from focus left the chart
    // dimmed for good whenever a button was focused without a later blur --
    // a restored page or a stray click was enough.
    const { onActivate } = setup()
    fireEvent.focus(screen.getByRole('button', { name: 'Reconciled' }))
    expect(onActivate).not.toHaveBeenCalled()
  })

  it('toggles a sticky selection on click, the path keyboard and touch both use', () => {
    const { onToggleSticky } = setup()
    fireEvent.click(screen.getByRole('button', { name: 'Still outstanding' }))
    expect(onToggleSticky).toHaveBeenCalledWith(1)
  })

  it('reports only the stuck entry as pressed', () => {
    // A transient hover is not state and must not be announced as though it
    // were -- only a click leaves something a screen reader should report.
    setup({ sticky: 1, active: 1 })
    expect(screen.getByRole('button', { name: 'Still outstanding' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.getByRole('button', { name: 'Reconciled' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
  })

  it('dims the swatch of the series being faded, and only that one', () => {
    const { container } = render(
      <SeriesLegend
        items={ITEMS}
        active={1}
        sticky={null}
        onActivate={vi.fn()}
        onToggleSticky={vi.fn()}
      />,
    )
    const [outstanding, reconciled] = [...container.querySelectorAll<HTMLElement>('.swatch')]
    expect(outstanding.style.opacity).toBe('1')
    expect(reconciled.style.opacity).toBe('0.35')
  })

  it('leaves every swatch lit when nothing is active', () => {
    const { container } = render(
      <SeriesLegend
        items={ITEMS}
        active={null}
        sticky={null}
        onActivate={vi.fn()}
        onToggleSticky={vi.fn()}
      />,
    )
    for (const s of container.querySelectorAll<HTMLElement>('.swatch')) {
      expect(s.style.opacity).toBe('1')
    }
  })
})
