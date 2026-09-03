import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { useEntered } from './useEntered'

/* The landing screen and the dashboard are chosen by one fact: whether the
   URL carries a hash. Deriving it from the URL rather than storing it in
   React state is what makes the browser's Back button return to the landing
   for free -- there is no second source of truth to keep in step. */

/* jsdom queues hashchange rather than firing it synchronously, so a plain
   assignment inside act() would assert before the listener ever ran. Same
   approach as useActiveTab.test.ts. */
function setHash(hash: string) {
  window.location.hash = hash
  window.dispatchEvent(new HashChangeEvent('hashchange'))
}

beforeEach(() => {
  window.location.hash = ''
})

describe('useEntered', () => {
  it('is false on a bare URL, so a first visit sees the landing', () => {
    const { result } = renderHook(() => useEntered())
    expect(result.current).toBe(false)
  })

  it('is true for a deep link, so every existing bookmark skips the landing', () => {
    window.location.hash = '#exceptions'
    const { result } = renderHook(() => useEntered())
    expect(result.current).toBe(true)
  })

  it('is true even for a hash it does not recognise', () => {
    // useActiveTab already falls back to overview for these. The landing
    // must not reintroduce a second opinion about an unknown hash.
    window.location.hash = '#garbage'
    const { result } = renderHook(() => useEntered())
    expect(result.current).toBe(true)
  })

  it('becomes true when a hash appears', () => {
    const { result } = renderHook(() => useEntered())
    act(() => setHash('#data'))
    expect(result.current).toBe(true)
  })

  it('becomes false again when the hash is cleared', () => {
    // This is the Back button: it pops the hash off and the landing returns.
    window.location.hash = '#overview'
    const { result } = renderHook(() => useEntered())
    act(() => setHash(''))
    expect(result.current).toBe(false)
  })
})
