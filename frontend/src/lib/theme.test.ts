// Vite's ?raw import rather than node:fs -- it needs no @types/node in an
// app-scoped tsconfig, and it resolves through the same pipeline that serves
// the file, so the test reads exactly what ships.
import html from '../../index.html?raw'
import { afterEach, describe, expect, it } from 'vitest'
import {
  readPreference,
  resolve,
  THEME_STORAGE_KEY,
  writePreference,
  type ThemePreference,
} from './theme'

afterEach(() => localStorage.clear())

describe('resolve', () => {
  it('answers the OS only when the preference defers to it', () => {
    expect(resolve('system', true)).toBe('light')
    expect(resolve('system', false)).toBe('dark')
  })

  it('ignores the OS when a theme was chosen explicitly', () => {
    // This is the case the old media-query contract could not express: on an
    // OS set to dark, choosing light has to actually produce light.
    expect(resolve('light', false)).toBe('light')
    expect(resolve('dark', true)).toBe('dark')
  })
})

describe('readPreference', () => {
  it('round-trips each preference', () => {
    for (const pref of ['system', 'light', 'dark'] as ThemePreference[]) {
      writePreference(pref)
      expect(readPreference()).toBe(pref)
    }
  })

  it('falls back to system for an unset or unrecognised value', () => {
    expect(readPreference()).toBe('system')
    // A value left by an older build must not resolve to a broken theme.
    localStorage.setItem(THEME_STORAGE_KEY, 'solarized')
    expect(readPreference()).toBe('system')
  })
})

describe('the pre-paint script in index.html', () => {
  // The script has to be inline and duplicated -- a module script is deferred
  // and would run after first paint, which is the flash it exists to prevent.
  // Duplication means it can drift, so pin the contract it shares with theme.ts.
  it('reads the same storage key this module writes', () => {
    expect(html).toContain(THEME_STORAGE_KEY)
  })

  it('resolves against the same media query', () => {
    expect(html).toContain('prefers-color-scheme: light')
  })

  it('writes a concrete theme rather than leaving the attribute unset', () => {
    // index.css keys both the palette and every `dark:` utility off this
    // attribute, so "no attribute" is not a state the app renders correctly in.
    expect(html).toMatch(/dataset\.theme\s*=/)
    expect(html).toContain("'light'")
    expect(html).toContain("'dark'")
  })

  it('is not deferred', () => {
    const scriptTag = html.slice(html.indexOf('<script'), html.indexOf('</script>'))
    expect(scriptTag).not.toContain('type="module"')
    expect(scriptTag).not.toContain('defer')
  })
})
