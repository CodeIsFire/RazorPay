import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DataTab } from '@/components/data/DataTab'
import { ToastProvider } from '@/components/Toast'

/* Uploading replaces a whole source and deletes everything derived from it,
   including actions that may already have moved real money. So these tests
   are mostly about the gap between picking a file and committing to it: the
   dry run has to happen first, its numbers have to be on screen, and nothing
   may be written until someone says yes. */

const SUMMARY = {
  ledger: { rows: 120, updated_at: '2026-09-01 10:00:00' },
  bank_statement: { rows: 0, updated_at: null },
}

const PREVIEW = {
  rows_to_load: 2,
  rows_to_delete: 120,
  exceptions_to_delete: 7,
  actions_to_delete: 3,
  live_payouts_affected: 0,
}

const RESULT = {
  rows_loaded: 2,
  rows_deleted: 120,
  exceptions_deleted: 7,
  actions_deleted: 3,
}

let fetchMock: ReturnType<typeof vi.fn>

type Responses = {
  preview?: unknown
  previewStatus?: number
  commitStatus?: number
}

function mount({ preview = PREVIEW, previewStatus = 200, commitStatus = 200 }: Responses = {}) {
  fetchMock = vi.fn(async (url: string) => {
    const u = String(url)
    if (u.includes('/data/summary')) {
      return new Response(JSON.stringify(SUMMARY), { status: 200 })
    }
    if (u.includes('/data/upload')) {
      return u.includes('dry_run=true')
        ? new Response(JSON.stringify(preview), { status: previewStatus })
        : new Response(JSON.stringify(RESULT), { status: commitStatus })
    }
    return new Response(JSON.stringify({}), { status: 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <DataTab />
      </ToastProvider>
    </QueryClientProvider>,
  )
}

const uploadCalls = (dryRun: boolean) =>
  fetchMock.mock.calls.filter(([url]) => {
    const u = String(url)
    return u.includes('/data/upload') && u.includes(`dry_run=${dryRun}`)
  })

const csv = (name = 'ledger.csv') =>
  new File(['external_ref,amount_paise,occurred_at\nORD-1,100,2026-09-01\n'], name, {
    type: 'text/csv',
  })

async function pickAndUpload(user: ReturnType<typeof userEvent.setup>) {
  await user.upload(screen.getByLabelText(/ledger csv file/i), csv())
  await user.click(screen.getByRole('button', { name: /^upload ledger$/i }))
}

afterEach(() => vi.unstubAllGlobals())

describe('the Data tab shows what each source currently holds', () => {
  it('names a card per uploadable source, with its row count', async () => {
    mount()
    expect(await screen.findByText(/120 rows/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /^ledger$/i })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /^bank statement$/i })).toBeInTheDocument()
  })

  it('says a source is empty rather than showing a bare zero', async () => {
    mount()
    expect(await screen.findByText(/no rows uploaded yet/i)).toBeInTheDocument()
  })

  it('offers each template as a download from the endpoint that generates it', async () => {
    mount()
    const link = await screen.findByRole('link', { name: /download ledger template/i })
    expect(link).toHaveAttribute('href', '/data/templates/ledger')
    expect(link).toHaveAttribute('download')
  })
})

describe('nothing is written until the impact is shown and accepted', () => {
  it('will not upload before a file is chosen', async () => {
    mount()
    expect(await screen.findByRole('button', { name: /^upload ledger$/i })).toBeDisabled()
  })

  it('dry-runs first and shows what the replace would destroy', async () => {
    const user = userEvent.setup()
    mount()
    await pickAndUpload(user)

    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText(/120/)).toBeInTheDocument()
    expect(within(dialog).getByText(/7/)).toBeInTheDocument()
    expect(uploadCalls(true)).toHaveLength(1)
    expect(uploadCalls(false)).toHaveLength(0)
  })

  it('commits only after the confirmation is accepted', async () => {
    const user = userEvent.setup()
    mount()
    await pickAndUpload(user)
    await user.click(await screen.findByRole('button', { name: /replace/i }))

    await waitFor(() => expect(uploadCalls(false)).toHaveLength(1))
    // The toast region is always mounted, so wait for its text, not for it.
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(/2 rows loaded/i),
    )
  })

  it('writes nothing when the confirmation is cancelled', async () => {
    const user = userEvent.setup()
    mount()
    await pickAndUpload(user)
    await user.click(await screen.findByRole('button', { name: /cancel/i }))

    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(uploadCalls(false)).toHaveLength(0)
  })

  it('re-sends the file rather than trusting the dry run it just did', async () => {
    const user = userEvent.setup()
    mount()
    await pickAndUpload(user)
    await user.click(await screen.findByRole('button', { name: /replace/i }))

    await waitFor(() => expect(uploadCalls(false)).toHaveLength(1))
    const body = uploadCalls(false)[0][1]?.body as FormData
    expect(body).toBeInstanceOf(FormData)
    expect((body.get('file') as File).name).toBe('ledger.csv')
  })

  it('says out loud when the replace would delete dispatched payouts', async () => {
    // The one case where this is not a routine data swap: those actions are
    // the only record this app holds of money that actually moved.
    const user = userEvent.setup()
    mount({ preview: { ...PREVIEW, live_payouts_affected: 2 } })
    await pickAndUpload(user)

    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText(/2 dispatched payout/i)).toBeInTheDocument()
  })
})

describe('a file that will not parse is explained, not just refused', () => {
  it('lists every bad row with its column and reason, and asks for nothing', async () => {
    const user = userEvent.setup()
    mount({
      previewStatus: 422,
      preview: {
        detail: '2 problem(s) in the uploaded file',
        errors: [
          { row_number: 1, column: 'amount_paise', message: 'must be a whole number of paise' },
          { row_number: 2, column: 'occurred_at', message: 'must be an ISO 8601 date or datetime' },
        ],
      },
    })
    await pickAndUpload(user)

    const table = await screen.findByRole('table', { name: /problems/i })
    expect(within(table).getByText('amount_paise')).toBeInTheDocument()
    expect(within(table).getByText(/must be an ISO 8601 date/i)).toBeInTheDocument()
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(uploadCalls(false)).toHaveLength(0)
  })

  it('labels a header-level problem as the header rather than row 0', async () => {
    const user = userEvent.setup()
    mount({
      previewStatus: 422,
      preview: {
        errors: [
          { row_number: 0, column: 'amount_paise', message: 'required column is missing' },
        ],
      },
    })
    await pickAndUpload(user)

    const table = await screen.findByRole('table', { name: /problems/i })
    expect(within(table).getByText(/header/i)).toBeInTheDocument()
  })

  it('reports a server failure as a failure', async () => {
    const user = userEvent.setup()
    mount({ previewStatus: 500, preview: { detail: 'boom' } })
    await pickAndUpload(user)

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/boom/i))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })
})
