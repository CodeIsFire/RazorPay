import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RailNav } from '@/components/nav/RailNav'
import { ToastProvider } from '@/components/Toast'

/* The reset button throws away whatever the user uploaded, so the tests that
   matter are the ones about the gap before it fires: it must confirm, and
   cancelling must call nothing. */

let fetchMock: ReturnType<typeof vi.fn>

function mount(resetStatus = 200) {
  fetchMock = vi.fn(async (url: string) => {
    if (String(url).includes('/demo/reset')) {
      return new Response(
        JSON.stringify(
          resetStatus === 200
            ? { ledger: 66, gateway: 67, bank_statement: 68, exceptions: 60 }
            : { detail: 'boom' },
        ),
        { status: resetStatus },
      )
    }
    return new Response(JSON.stringify({}), { status: 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <RailNav activeTab="overview" onSelect={() => {}} />
      </ToastProvider>
    </QueryClientProvider>,
  )
}

const resetCalls = () =>
  fetchMock.mock.calls.filter(([url]) => String(url).includes('/demo/reset'))

afterEach(() => vi.unstubAllGlobals())

describe('the reload-demo-data control', () => {
  it('sits in the rail as its own button', () => {
    mount()
    expect(screen.getByRole('button', { name: /reload demo data/i })).toBeInTheDocument()
  })

  it('asks before throwing away whatever is loaded', async () => {
    const user = userEvent.setup()
    mount()
    await user.click(screen.getByRole('button', { name: /reload demo data/i }))

    expect(await screen.findByRole('alertdialog')).toBeInTheDocument()
    expect(resetCalls()).toHaveLength(0)
  })

  it('reloads only once the confirmation is accepted', async () => {
    const user = userEvent.setup()
    mount()
    await user.click(screen.getByRole('button', { name: /reload demo data/i }))
    const dialog = await screen.findByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: /reload/i }))

    await waitFor(() => expect(resetCalls()).toHaveLength(1))
    expect(resetCalls()[0][1]?.method).toBe('POST')
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(/66 ledger rows/i),
    )
  })

  it('calls nothing when the confirmation is cancelled', async () => {
    const user = userEvent.setup()
    mount()
    await user.click(screen.getByRole('button', { name: /reload demo data/i }))
    await user.click(await screen.findByRole('button', { name: /cancel/i }))

    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(resetCalls()).toHaveLength(0)
  })

  it('reports a failed reload as a failure', async () => {
    const user = userEvent.setup()
    mount(500)
    await user.click(screen.getByRole('button', { name: /reload demo data/i }))
    const dialog = await screen.findByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: /reload/i }))

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/boom/i))
  })
})

describe('the rail still navigates', () => {
  it('keeps one tab stop for the selected tab and none for the rest', () => {
    mount()
    const overview = screen.getByRole('tab', { name: /overview/i })
    expect(overview).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: /insights/i })).toHaveAttribute('tabindex', '-1')
  })
})
