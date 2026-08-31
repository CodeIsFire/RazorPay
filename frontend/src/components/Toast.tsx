import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'

type Toast = { message: string; isError: boolean; seq: number }
type ShowToast = (message: string, isError?: boolean) => void

const ToastContext = createContext<ShowToast>(() => {})

/** Every transient message in the app -- an action's result, a failed
    request, the developer-controls blurb -- goes through here. Errors from
    the assistant deliberately land here too rather than inside the chat log. */
export const useToast = () => useContext(ToastContext)

const DISMISS_MS = 4000

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<Toast | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const show = useCallback<ShowToast>((message, isError = false) => {
    setToast((prev) => ({ message, isError, seq: (prev?.seq ?? 0) + 1 }))
  }, [])

  // Keyed on seq, not on the message, so re-running an action that reports the
  // same result restarts the four seconds instead of letting the first
  // timeout dismiss the second toast early.
  useEffect(() => {
    if (!toast) return
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setToast(null), DISMISS_MS)
    return () => clearTimeout(timer.current)
  }, [toast])

  return (
    <ToastContext.Provider value={show}>
      {children}
      <div
        id="status-toast"
        className={[toast ? 'show' : '', toast?.isError ? 'error' : ''].filter(Boolean).join(' ')}
        role="status"
        aria-live="polite"
      >
        {toast?.message ?? ''}
      </div>
    </ToastContext.Provider>
  )
}
