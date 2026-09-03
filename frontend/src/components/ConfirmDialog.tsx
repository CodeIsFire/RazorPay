import { useEffect, useRef, type ReactNode } from 'react'
import { useFocusTrap, useScrollLock } from '@/hooks/useFocusTrap'

/* The shell every "are you sure" in this app shares.

   Extracted from RouteConfirm when the data upload needed the same thing: a
   scrim, a real focus trap, Escape to cancel, and focus landing on Cancel so
   a stray Return picks the safe option. None of that is decoration -- it is
   what makes aria-modal's claim that the page behind is inert actually true --
   and it is exactly the part that gets quietly dropped when a second dialog
   is written from scratch.

   The body is the caller's: what these dialogs have in common is the
   machinery, not the words. */
export function ConfirmDialog({
  title,
  titleId,
  confirmLabel,
  confirmDisabled = false,
  onConfirm,
  onCancel,
  children,
}: {
  title: string
  titleId: string
  confirmLabel: string
  confirmDisabled?: boolean
  onConfirm: () => void
  onCancel: () => void
  children: ReactNode
}) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const cancelRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    cancelRef.current?.focus()
  }, [])

  useFocusTrap(dialogRef)
  useScrollLock()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])

  return (
    <div className="modal-scrim" onClick={onCancel}>
      <div
        ref={dialogRef}
        className="modal"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-title" id={titleId}>
          {title}
        </div>

        {children}

        <div className="modal-actions">
          <button ref={cancelRef} className="btn" onClick={onCancel}>
            Cancel
          </button>
          <button className="btn btn-danger" onClick={onConfirm} disabled={confirmDisabled}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
