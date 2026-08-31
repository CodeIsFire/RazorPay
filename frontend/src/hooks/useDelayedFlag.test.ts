import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useDelayedFlag } from './useDelayedFlag'

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

const advance = (ms: number) => act(() => void vi.advanceTimersByTime(ms))

describe('useDelayedFlag', () => {
  it('shows nothing at all for a response that beats the delay', () => {
    // The whole point: on localhost the API answers in ~10ms, and a skeleton
    // that appears and vanishes inside 20ms is a flash, not information.
    const { result, rerender } = renderHook(({ on }) => useDelayedFlag(on), {
      initialProps: { on: true },
    })
    advance(60)
    rerender({ on: false })
    advance(1000)
    expect(result.current).toBe(false)
  })

  it('shows the flag once the response is genuinely slow', () => {
    const { result } = renderHook(() => useDelayedFlag(true))
    expect(result.current).toBe(false)
    advance(130)
    expect(result.current).toBe(true)
  })

  it('holds a shown flag for the minimum, so it cannot flash out', () => {
    const { result, rerender } = renderHook(({ on }) => useDelayedFlag(on), {
      initialProps: { on: true },
    })
    advance(130)
    expect(result.current).toBe(true)

    // Data lands almost immediately after the skeleton appeared.
    rerender({ on: false })
    advance(100)
    expect(result.current).toBe(true)

    advance(300)
    expect(result.current).toBe(false)
  })

  it('drops the flag immediately once it has been visible long enough', () => {
    const { result, rerender } = renderHook(({ on }) => useDelayedFlag(on), {
      initialProps: { on: true },
    })
    advance(130)
    advance(400)
    rerender({ on: false })
    advance(1)
    expect(result.current).toBe(false)
  })

  it('respects custom timings', () => {
    const { result } = renderHook(() => useDelayedFlag(true, { delayMs: 500, minVisibleMs: 0 }))
    advance(400)
    expect(result.current).toBe(false)
    advance(150)
    expect(result.current).toBe(true)
  })
})
