import { LoadingAnnounce, Skeleton } from '@/components/ui/Skeleton'

/* App-shaped skeletons. Each one mirrors the real component's geometry closely
   enough that nothing shifts when the data lands -- a placeholder that is the
   wrong size just moves the jank rather than removing it. */

/** Mirrors GapHero: eyebrow, hero figure, note, then the two-row bar. */
export function GapHeroSkeleton() {
  return (
    <div className="card gap-hero">
      <LoadingAnnounce what="the reconciliation totals" />
      <Skeleton width={168} height={13} />
      <Skeleton className="skeleton-figure" width={300} height={42} radius="var(--r-control)" />
      <Skeleton width={232} height={12} />
      <div className="gap-bar">
        {['expected', 'settled'].map((row) => (
          <div className="row" key={row}>
            <Skeleton width={62} height={12} />
            <Skeleton width={104} height={13} />
            <Skeleton height={18} radius="var(--r-sm)" />
          </div>
        ))}
      </div>
    </div>
  )
}

/** Mirrors ReconciliationFlow: total, track, legend, footer stats. */
export function FlowSkeleton() {
  return (
    <div className="card">
      <div className="card-head">
        <div>
          <Skeleton width={182} height={16} />
          <Skeleton className="skeleton-sub" width={420} height={12} />
        </div>
      </div>
      <div className="card-body">
        <LoadingAnnounce what="the reconciliation breakdown" />
        <div className="flow-total">
          <Skeleton width={54} height={24} radius="var(--r-sm)" />
        </div>
        <Skeleton height={18} radius="var(--r-sm)" />
        <div className="flow-legend">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} width={148} height={14} />
          ))}
        </div>
        <div className="flow-footer">
          {[0, 1].map((i) => (
            <div className="flow-stat" key={i}>
              <Skeleton width={84} height={10} />
              <Skeleton width={64} height={14} />
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

/** Mirrors the terminal-style activity stream: monospace lines of even height
    and uneven length. */
export function LogSkeleton({ lines = 8 }: { lines?: number }) {
  const widths = ['86%', '72%', '94%', '65%', '80%', '90%', '58%', '76%']
  return (
    <div className="logview" aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <div className="log-line" key={i}>
          <Skeleton width={widths[i % widths.length]} height={11} />
        </div>
      ))}
    </div>
  )
}

/** Mirrors the KPI tile row on Insights. */
export function TileRowSkeleton({ tiles = 3 }: { tiles?: number }) {
  return (
    <div className="kpi-row">
      {Array.from({ length: tiles }, (_, i) => (
        <div className="tile" key={i}>
          <Skeleton width={96} height={10} />
          <Skeleton className="skeleton-figure" width={132} height={24} radius="var(--r-sm)" />
        </div>
      ))}
    </div>
  )
}

/** Mirrors BarRows: label, track, value per row. */
export function BarRowsSkeleton({ rows = 5 }: { rows?: number }) {
  const fills = ['92%', '80%', '61%', '52%', '44%', '35%']
  return (
    <div aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        <div className="bar-row" key={i}>
          <Skeleton width={i % 2 ? 108 : 132} height={12} />
          <Skeleton height={10} width={fills[i % fills.length]} radius="var(--r-sm)" />
          <Skeleton width={72} height={12} />
        </div>
      ))}
    </div>
  )
}
