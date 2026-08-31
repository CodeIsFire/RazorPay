import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RunMenu } from '@/components/RunMenu'
import { ToastProvider } from '@/components/Toast'

/* Route dispatches real payouts and nothing here can undo one. These tests
   exist to keep it behind an explicit confirmation carrying a real count --
   the previous behaviour fired it straight from a menu click and told the
   operator afterwards. */

const PREVIEW = {
  would_dispatch: 3,
  would_skip: 2,
  would_abandon: 1,
  would_error: 0,
  value_paise: 4_250_00,
}

let fetchMock: ReturnType<typeof vi.fn>

function mount(preview: unknown = PREVIEW, previewStatus = 200) {
  fetchMock = vi.fn(async (url: string) => {
    const u = String(url)
    if (u.includes('/pipeline/route/preview')) {
      return new Response(JSON.stringify(preview), { status: previewStatus })
    }
    if (u.includes('/pipeline/route')) {
      return new Response(JSON.stringify({ dispatched: 3, skipped: 2, abandoned: 1, error: 0 }), {
        status: 200,
      })
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
        <RunMenu />
      </ToastProvider>
    </QueryClientProvider>,
  )
}

const dispatchCalls = () =>
  fetchMock.mock.calls.filter(
    ([url]) => String(url).includes('/pipeline/route') && !String(url).includes('preview'),
  )

async function openRoute(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: /run/i }))
  await user.click(screen.getByRole('menuitem', { name: /run route/i }))
}

afterEach(() => vi.unstubAllGlobals())

describe('Run → Route', () => {
  it('dispatches nothing until the confirmation is accepted', async () => {
    const user = userEvent.setup()
    mount()
    await openRoute(user)

    expect(await screen.findByRole('alertdialog')).toBeInTheDocument()
    expect(dispatchCalls()).toHaveLength(0)
  })

  it('shows the real dispatch count and value from the preview', async () => {
    const user = userEvent.setup()
    mount()
    await openRoute(user)

    const dialog = await screen.findByRole('alertdialog')
    await waitFor(() => expect(dialog).toHaveTextContent(/3\s*payouts/i))
    expect(dialog).toHaveTextContent(/₹4,250/)
    // The held and abandoned counts matter too -- they explain why the number
    // is smaller than the visible backlog.
    expect(dialog).toHaveTextContent(/2.*held/i)
    expect(dialog).toHaveTextContent(/1.*abandoned/i)
  })

  it('dispatches once the confirm button is pressed', async () => {
    const user = userEvent.setup()
    mount()
    await openRoute(user)
    await screen.findByRole('alertdialog')

    await user.click(await screen.findByRole('button', { name: /dispatch 3 payouts/i }))
    await waitFor(() => expect(dispatchCalls()).toHaveLength(1))
  })

  it('dispatches nothing when cancelled', async () => {
    const user = userEvent.setup()
    mount()
    await openRoute(user)
    await screen.findByRole('alertdialog')

    await user.click(screen.getByRole('button', { name: /cancel/i }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(dispatchCalls()).toHaveLength(0)
  })

  it('dispatches nothing when dismissed with Escape', async () => {
    const user = userEvent.setup()
    mount()
    await openRoute(user)
    await screen.findByRole('alertdialog')

    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(dispatchCalls()).toHaveLength(0)
  })

  it('refuses to dispatch when the preview could not be fetched', async () => {
    // Never offer to move money against an unknown backlog.
    const user = userEvent.setup()
    mount({ detail: 'database is locked' }, 500)
    await openRoute(user)

    const dialog = await screen.findByRole('alertdialog')
    await waitFor(() => expect(dialog).toHaveTextContent(/couldn’t check/i))
    expect(screen.getByRole('button', { name: /dispatch/i })).toBeDisabled()
    expect(dispatchCalls()).toHaveLength(0)
  })

  it('refuses to dispatch when nothing is eligible', async () => {
    const user = userEvent.setup()
    mount({ ...PREVIEW, would_dispatch: 0, value_paise: 0 })
    await openRoute(user)

    const dialog = await screen.findByRole('alertdialog')
    await waitFor(() => expect(dialog).toHaveTextContent(/nothing is eligible/i))
    expect(screen.getByRole('button', { name: /dispatch/i })).toBeDisabled()
  })

  it('opens focused on Cancel, so a stray Return does not dispatch', async () => {
    const user = userEvent.setup()
    mount()
    await openRoute(user)
    await screen.findByRole('alertdialog')

    expect(screen.getByRole('button', { name: /cancel/i })).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(dispatchCalls()).toHaveLength(0)
  })
})
