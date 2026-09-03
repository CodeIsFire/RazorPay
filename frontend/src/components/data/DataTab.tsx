import { LoadFailed } from '@/components/LoadFailed'
import { PromptTerminal } from '@/components/data/PromptTerminal'
import { UploadCard } from '@/components/data/UploadCard'
import { useDataSummary } from '@/lib/queries'

/* Load your own data instead of the generated fixture.

   Only the two sides a user actually holds a file for. The third source,
   'gateway', is RazorpayX's own record and arrives over the API or a
   webhook -- there is nothing to upload, which is why there is no third
   card here. */
const CARDS = [
  {
    source: 'ledger' as const,
    title: 'Ledger',
    blurb:
      'What you expected to pay — the side reconciliation measures everything else against.',
  },
  {
    source: 'bank_statement' as const,
    title: 'Bank statement',
    blurb:
      'What your bank says actually left the account. Reconciled against the ledger as an independent second opinion.',
  },
]

export function DataTab() {
  const summary = useDataSummary()

  return (
    <>
      <p className="page-desc">
        Upload your own ledger and bank statement as CSV. Each upload replaces that source
        entirely and clears the exceptions and actions derived from it — download the template
        first if you’re unsure of the columns.
      </p>

      {/* The cards still render underneath: the upload itself works whether
          or not the current row counts could be read. */}
      {summary.isError && (
        <LoadFailed
          what="the current row counts"
          error={summary.error}
          onRetry={() => summary.refetch()}
          retrying={summary.isFetching}
        />
      )}

      {CARDS.map((card) => (
        <UploadCard
          key={card.source}
          source={card.source}
          title={card.title}
          blurb={card.blurb}
          rows={summary.data?.[card.source].rows}
          updatedAt={summary.data?.[card.source].updated_at}
        />
      ))}

      {/* Below both cards deliberately: the upload is the job, this is the
          thing you reach for only once, to get a file worth uploading. */}
      <PromptTerminal />
    </>
  )
}
