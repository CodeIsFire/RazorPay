import { useCallback, useEffect, useRef, useState } from 'react'

/* The open/close contract shared by the Run menu and the assistant panel:
   Escape closes and returns focus to the trigger, a click outside closes, and
   the trigger's aria-expanded mirrors the state. Both surfaces behaved
   identically in the vanilla dashboard; this is that behaviour in one place
   rather than wired twice. */
export function useDismissableMenu<T extends HTMLElement, U extends HTMLElement>() {
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<T>(null)
  const panelRef = useRef<U>(null)

  const close = useCallback((returnFocus = true) => {
    setOpen(false)
    if (returnFocus) triggerRef.current?.focus()
  }, [])

  useEffect(() => {
    if (!open) return

    const onKeyDown = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape') close()
    }
    const onPointerDown = (ev: PointerEvent) => {
      const target = ev.target as Node
      if (panelRef.current?.contains(target) || triggerRef.current?.contains(target)) return
      // No focus return here: the user is already interacting somewhere else,
      // and yanking focus back to the trigger would fight that.
      close(false)
    }

    document.addEventListener('keydown', onKeyDown)
    document.addEventListener('pointerdown', onPointerDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.removeEventListener('pointerdown', onPointerDown)
    }
  }, [open, close])

  return { open, setOpen, close, triggerRef, panelRef }
}
