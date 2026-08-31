import type {
  AuditResponse,
  ChatResponse,
  ChatTurn,
  DailyResponse,
  ExceptionDetail,
  ExceptionIntelligence,
  ExceptionsResponse,
  Funnel,
  IntegrationStatus,
  ReconcileResult,
  RoutePreview,
  RouteResult,
  SyncPayoutsResult,
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


  /** POST {message, history} -- the question and the transcript are separate
      fields, and the server re-validates and caps the history it is given. */
  chat: (message: string, history: ChatTurn[]) =>
    post<ChatResponse>('/assistant/chat', { message, history }),
}
