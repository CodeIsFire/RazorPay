import { useEffect, useRef, useState } from 'react'

/* Debounces a loading flag in both directions, so a skeleton never flickers.

   Two separate problems, and a naive `if (isPending)` has both:

   - The API answers in ~10ms on localhost and only a little slower in
     production. Showing a skeleton for 10ms is a strobe -- the reader
     registers a flash of something without ever reading it, which is worse
     than a beat of nothing.
   - Once shown, a skeleton that vanishes 30ms later is the same flash in
     reverse.

   So: wait `delayMs` before admitting we are loading at all, and once we have
   admitted it, hold for at least `minVisibleMs`. Fast responses show no
   skeleton; slow ones show a stable one. */
export function useDelayedFlag(
  active: boolean,
  { delayMs = 120, minVisibleMs = 320 }: { delayMs?: number; minVisibleMs?: number } = {},
): boolean {
  const [visible, setVisible] = useState(false)
  const shownAt = useRef<number | null>(null)

  useEffect(() => {
    if (active) {
      if (visible) return
      const timer = setTimeout(() => {
        shownAt.current = Date.now()
        setVisible(true)
      }, delayMs)
      return () => clearTimeout(timer)
    }

    if (!visible) return
    const elapsed = shownAt.current === null ? minVisibleMs : Date.now() - shownAt.current
    const remaining = Math.max(0, minVisibleMs - elapsed)
    if (remaining === 0) {
      shownAt.current = null
      setVisible(false)
      return
    }
    const timer = setTimeout(() => {
      shownAt.current = null
      setVisible(false)
    }, remaining)
    return () => clearTimeout(timer)
  }, [active, visible, delayMs, minVisibleMs])

  return visible
}
