import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useActiveTab } from './useActiveTab'

const trackTabView = vi.fn()
vi.mock('@/lib/analytics', () => ({ trackTabView: (...a: unknown[]) => trackTabView(...a) }))

beforeEach(() => {
  trackTabView.mockClear()
  window.location.hash = ''
})

describe('useActiveTab', () => {
  it('reports the landing tab once', () => {
    // The tab someone arrives on is the most interesting one, so it counts --
    // but only once, despite React's development double-effect.
    renderHook(() => useActiveTab())
    expect(trackTabView.mock.calls).toEqual([['overview']])
  })

  it('reports the deep-linked tab, not the default', () => {
    window.location.hash = '#insights'
    renderHook(() => useActiveTab())
    expect(trackTabView.mock.calls).toEqual([['insights']])
  })

  it('reports each tab change once', () => {
    const { result } = renderHook(() => useActiveTab())
    act(() => result.current[1]('exceptions'))
    act(() => result.current[1]('audit'))
    expect(trackTabView.mock.calls).toEqual([['overview'], ['exceptions'], ['audit']])
  })

  it('does not report re-selecting the tab already shown', () => {
    const { result } = renderHook(() => useActiveTab())
    act(() => result.current[1]('exceptions'))
    act(() => result.current[1]('exceptions'))
    expect(trackTabView.mock.calls).toEqual([['overview'], ['exceptions']])
  })

  it('reports a tab reached by the browser Back button', () => {
    const { result } = renderHook(() => useActiveTab())
    act(() => result.current[1]('audit'))
    act(() => {
      window.location.hash = '#insights'
      window.dispatchEvent(new HashChangeEvent('hashchange'))
    })
    expect(trackTabView.mock.calls).toEqual([['overview'], ['audit'], ['insights']])
  })

  it('ignores a hash that is not a real tab', () => {
    window.location.hash = '#not-a-tab'
    const { result } = renderHook(() => useActiveTab())
    expect(result.current[0]).toBe('overview')
    expect(trackTabView.mock.calls).toEqual([['overview']])
  })
})
