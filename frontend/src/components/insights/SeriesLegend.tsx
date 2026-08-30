export interface SeriesLegendItem {
  label: string
  color: string
  /** Position of this series among the chart's <Bar> children -- that ordinal
      is what Bar compares against to decide whether it is the faded one. */
  seriesIndex: number
}

/* A legend that isolates. Hovering (or focusing) an entry fades the other
   series in the chart above, which is how you read "which days are actually
   holding money up" without squinting the blue apart from the grey.

   Only used where the entries name real series. The age chart's legend looks
   similar but names a CONDITION on a single series -- there is no second thing
   to fade there, so it stays a plain key rather than borrowing this because it
   was available.

   Every entry is a button, not a span, because hover is not available to
   everyone: activating one sticks the isolation, which is how a keyboard and a
   touch screen both reach it.

   Deliberately NOT wired to focus. Focus is sticky in a way hover is not --
   nothing guarantees a matching blur, so a button that merely receives focus
   (a restored page, a stray click) would leave the chart dimmed with no way to
   see why. Activation is the honest signal, and it is the one a button already
   reports through aria-pressed. */
export function SeriesLegend({
  items,
  active,
  sticky,
  onActivate,
  onToggleSticky,
}: {
  items: SeriesLegendItem[]
  active: number | null
  sticky: number | null
  onActivate: (seriesIndex: number | null) => void
  onToggleSticky: (seriesIndex: number) => void
}) {
  return (
    <div className="chart-legend">
      {items.map((item) => (
        <button
          key={item.label}
          type="button"
          className="item"
          // Pressed only describes the stuck state. A transient hover is not
          // state and should not be announced as though it were.
          aria-pressed={sticky === item.seriesIndex}
          onMouseEnter={() => onActivate(item.seriesIndex)}
          onMouseLeave={() => onActivate(null)}
          onClick={() => onToggleSticky(item.seriesIndex)}
        >
          <span
            className="swatch"
            style={{
              background: item.color,
              // The swatch dims with its series, so the key and the chart never
              // disagree about what is currently emphasised.
              opacity: active === null || active === item.seriesIndex ? 1 : 0.35,
            }}
          />
          {item.label}
        </button>
      ))}
    </div>
  )
}
