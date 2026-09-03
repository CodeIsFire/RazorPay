import { useRef, useState } from 'react'
import { IconDownload } from '@/components/icons'
import { useToast } from '@/components/Toast'
import { UploadConfirm } from '@/components/data/UploadConfirm'
import { api, ApiError, UploadValidationError } from '@/lib/api'
import { fmtTs } from '@/lib/format'
import { useUploadData } from '@/lib/queries'
import type { UploadPreview, UploadProblem, UploadSource } from '@/lib/types'

/** One source's card: what it holds now, its template, and the upload. */
export function UploadCard({
  source,
  title,
  blurb,
  rows,
  updatedAt,
}: {
  source: UploadSource
  title: string
  blurb: string
  rows: number | undefined
  updatedAt: string | null | undefined
}) {
  const toast = useToast()
  const commit = useUploadData()
  const inputRef = useRef<HTMLInputElement>(null)

  const [file, setFile] = useState<File | null>(null)
  const [checking, setChecking] = useState(false)
  /* The two outcomes of a dry run, and they are mutually exclusive: a file
     either parses (and we can show its impact) or it doesn't (and we show
     why). Keeping both would let a stale error table sit under a confirm
     panel describing a different file. */
  const [problems, setProblems] = useState<UploadProblem[] | null>(null)
  const [preview, setPreview] = useState<UploadPreview | null>(null)

  function reset() {
    setFile(null)
    setPreview(null)
    setProblems(null)
    // The input keeps the old filename otherwise, and re-picking the same
    // file would then fire no change event at all.
    if (inputRef.current) inputRef.current.value = ''
  }

  /* Always run fresh against the file in hand -- never cached, never carried
     over from a previous attempt. The counts are what the user is agreeing
     to, and a stale one is worse than none. */
  async function check() {
    if (!file) return
    setChecking(true)
    setProblems(null)
    setPreview(null)
    try {
      setPreview(await api.uploadData(source, file, true))
    } catch (err) {
      if (err instanceof UploadValidationError) setProblems(err.problems)
      else toast(err instanceof ApiError ? err.message : `Couldn’t check ${title}`, true)
    } finally {
      setChecking(false)
    }
  }

  async function confirm() {
    if (!file) return
    try {
      // Re-sends the file. Nothing about the dry run is trusted: the server
      // validates again, and this call is the only one that writes.
      const result = await commit.mutateAsync({ source, file })
      toast(
        `${title} replaced — ${result.rows_loaded} rows loaded, ` +
          `${result.exceptions_deleted} exceptions cleared`,
      )
      reset()
    } catch (err) {
      toast(err instanceof ApiError ? err.message : `Couldn’t replace ${title}`, true)
      setPreview(null)
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2 className="title">{title}</h2>
          <div className="sub">{blurb}</div>
        </div>
        <a className="doc-link" href={api.templateUrl(source)} download>
          <IconDownload />
          Download {title.toLowerCase()} template
        </a>
      </div>

      <div className="card-body">
        <p className="upload-state">
          {rows === undefined
            ? 'Checking…'
            : rows === 0
              ? 'No rows uploaded yet.'
              : `${rows} row${rows === 1 ? '' : 's'} loaded${
                  updatedAt ? `, last updated ${fmtTs(updatedAt)}` : ''
                }.`}
        </p>

        <div className="upload-row">
          <input
            ref={inputRef}
            type="file"
            accept=".csv,text/csv"
            aria-label={`${title} CSV file`}
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null)
              // A new file makes the previous verdict meaningless.
              setProblems(null)
              setPreview(null)
            }}
          />
          <button
            className="btn"
            disabled={!file || checking || commit.isPending}
            onClick={check}
          >
            {checking ? 'Checking…' : `Upload ${title.toLowerCase()}`}
          </button>
        </div>

        {problems && (
          <div className="upload-problems">
            <p>
              Nothing was uploaded. {problems.length} problem
              {problems.length === 1 ? '' : 's'} in this file:
            </p>
            <table aria-label={`${title} upload problems`}>
              <thead>
                <tr>
                  <th>Row</th>
                  <th>Column</th>
                  <th>Problem</th>
                </tr>
              </thead>
              <tbody>
                {problems.map((p, i) => (
                  <tr key={`${p.row_number}-${p.column}-${i}`}>
                    {/* Row 0 is not a row -- it is the file or its header. */}
                    <td>{p.row_number === 0 ? 'Header' : p.row_number}</td>
                    <td className="mono">{p.column || '—'}</td>
                    <td>{p.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {preview && (
        <UploadConfirm
          title={title}
          preview={preview}
          committing={commit.isPending}
          onConfirm={confirm}
          onCancel={() => setPreview(null)}
        />
      )}
    </section>
  )
}
