import { useEffect, useRef, useState } from 'react'

/* Returns false for one frame, then true -- the hook a CSS-transitioned
   reveal needs in React.

   The vanilla dashboard got its sweep for free: it rendered the segment into
   static HTML at width 0 and a later JS write triggered the transition. React
   mounts the element with its final width already applied, so nothing
   transitions at all. Hence: mount at zero, then write the real value.

   Three behaviours here are load-bearing, all learned the hard way:

   - A grow-from-zero started while the tab is hidden can sit frozen at 0,
     because neither CSS transitions nor rAF advance in a tab that is not
     compositing. A hidden tab therefore skips straight to the final state --
     an un-animated bar is fine, an empty one reads as broken.
   - The timeout backs up the rAF pair for the same reason; whichever lands
     first wins and the other is a no-op.
   - `signature` identifies what is being revealed. A poll that produces the
     same signature must not restart the animation, so the bar does not blink
     every 15 seconds. Round the signature to the precision actually rendered:
     a difference finer than the painted value is not a change.

   The cleanup releasing the guard is what makes this survive StrictMode, whose
   mount/cleanup/mount cancels the first pass's scheduled work; without it the
   guard blocks the second pass from rescheduling and the reveal never fires. */
export function useFirstPaintReveal(signature: string): boolean {
  const [revealed, setRevealed] = useState(false)
  const seen = useRef<string | null>(null)

  useEffect(() => {
    if (seen.current === signature) return
    const first = seen.current === null
    seen.current = signature
    if (!first) return

    if (document.visibilityState !== 'visible') {
      setRevealed(true)
      return
    }

    let raf1 = 0
    let raf2 = 0
    const settle = () => setRevealed(true)
    raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(settle)
    })
    const timer = setTimeout(settle, 120)

    return () => {
      cancelAnimationFrame(raf1)
      cancelAnimationFrame(raf2)
      clearTimeout(timer)
      // Releasing the guard is what lets a torn-down-before-it-ran schedule be
      // retried. See the note above on StrictMode.
      seen.current = null
    }
  }, [signature])

  return revealed
}
