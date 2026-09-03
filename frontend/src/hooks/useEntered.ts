import { useEffect, useState } from 'react'

const hasHash = () => window.location.hash.replace('#', '') !== ''

/** Whether the visitor has entered the dashboard, derived from the URL.

    One fact, read from one place: a bare URL shows the landing, any hash at
    all shows the dashboard. Deriving it rather than storing it in state is
    what makes the browser's Back button work with no extra code -- popping
    the hash off restores the landing, because there is no second source of
    truth left saying otherwise.

    Any hash counts, including one no tab claims: useActiveTab already falls
    back to overview for those, and a second opinion here would mean
    `#garbage` showed the landing while `#overview` showed the dashboard. */
export function useEntered(): boolean {
  const [entered, setEntered] = useState(hasHash)

  useEffect(() => {
    const onHashChange = () => setEntered(hasHash())
    window.addEventListener('hashchange', onHashChange)
    // The hash can change between first render and this effect attaching.
    onHashChange()
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  return entered
}
