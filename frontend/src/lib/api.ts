import type {
  AuditResponse,
  ChatResponse,
  ChatTurn,
  DailyResponse,
  DataSummary,
  DemoResetResult,
  ExceptionDetail,
  ExceptionIntelligence,
  ExceptionsResponse,
  Funnel,
  IntegrationStatus,
  ReconcileResult,
  RoutePreview,
  RouteResult,
  SyncPayoutsResult,
  UploadPreview,
  UploadProblem,
  UploadResult,
  UploadSource,
} from './types'

/* Paths are relative on purpose: in production FastAPI serves this build from
   its own origin, and in dev vite.config.ts proxies these prefixes to :8000.
   Either way the browser makes same-origin requests, which is why the backend
   needs no CORS configuration. */

/** FastAPI reports every error as {"detail": string}, so that string is what
    reaches the UI -- the backend's messages are already written for a reader
    ("recheck only applies to timing_lag exceptions", "That question is too
    long"), and replacing them with a generic message would lose that. */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
  }
}

/** A 422 from the upload endpoints, carrying the per-row problems the file
    was rejected for. It extends ApiError so every existing catch that expects
    one still works -- `detail` is the summary line, `problems` is the table.
    Nothing else in the API answers this shape, which is why this is the only
    error class with structure beyond a message. */
export class UploadValidationError extends ApiError {
  readonly problems: UploadProblem[]

  constructor(detail: string, problems: UploadProblem[]) {
    super(422, detail)
    this.name = 'UploadValidationError'
    this.problems = problems
  }
}


async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: init?.body
      ? { 'Content-Type': 'application/json', ...init?.headers }
      : init?.headers,
  })
  if (!res.ok) {
    const detail = await res
      .json()
      .then((body: { detail?: string }) => body.detail)
      .catch(() => undefined)
    throw new ApiError(res.status, detail ?? res.statusText)
  }
  return res.json() as Promise<T>
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
  })

export const api = {
  integrationStatus: () => request<IntegrationStatus>('/integration/status'),
  funnel: () => request<Funnel>('/funnel'),
  analyticsExceptions: () => request<ExceptionIntelligence>('/analytics/exceptions'),
  analyticsDaily: () => request<DailyResponse>('/analytics/daily'),
  audit: (limit = 200) => request<AuditResponse>(`/audit?limit=${limit}`),

  /* Always fetched unfiltered. Status, cause, search and sort are all applied
     client-side over this one array, exactly as the vanilla dashboard did --
     the filters are instant and the export stays able to see every row. */
  exceptions: (limit = 500) => request<ExceptionsResponse>(`/exceptions?limit=${limit}`),
  exceptionDetail: (key: string) =>
    request<ExceptionDetail>(`/exceptions/${encodeURIComponent(key)}/detail`),

  reconcile: (actualSource: 'gateway' | 'bank_statement' = 'gateway') =>
    post<ReconcileResult>(`/pipeline/reconcile?actual_source=${actualSource}`),
  route: () => post<RouteResult>('/pipeline/route'),
  routePreview: () => request<RoutePreview>('/pipeline/route/preview'),
  syncPayouts: () => post<SyncPayoutsResult>('/pipeline/sync-payouts'),

  resolveException: (key: string, note = 'resolved from dashboard') =>
    post<{ exception_key: string; status: 'resolved' }>(
      `/exceptions/${encodeURIComponent(key)}/resolve`,
      { note },
    ),
  recheckException: (key: string) =>
    post<{ exception_key: string; status: 'resolved' }>(
      `/exceptions/${encodeURIComponent(key)}/recheck`,
    ),


  /** Downloaded by the browser directly, as a plain link -- the response is
      an attachment, so there is nothing for fetch() to do with it. */
  templateUrl: (source: UploadSource) => `/data/templates/${source}`,

  dataSummary: () => request<DataSummary>('/data/summary'),

  /** Clears everything and reloads the fixed demo dataset, reconciled.
      Destructive -- the caller confirms first. */
  resetDemo: () => post<DemoResetResult>('/demo/reset'),

  /* Deliberately not routed through request(): that sets a JSON content-type
     whenever there is a body, and a multipart POST must be left alone so the
     browser can write its own boundary. */
  uploadData: async (source: UploadSource, file: File, dryRun: boolean) => {
    const body = new FormData()
    body.append('file', file)
    const res = await fetch(`/data/upload/${source}?dry_run=${dryRun}`, { method: 'POST', body })
    const payload = await res.json().catch(() => ({}) as Record<string, unknown>)
    if (res.ok) return payload as UploadPreview & UploadResult
    const detail = typeof payload.detail === 'string' ? payload.detail : res.statusText
    if (res.status === 422 && Array.isArray(payload.errors)) {
      throw new UploadValidationError(detail, payload.errors as UploadProblem[])
    }
    throw new ApiError(res.status, detail)
  },

  /** POST {message, history} -- the question and the transcript are separate
      fields, and the server re-validates and caps the history it is given. */
  chat: (message: string, history: ChatTurn[]) =>
    post<ChatResponse>('/assistant/chat', { message, history }),
}
