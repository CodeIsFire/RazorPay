import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ExceptionsTable } from '@/components/exceptions/ExceptionsTable'
import { ToastProvider } from '@/components/Toast'
import type { ExceptionRecord } from '@/lib/types'

/* Conflict detail used to be unreachable without a mouse: the only way to open
   a row was an onClick on the <tr>, which no key can activate. */

const row = (over: Partial<ExceptionRecord> = {}): ExceptionRecord => ({
  id: 1,
  exception_key: 'EX-1',
  cause: 'failed_payment',
  ledger_ref: 'LED-001',
  gateway_ref: null,
  matched_source: 'gateway',
  amount_paise: 100000,
  detail: 'payout failed',
  status: 'open',
  retry_count: 0,
  created_at: '2026-08-20 10:00:00',
  updated_at: '2026-08-20 10:00:00',
  ...over,
})

function mount(expandedKey: string | null, onToggleExpand = vi.fn()) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({}), { status: 200 })))
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <ExceptionsTable
          rows={[row()]}
          expandedKey={expandedKey}
          busyKey={null}
          emptyMessage="nothing"
          onToggleExpand={onToggleExpand}
          onResolve={vi.fn()}
          onRecheck={vi.fn()}
        />
      </ToastProvider>
    </QueryClientProvider>,
  )
  return onToggleExpand
}

afterEach(() => vi.unstubAllGlobals())

describe('opening conflict detail', () => {
  it('exposes a real control, not just a clickable row', () => {
    mount(null)
    expect(screen.getByRole('button', { name: /payout failed/i })).toBeInTheDocument()
  })

  it('can be opened from the keyboard alone', async () => {
    const user = userEvent.setup()
    const onToggle = mount(null)

    await user.tab()
    expect(screen.getByRole('button', { name: /payout failed/i })).toHaveFocus()

    await user.keyboard('{Enter}')
    expect(onToggle).toHaveBeenCalledWith('EX-1')
  })

  it('reports collapsed and expanded state', () => {
    mount(null)
    expect(screen.getByRole('button', { name: /payout failed/i })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
  })

  it('points at the detail it controls once open', () => {
    mount('EX-1')
    const toggle = screen.getByRole('button', { name: /payout failed/i })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    // The target must actually exist, or the reference is a dead end.
    const id = toggle.getAttribute('aria-controls')!
    expect(document.getElementById(id)).toBeTruthy()
  })

  it('toggles once per activation, not twice', async () => {
    // The row is still clickable for the mouse, so the button has to stop the
    // event bubbling -- otherwise activating it opens and immediately closes.
    const user = userEvent.setup()
    const onToggle = mount(null)
    await user.click(screen.getByRole('button', { name: /payout failed/i }))
    expect(onToggle).toHaveBeenCalledTimes(1)
  })
})
