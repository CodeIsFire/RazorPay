export function fmtPaise(paise: number): string {
  return '₹' + (paise / 100).toLocaleString('en-IN', { maximumFractionDigits: 2 })
}

export function fmtPct(rate: number | null | undefined): string {
  return rate === null || rate === undefined ? '–' : (rate * 100).toFixed(1) + '%'
}

/** SQLite writes "2026-08-19 15:00:00" (naive UTC); transaction rows can also
    carry a full ISO string with its own offset. Only stamp a "Z" when there is
    no zone designator already. Every timestamp formatter goes through here so
    this rule lives in exactly one place. */
export function parseTs(ts: string | null | undefined): Date | null {
  if (!ts) return null
  const norm = String(ts).replace(' ', 'T')
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(norm) ? norm : norm + 'Z')
  return isNaN(d.getTime()) ? null : d
}

/** Fixed-width 24h clock for the activity stream: 8 characters, always, so the
    column beside it starts at the same place on every line. The date is in the
    line's tooltip -- these are the newest few events, and a repeated "Aug 29"
    on every row is noise in a stream this short. */
export function fmtLogTime(ts: string): string {
  const d = parseTs(ts)
  if (!d) return String(ts || '').slice(0, 8)
  return d.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

/** Seconds are noise everywhere except the Activity log, where ordering within
    a single pipeline run actually matters. */
export function fmtTs(ts: string | null | undefined, { seconds = false } = {}): string {
  if (!ts) return ''
  const d = parseTs(ts)
  if (!d) return ts
  const opts: Intl.DateTimeFormatOptions = {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }
  if (seconds) opts.second = '2-digit'
  return d.toLocaleString(undefined, opts)
}

/** Backend detail strings state amounts in raw paise ("short of the expected
    90000 paise by 30000 paise"). Rewrite those to rupees for display; the
    stored value is untouched.

    Older webhook_received rows stored the whole payload as a Python-repr blob
    ({'entity': 'event', ...}). Newer rows store a short line instead, but the
    historical blobs stay in the DB -- collapse them to the one id worth
    showing rather than rendering the noise. */
export function formatDetail(s: string | null | undefined): string {
  if (!s) return ''
  const str = String(s)
  if (/^\s*[[{]['"]/.test(str)) {
    const id = str.match(/['"](?:id|entity_id)['"]\s*:\s*['"]([^'"]+)['"]/)
    return id ? `payload · ${id[1]}` : 'payload received'
  }
  return str.replace(/(\d+)\s*paise\b/g, (_, p) => fmtPaise(Number(p)))
}
