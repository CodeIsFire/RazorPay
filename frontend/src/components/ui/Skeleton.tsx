/* Placeholders for content that has not arrived yet.

   These exist because of a specific failure this dashboard had: no tab checked
   its query's pending state, so every screen rendered `data ?? []` and showed
   its EMPTY state while the first fetch was still in flight. On a tool that
   dispatches real payouts, "nothing needs attention" and "we have not asked
   yet" are opposite claims, and they looked identical. LoadFailed already
   makes that distinction for failed reads; this makes it for pending ones.

   A skeleton must therefore be visibly a placeholder and never mistakable for
   a real value -- no zeroes, no dashes, no empty-state copy. */

export function Skeleton({
  width,
  height = 12,
  radius = 'var(--r-sm)',
  className = '',
}: {
  width?: number | string
  height?: number | string
  radius?: string
  className?: string
}) {
  return (
    <span
      className={`skeleton ${className}`.trim()}
      style={{ width, height, borderRadius: radius }}
      aria-hidden="true"
    />
  )
}

/** Several lines of varying width, so a block reads as prose rather than as a
    stack of identical bars. Widths are deterministic, not random: a skeleton
    that reshuffles on every render draws attention to itself. */
export function SkeletonText({ lines = 3, className = '' }: { lines?: number; className?: string }) {
  const widths = ['92%', '78%', '85%', '64%', '88%', '71%']
  return (
    <span className={`skeleton-text ${className}`.trim()} aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} width={widths[i % widths.length]} />
      ))}
    </span>
  )
}

/** Fills a table body while keeping the real <thead> mounted, so column widths
    are already settled when the data lands and nothing jumps. */
export function TableSkeleton({ columns, rows = 8 }: { columns: number; rows?: number }) {
  const widths = ['70%', '54%', '84%', '46%', '62%', '76%', '38%', '58%', '66%']
  return (
    <tbody aria-hidden="true">
      {Array.from({ length: rows }, (_, r) => (
        <tr key={r} className="skeleton-row">
          {Array.from({ length: columns }, (_, c) => (
            <td key={c}>
              <Skeleton width={widths[(r + c) % widths.length]} />
            </td>
          ))}
        </tr>
      ))}
    </tbody>
  )
}

/** The announcement that pairs with any of the above. Screen readers get a
    spoken "loading" instead of a silent gap, since the shapes are aria-hidden. */
export function LoadingAnnounce({ what }: { what: string }) {
  return (
    <span className="sr-only" role="status">
      Loading {what}…
    </span>
  )
}
