import { useState } from 'react'
import { IconChevron } from '@/components/icons'
import type { QuickFilter } from '@/lib/exceptionFilters'
import { CAUSE_LABELS, STATUS_LABELS } from '@/lib/labels'
import type { Cause, ExceptionStatus } from '@/lib/types'

const QUICK_CHIPS: [QuickFilter, string][] = [
  ['all', 'All'],
  ['backlog', 'Backlog'],
  ['needs_action', 'Needs Action'],
  ['pending', 'Processing'],
  ['resolved', 'Processed'],
]

const STATUS_ORDER: ExceptionStatus[] = ['open', 'pending', 'resolved', 'abandoned']
const CAUSE_ORDER: Cause[] = [
  'failed_payment',
  'fee_mismatch',
  'timing_lag',
  'duplicate',
  'unexplained',
  'refund_unmatched',
  'chargeback',
  'partial_payment',
]

export function ExceptionFilters({
  quickFilter,
  statusFilter,
  causeFilter,
  onQuickFilter,
  onStatusFilter,
  onCauseFilter,
}: {
  quickFilter: QuickFilter
  statusFilter: string
  causeFilter: string
  onQuickFilter: (q: QuickFilter) => void
  onStatusFilter: (s: string) => void
  onCauseFilter: (c: string) => void
}) {
  const [showAdvanced, setShowAdvanced] = useState(false)

  return (
    <>
      <div className="filter-row">
        {QUICK_CHIPS.map(([value, label]) => (
          <button
            key={value}
            className={
              // The chips and the status select are two views of one choice.
              // While the select is set it owns the answer, so no chip is lit
              // -- except "All", which is what an empty selection means.
              `chip${!statusFilter && quickFilter === value ? ' active' : ''}`
            }
            onClick={() => onQuickFilter(value)}
          >
            <span className="radio" />
            {label}
          </button>
        ))}
        <button
          className={`filter-more${showAdvanced ? ' open' : ''}`}
          aria-expanded={showAdvanced}
          onClick={() => setShowAdvanced((v) => !v)}
        >
          More filters
          <IconChevron />
        </button>
      </div>

      <div className="advanced-filters" hidden={!showAdvanced}>
        <select
          className="filter-select"
          aria-label="Filter by status"
          value={statusFilter}
          onChange={(e) => onStatusFilter(e.target.value)}
        >
          <option value="">Any status</option>
          {STATUS_ORDER.map((s) => (
            <option key={s} value={s}>
              {STATUS_LABELS[s]}
            </option>
          ))}
        </select>
        <select
          className="filter-select"
          aria-label="Filter by cause"
          value={causeFilter}
          onChange={(e) => onCauseFilter(e.target.value)}
        >
          <option value="">Any cause</option>
          {CAUSE_ORDER.map((c) => (
            <option key={c} value={c}>
              {CAUSE_LABELS[c]}
            </option>
          ))}
        </select>
      </div>
    </>
  )
}
