import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatPanel } from './ChatPanel'
import { ToastProvider } from '@/components/Toast'
import type { IntegrationStatus } from '@/lib/types'

const status = (over: Partial<IntegrationStatus> = {}): IntegrationStatus => ({
  executor: 'live',
  key_configured: true,
  account_number_configured: true,
  webhook_secret_configured: true,
  assistant_configured: true,
  payable_ledger_rows: 10,
  ledger_rows: 10,
  ...over,
})

let fetchMock: ReturnType<typeof vi.fn>

function mount(integration: IntegrationStatus) {
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).includes('/integration/status')) {
      return new Response(JSON.stringify(integration), { status: 200 })
    }
    if (String(url).includes('/assistant/chat')) {
      const body = JSON.parse(String(init?.body))
      return new Response(JSON.stringify({ reply: `echo:${body.message}` }), { status: 200 })
    }
    throw new Error(`unexpected fetch: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)

  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <ChatPanel />
      </ToastProvider>
    </QueryClientProvider>,
  )
}

const chatCalls = () =>
  fetchMock.mock.calls.filter(([url]) => String(url).includes('/assistant/chat'))

beforeEach(() => vi.useRealTimers())
afterEach(() => vi.unstubAllGlobals())

describe('ChatPanel', () => {
  it('sends {message, history} — not {messages}', async () => {
    // The removed Next.js scaffold posted {messages}, which the backend has
    // never accepted. This is the regression test for that contract.
    const user = userEvent.setup()
    mount(status())
    await user.click(await screen.findByRole('button', { name: /need help/i }))
    await user.type(screen.getByLabelText('Your question'), 'why is my match rate low')
    await user.click(screen.getByRole('button', { name: 'Ask' }))

    await waitFor(() => expect(chatCalls()).toHaveLength(1))
    const body = JSON.parse(String(chatCalls()[0][1]?.body))
    expect(Object.keys(body).sort()).toEqual(['history', 'message'])
    expect(body.message).toBe('why is my match rate low')
    expect(body.history).toEqual([])
  })

  it('replays the transcript as history on the next turn', async () => {
    const user = userEvent.setup()
    mount(status())
    await user.click(await screen.findByRole('button', { name: /need help/i }))
    const input = screen.getByLabelText('Your question')

    await user.type(input, 'first')
    await user.click(screen.getByRole('button', { name: 'Ask' }))
    await waitFor(() => expect(screen.getByText('echo:first')).toBeInTheDocument())

    await user.type(input, 'second')
    await user.click(screen.getByRole('button', { name: 'Ask' }))
    await waitFor(() => expect(chatCalls()).toHaveLength(2))

    const body = JSON.parse(String(chatCalls()[1][1]?.body))
    expect(body.message).toBe('second')
    expect(body.history).toEqual([
      { role: 'user', content: 'first' },
      { role: 'assistant', content: 'echo:first' },
    ])
  })

  it('reports a failure as a toast and leaves no half-finished exchange', async () => {
    const user = userEvent.setup()
    mount(status())
    await user.click(await screen.findByRole('button', { name: /need help/i }))

    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).includes('/assistant/chat')) {
        return new Response(JSON.stringify({ detail: 'The assistant is unavailable right now.' }), {
          status: 502,
        })
      }
      return new Response(JSON.stringify(status()), { status: 200 })
    })

    await user.type(screen.getByLabelText('Your question'), 'this fails')
    await user.click(screen.getByRole('button', { name: 'Ask' }))

    await waitFor(() =>
      expect(screen.getByText('The assistant is unavailable right now.')).toBeInTheDocument(),
    )
    // The failed question must not be left sitting in the log as if it landed.
    expect(screen.queryByText('this fails')).not.toBeInTheDocument()
    expect(screen.queryByText('Thinking…')).not.toBeInTheDocument()
  })

  it('will not open at all when no assistant key is configured', async () => {
    // Without a key the assistant cannot answer, so the button explains the
    // app instead of opening a panel that could only fail.
    const user = userEvent.setup()
    mount(status({ assistant_configured: false }))
    const fab = await screen.findByRole('button', { name: /need help/i })
    await waitFor(() => expect(fab).toHaveAttribute('title', 'About this app'))

    await user.click(fab)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByText(/reconciles a synthetic ledger/i)).toBeInTheDocument()
    expect(chatCalls()).toHaveLength(0)
  })

  it('renders a reply as text, never as markup', async () => {
    const user = userEvent.setup()
    mount(status())
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).includes('/assistant/chat')) {
        return new Response(JSON.stringify({ reply: '<img src=x onerror=alert(1)>' }), {
          status: 200,
        })
      }
      return new Response(JSON.stringify(status()), { status: 200 })
    })
    await user.click(await screen.findByRole('button', { name: /need help/i }))
    await user.type(screen.getByLabelText('Your question'), 'hi')
    await user.click(screen.getByRole('button', { name: 'Ask' }))

    const bubble = await screen.findByText('<img src=x onerror=alert(1)>')
    expect(bubble.querySelector('img')).toBeNull()
  })
})
