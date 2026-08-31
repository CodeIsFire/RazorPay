import { useEffect, type RefObject } from 'react'

/* Keeps Tab inside a modal, and stops the page behind it scrolling.

   `aria-modal="true"` tells assistive technology that the rest of the page is
   inert, but it does not make it so: keyboard focus still walks straight out of
   the dialog and onto the controls behind it. For the route-confirm dialog that
   is not a cosmetic gap -- a keyboard user tabbing past Cancel lands on the
   dashboard underneath while a confirmation about dispatching real payouts is
   still on screen, with no visible indication of where focus went.

   Implemented as a manual cycle rather than by marking the app `inert`, because
   this dialog renders INSIDE the app subtree: inerting its own ancestor would
   disable the dialog too. */

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

export function useFocusTrap(ref: RefObject<HTMLElement | null>, active = true): void {
  useEffect(() => {
    if (!active) return
    const node = ref.current
    if (!node) return

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return

      // Re-queried on every Tab rather than cached: the dialog's own controls
      // change as the preview resolves -- Dispatch is disabled while loading
      // and while nothing is eligible -- so a list captured at mount would let
      // Tab land on a control that is no longer reachable.
      /* checkVisibility(), not offsetParent. offsetParent is null for any
         position:fixed element AND for everything in jsdom, which has no
         layout at all -- so this filter used to collapse to a single element,
         first === last === current, and every Tab was preventDefault'd. The
         trap froze focus rather than cycling it, and the tests could not tell
         the difference because they only asserted focus never escaped.
         checkVisibility is absent in jsdom, so `?? true` keeps the cycle real
         in tests while browsers get a genuine visibility check. */
      const focusable = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => el.checkVisibility?.() ?? true,
      )
      if (focusable.length === 0) {
        event.preventDefault()
        return
      }

      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const current = document.activeElement

      // Focus outside the dialog entirely (a stray programmatic focus, or the
      // very first Tab after a click landed on the scrim) is pulled back in.
      if (!(current instanceof HTMLElement) || !node.contains(current)) {
        event.preventDefault()
        first.focus()
        return
      }

      if (event.shiftKey && current === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && current === last) {
        event.preventDefault()
        first.focus()
      }
    }

    // Whatever opened the dialog gets focus back when it closes. Without this
    // a keyboard user who cancels is dropped on <body> and has to tab from the
    // top of the page to find their place again. useDismissableMenu already
    // makes this promise for the Run menu; a modal owes it at least as much.
    const opener = document.activeElement as HTMLElement | null

    document.addEventListener('keydown', onKeyDown, true)
    return () => {
      document.removeEventListener('keydown', onKeyDown, true)
      if (opener?.isConnected) opener.focus()
    }
  }, [ref, active])
}

/** Freezes the page behind a modal. Restores whatever overflow was there
    before rather than assuming it was the default, so nesting or an early
    unmount cannot leave the page permanently unscrollable. */
export function useScrollLock(active = true): void {
  useEffect(() => {
    if (!active) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [active])
}
