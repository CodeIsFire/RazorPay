import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuditTab } from '@/components/audit/AuditTab'
import { ExceptionsTab } from '@/components/exceptions/ExceptionsTab'
import { ToastProvider } from '@/components/Toast'

/* The bug these guard: every tab rendered `data ?? []`, so a failed request
   produced the same screen as a genuinely clean backlog. On a tool that
   dispatches payouts, "Nothing needs attention" when the truth is unknown is
   a correctness problem, and the audit tab's empty state went further -- it
   told the operator to go and run the pipeline. */

function mountFailing(ui: React.ReactElement) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify({ detail: 'database is locked' }), { status: 500 })),
  )
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>{ui}</ToastProvider>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('a failed read is never shown as an empty result', () => {
  it('exceptions: does not claim nothing needs attention', async () => {
    mountFailing(<ExceptionsTab query="" />)
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.queryByText(/nothing needs attention/i)).not.toBeInTheDocument()
  })

  it('exceptions: names what failed and keeps the server’s reason', async () => {
    mountFailing(<ExceptionsTab query="" />)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/couldn’t load the exception list/i)
    expect(alert).toHaveTextContent(/database is locked/i)
  })

  it('exceptions: offers a retry', async () => {
    mountFailing(<ExceptionsTab query="" />)
    const alert = await screen.findByRole('alert')
    expect(within(alert).getByRole('button', { name: /try again/i })).toBeInTheDocument()
  })

  it('audit: does not invite re-running a payout pipeline', async () => {
    // The worst copy in the app to show on a failed READ.
    mountFailing(<AuditTab query="" />)
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.queryByText(/open Run and start with Reconcile/i)).not.toBeInTheDocument()
  })

  it('says the figures are unknown rather than zero', async () => {
    mountFailing(<ExceptionsTab query="" />)
    expect(await screen.findByRole('alert')).toHaveTextContent(/not zero/i)
  })

  it('does not report a failed refresh as a success', async () => {
    // Seen on screen: the panel read "Couldn't load the activity log" while a
    // toast underneath it said "Activity log refreshed." refetch() resolves
    // with an error result instead of throwing, so the toast fired anyway.
    const user = userEvent.setup()
    mountFailing(<AuditTab query="" />)
    await screen.findByRole('alert')

    await user.click(screen.getByRole('button', { name: 'Refresh' }))
    expect(await screen.findByText(/couldn’t refresh the activity log/i)).toBeInTheDocument()
    expect(screen.queryByText(/activity log refreshed/i)).not.toBeInTheDocument()
  })
})
