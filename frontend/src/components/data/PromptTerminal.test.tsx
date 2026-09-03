import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PromptTerminal } from '@/components/data/PromptTerminal'
import { ToastProvider } from '@/components/Toast'
import { CONVERSION_PROMPT } from '@/lib/conversionPrompt'

/* One button, one job: put the whole prompt on the clipboard. The tests that
   matter are that it copies the WHOLE thing (not the visible slice of a
   scrolling box) and that a refused clipboard says so instead of silently
   claiming success -- someone would paste stale content and never know. */

let writeText: ReturnType<typeof vi.fn>

function mount({ refuse = false } = {}) {
  // The rejection is created inside the call, not passed in ready-made: a
  // promise rejected at setup time has no handler yet and surfaces as an
  // unhandled rejection that fails whichever test happens to be running.
  writeText = vi.fn(() =>
    refuse ? Promise.reject(new Error('denied')) : Promise.resolve(),
  )
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  })
  return render(
    <ToastProvider>
      <PromptTerminal />
    </ToastProvider>,
  )
}

beforeEach(() => vi.useRealTimers())
afterEach(() => vi.restoreAllMocks())

describe('PromptTerminal', () => {
  it('shows the prompt itself, not a description of it', () => {
    mount()
    expect(screen.getByText(/WHICH FILE:/)).toBeInTheDocument()
    expect(screen.getByText(/amount_paise/)).toBeInTheDocument()
  })

  it('copies the entire prompt, not just what is scrolled into view', async () => {
    const user = userEvent.setup()
    mount()
    await user.click(screen.getByRole('button', { name: /copy/i }))
    expect(writeText).toHaveBeenCalledWith(CONVERSION_PROMPT)
  })

  it('confirms the copy on the button itself', async () => {
    const user = userEvent.setup()
    mount()
    await user.click(screen.getByRole('button', { name: /copy/i }))
    expect(await screen.findByRole('button', { name: /copied/i })).toBeInTheDocument()
  })

  it('reports a refused clipboard instead of claiming success', async () => {
    const user = userEvent.setup()
    mount({ refuse: true })
    await user.click(screen.getByRole('button', { name: /copy/i }))

    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(/couldn’t copy/i),
    )
    expect(screen.queryByRole('button', { name: /copied/i })).not.toBeInTheDocument()
  })

  it('keeps the scrolling block reachable from the keyboard', () => {
    // A fixed-height box that only a mouse wheel can scroll hides most of
    // its content from keyboard users.
    mount()
    expect(screen.getByRole('region', { name: /conversion prompt/i })).toHaveAttribute(
      'tabindex',
      '0',
    )
  })
})

describe('the prompt text', () => {
  it('carries the rules a CSV is actually rejected for', () => {
    // Pinned deliberately: this string encodes app/ingest.py's validation,
    // and a prompt that drifts from the validator produces files that fail.
    expect(CONVERSION_PROMPT).toContain('external_ref,amount_paise,occurred_at')
    expect(CONVERSION_PROMPT).toContain('rupees × 100')
    expect(CONVERSION_PROMPT).toContain('ISO 8601')
    expect(CONVERSION_PROMPT).toContain('original_ref')
  })
})
