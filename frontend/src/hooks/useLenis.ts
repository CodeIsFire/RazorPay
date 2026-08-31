import Lenis from 'lenis'
import { useEffect, type RefObject } from 'react'

/* Three things this setup has to get right, all of them learned the hard way
   in the version this replaces:

   1. This page does not scroll on the window. `.content` is the scroll
      container, so Lenis is given an explicit wrapper/content pair -- the
      default window setup would attach to something that never scrolls and do
      nothing at all.
   2. The exception/audit tables and the assistant log scroll on their own and
      are marked [data-lenis-prevent], so wheel events inside them stay theirs.
      Without that, smoothing the page steals their scrolling.
   3. Smooth scrolling is motion, and this app respects prefers-reduced-motion.
      When that is set we simply never start Lenis. */
export function useLenis(
  wrapperRef: RefObject<HTMLElement | null>,
  contentRef: RefObject<HTMLElement | null>,
) {
  useEffect(() => {
    const wrapper = wrapperRef.current
    const content = contentRef.current
    if (!wrapper || !content) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return

    const lenis = new Lenis({
      wrapper,
      content,
      duration: 0.6,
      easing: (t: number) => 1 - Math.pow(1 - t, 3),
      smoothWheel: true,
      syncTouch: false,
    })

    let frame = 0
    const raf = (time: number) => {
      lenis.raf(time)
      frame = requestAnimationFrame(raf)
    }
    frame = requestAnimationFrame(raf)

    return () => {
      cancelAnimationFrame(frame)
      lenis.destroy()
    }
  }, [wrapperRef, contentRef])
}
