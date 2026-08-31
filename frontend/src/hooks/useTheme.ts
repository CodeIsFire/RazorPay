import { useCallback, useEffect, useState } from 'react'
import {
  apply,
  currentlyApplied,
  LIGHT_QUERY,
  readPreference,
  THEME_STORAGE_KEY,
  resolve,
  type ResolvedTheme,
  type ThemePreference,
  writePreference,
} from '@/lib/theme'

/* Owns the theme preference and keeps <html data-theme> in step with it.

   Two listeners, for two different ways the answer can change without this tab
   doing anything:

   - matchMedia, so an OS switch takes effect live while the preference is
     'system'. It stays attached under an explicit preference too, because the
     OS value is still reported to the user as "System (dark)" and that label
     has to stay true.
   - storage, so changing the theme in one tab moves every other tab. Without
     it two open dashboards disagree until one is reloaded.

   Initial state is read from the DOM rather than recomputed: the pre-paint
   script in index.html has already resolved and applied a theme, so seeding
   from it means the first React render agrees with what is on screen. */
export function useTheme() {
  const [preference, setPreferenceState] = useState<ThemePreference>(readPreference)
  const [prefersLight, setPrefersLight] = useState(
    () => currentlyApplied() === 'light' && readPreference() === 'system',
  )

  useEffect(() => {
    const mql = window.matchMedia(LIGHT_QUERY)
    setPrefersLight(mql.matches)
    const onChange = (event: MediaQueryListEvent) => setPrefersLight(event.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [])

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      // A null key means the whole store was cleared, which should also be
      // honoured rather than ignored as "not our key".
      if (event.key !== null && event.key !== THEME_STORAGE_KEY) return
      setPreferenceState(readPreference())
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  const resolved: ResolvedTheme = resolve(preference, prefersLight)

  useEffect(() => {
    apply(resolved)
  }, [resolved])

  const setPreference = useCallback((next: ThemePreference) => {
    writePreference(next)
    setPreferenceState(next)
  }, [])

  return { preference, resolved, setPreference }
}
