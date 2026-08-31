/* Theme resolution, kept pure and DOM-light so it can be unit tested.

   The contract this file upholds is described on `@custom-variant dark` in
   index.css: <html> ALWAYS carries a concrete data-theme of "dark" or "light",
   never "system" and never nothing. Both the token palette and every Tailwind
   `dark:` utility key off that one attribute, so if it were ever absent or
   aspirational the two could disagree and render a half-light page.

   The same resolution runs twice: once in the inline pre-paint script in
   index.html (to avoid a flash) and once here (to react to changes). Keep them
   in agreement -- theme.storage-key.test.ts asserts the literals match. */

export type ThemePreference = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'

/** Mirrored in the inline script in index.html. */
export const THEME_STORAGE_KEY = 'rr-theme'

export const LIGHT_QUERY = '(prefers-color-scheme: light)'

function isPreference(value: unknown): value is ThemePreference {
  return value === 'system' || value === 'light' || value === 'dark'
}

/** Reads the stored preference. Anything unrecognised -- including a value from
    an older build -- degrades to 'system' rather than throwing. */
export function readPreference(): ThemePreference {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY)
    return isPreference(stored) ? stored : 'system'
  } catch {
    // Safari private mode throws on localStorage access, not just on write.
    return 'system'
  }
}

export function writePreference(preference: ThemePreference): void {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, preference)
  } catch {
    // Persistence is a convenience; the in-memory preference still applies for
    // this session, so a storage failure must not break switching themes.
  }
}

/** 'system' resolves against the OS; an explicit preference is its own answer. */
export function resolve(preference: ThemePreference, prefersLight: boolean): ResolvedTheme {
  if (preference === 'system') return prefersLight ? 'light' : 'dark'
  return preference
}

/** Writes the resolved theme to <html>. `colorScheme` is set alongside the
    attribute so form controls, scrollbars and the canvas the browser paints
    behind the page follow the theme too -- CSS tokens alone do not reach those. */
export function apply(resolved: ResolvedTheme): void {
  const root = document.documentElement
  root.dataset.theme = resolved
  root.style.colorScheme = resolved
}

/** The theme currently on <html>, as written by the pre-paint script. Used to
    seed React state so the first render already agrees with what is painted. */
export function currentlyApplied(): ResolvedTheme {
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'
}
