import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/* These tests are the guarantee that this dashboard does not leak ledger data
   to an analytics service. They assert on what is SENT, not on how it is
   built, so a future refactor of analytics.ts cannot quietly widen it. */

const trackSpy = vi.fn()
const constructSpy = vi.fn()

vi.mock('@openpanel/sdk', () => ({
  OpenPanel: class {
    constructor(options: { clientId: string }) {
      constructSpy(options)
    }
    track = (...args: unknown[]) => {
      trackSpy(...args)
      return Promise.resolve()
    }
  },
}))

async function load() {
  vi.resetModules()
  return import('./analytics')
}

function setDoNotTrack(value: string | null) {
  Object.defineProperty(window.navigator, 'doNotTrack', {
    value,
    configurable: true,
  })
}

beforeEach(() => {
  trackSpy.mockClear()
  constructSpy.mockClear()
  setDoNotTrack(null)
})

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('when no client id is configured', () => {
  it('never constructs a client and never sends anything', async () => {
    // The default state -- a fresh clone, a dev run, CI. Silence is the point.
    vi.stubEnv('VITE_OPENPANEL_CLIENT_ID', '')
    const { trackTabView } = await load()

    trackTabView('overview')
    trackTabView('exceptions')

    expect(constructSpy).not.toHaveBeenCalled()
    expect(trackSpy).not.toHaveBeenCalled()
  })
})

describe('when a client id is configured', () => {
  beforeEach(() => vi.stubEnv('VITE_OPENPANEL_CLIENT_ID', 'test-client-id'))

  it('sends one tab_view carrying only the tab name', async () => {
    const { trackTabView } = await load()
    trackTabView('insights')

    expect(trackSpy).toHaveBeenCalledTimes(1)
    const [name, properties] = trackSpy.mock.calls[0]
    expect(name).toBe('tab_view')
    // The whole payload, asserted exactly: no url, no referrer, no element
    // data, and above all no ledger reference.
    expect(properties).toEqual({ tab: 'insights' })
  })

  it('never sends anything but a known tab name', async () => {
    const { trackTabView } = await load()
    for (const tab of ['overview', 'exceptions', 'insights', 'audit'] as const) {
      trackTabView(tab)
    }
    const sent = trackSpy.mock.calls.map(([, p]) => (p as { tab: string }).tab)
    expect(sent).toEqual(['overview', 'exceptions', 'insights', 'audit'])
  })

  it('builds the client with the id and no secret', async () => {
    // A client secret in a browser bundle would not be a secret.
    const { trackTabView } = await load()
    trackTabView('overview')
    expect(constructSpy).toHaveBeenCalledWith({ clientId: 'test-client-id' })
  })

  it('reuses one client across events', async () => {
    const { trackTabView } = await load()
    trackTabView('overview')
    trackTabView('audit')
    expect(constructSpy).toHaveBeenCalledTimes(1)
  })

  it('stays silent when the browser signals Do Not Track', async () => {
    setDoNotTrack('1')
    const { trackTabView } = await load()
    trackTabView('overview')
    expect(constructSpy).not.toHaveBeenCalled()
    expect(trackSpy).not.toHaveBeenCalled()
  })

  it('swallows a failing send rather than surfacing it', async () => {
    // This app dispatches payouts. A dead analytics host must never become an
    // error the operator has to think about.
    trackSpy.mockImplementationOnce(() => {
      throw new Error('network down')
    })
    const { trackTabView } = await load()
    expect(() => trackTabView('overview')).not.toThrow()
  })

  it('swallows a rejected send rather than becoming an unhandled rejection', async () => {
    const { trackTabView } = await load()
    trackSpy.mockImplementationOnce(() => Promise.reject(new Error('502')))
    expect(() => trackTabView('audit')).not.toThrow()
    await new Promise((r) => setTimeout(r, 0))
  })
})
