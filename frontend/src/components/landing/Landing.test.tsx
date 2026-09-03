import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Landing } from './Landing'

/* The landing's whole job is to hand someone to one of two places. Both
   routes go through the URL rather than a callback, because the URL is what
   useEntered reads -- and assigning the hash (rather than replacing it) is
   what leaves a history entry for Back to come back to. */

beforeEach(() => {
  window.location.hash = ''
  vi.restoreAllMocks()
})

describe('Landing', () => {
  it('offers exactly two ways in', () => {
    render(<Landing />)
    expect(screen.getByRole('button', { name: /open dashboard/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /try it yourself/i })).toBeInTheDocument()
  })

  it('sends "Open dashboard" to the overview', async () => {
    const user = userEvent.setup()
    render(<Landing />)
    await user.click(screen.getByRole('button', { name: /open dashboard/i }))
    expect(window.location.hash).toBe('#overview')
  })

  it('sends "Try it yourself" straight to the Data tab', async () => {
    const user = userEvent.setup()
    render(<Landing />)
    await user.click(screen.getByRole('button', { name: /try it yourself/i }))
    expect(window.location.hash).toBe('#data')
  })

  it('leaves a history entry, so Back returns to the landing', async () => {
    // replaceState is right for switching tabs (Back should leave the
    // dashboard, not walk back through every tab visited) but wrong for
    // entering: it would make Back leave the site from the first click.
    const replaceState = vi.spyOn(window.history, 'replaceState')
    const user = userEvent.setup()
    render(<Landing />)
    await user.click(screen.getByRole('button', { name: /open dashboard/i }))
    expect(replaceState).not.toHaveBeenCalled()
  })

  it('does not render the dashboard chrome around itself', () => {
    // The point of a full-screen landing: no rail, no sidebar, no Run menu.
    render(<Landing />)
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^run$/i })).not.toBeInTheDocument()
  })
})
