import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RouteConfirm } from './RouteConfirm'
import type { RoutePreview } from '@/lib/types'

const preview: RoutePreview = {
  would_dispatch: 3,
  would_skip: 1,
  would_abandon: 0,
  would_error: 0,
  value_paise: 450000,
}

function open(over: Partial<Parameters<typeof RouteConfirm>[0]> = {}) {
  return render(
    <div>
      {/* Stands in for the dashboard behind the dialog. If focus can reach
          this, the modal is not modal. */}
      <button type="button">behind the dialog</button>
      <RouteConfirm
        preview={preview}
        loading={false}
        error={null}
        confirming={false}
        onConfirm={() => {}}
        onCancel={() => {}}
        {...over}
      />
    </div>,
  )
}

afterEach(() => {
  document.body.style.overflow = ''
})

describe('the dispatch confirmation is genuinely modal', () => {
  it('keeps Tab inside the dialog instead of walking onto the page behind', async () => {
    const user = userEvent.setup()
    open()
    const behind = screen.getByRole('button', { name: 'behind the dialog' })

    // Cancel holds focus on mount; tab all the way round several times.
    for (let i = 0; i < 6; i++) {
      await user.tab()
      expect(document.activeElement).not.toBe(behind)
    }
  })

  it('actually cycles Cancel -> Dispatch -> Cancel, not just refusing to move', async () => {
    /* The escape tests below pass for a trap that merely freezes focus on one
       control, which is exactly what this trap used to do: its visibility
       filter used offsetParent, which is null for everything in jsdom, so the
       focusable list collapsed to one element and every Tab was
       preventDefault'd. Asserting the ORDER is what distinguishes a working
       cycle from a frozen one. */
    const user = userEvent.setup()
    open()

    const names: string[] = []
    for (let i = 0; i < 4; i++) {
      await user.tab()
      names.push((document.activeElement as HTMLElement)?.textContent ?? '')
    }

    expect(names.some((n) => /dispatch/i.test(n))).toBe(true)
    expect(names.some((n) => /cancel/i.test(n))).toBe(true)
    // Wrapped rather than stopped: the same control is reached twice in four
    // presses of a two-control dialog.
    expect(new Set(names).size).toBeLessThan(names.length)
  })

  it('cycles backwards without escaping either', async () => {
    const user = userEvent.setup()
    open()
    const behind = screen.getByRole('button', { name: 'behind the dialog' })

    for (let i = 0; i < 6; i++) {
      await user.tab({ shift: true })
      expect(document.activeElement).not.toBe(behind)
    }
  })

  it('still opens focused on Cancel, so a stray Return does not dispatch', () => {
    open()
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Cancel' }))
  })

  it('does not trap focus onto a control that is disabled', async () => {
    // Dispatch is disabled while the preview is loading. Tab must not park on
    // it, which would leave Return doing nothing and look like a hung dialog.
    const user = userEvent.setup()
    open({ preview: null, loading: true })
    await user.tab()
    const dispatch = screen.getByRole('button', { name: /dispatch/i })
    expect(dispatch).toBeDisabled()
    expect(document.activeElement).not.toBe(dispatch)
  })

  it('freezes the page behind it and restores scrolling afterwards', () => {
    const { unmount } = open()
    expect(document.body.style.overflow).toBe('hidden')
    unmount()
    expect(document.body.style.overflow).not.toBe('hidden')
  })

  it('still closes on Escape', async () => {
    const onCancel = vi.fn()
    const user = userEvent.setup()
    open({ onCancel })
    await user.keyboard('{Escape}')
    expect(onCancel).toHaveBeenCalled()
  })
})
