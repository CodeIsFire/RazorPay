import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'

/** The dashboard refreshes itself every 15 seconds. These queries are mounted
    by the app shell rather than by the tab that displays them, because the
    vanilla dashboard polled everything regardless of which tab was open --
    mounting them per-tab would silently stop background tabs updating. */
export const POLL_MS = 15_000

export const queryKeys = {
  integrationStatus: ['integration-status'] as const,
  funnel: ['funnel'] as const,
  analyticsDaily: ['analytics', 'daily'] as const,
  analyticsExceptions: ['analytics', 'exceptions'] as const,
  audit: ['audit'] as const,
  exceptions: ['exceptions'] as const,
  exceptionDetail: (key: string) => ['exception-detail', key] as const,
}

const polled = { refetchInterval: POLL_MS } as const

export const useIntegrationStatus = () =>
  useQuery({ queryKey: queryKeys.integrationStatus, queryFn: api.integrationStatus, ...polled })

export const useFunnel = () =>
  useQuery({ queryKey: queryKeys.funnel, queryFn: api.funnel, ...polled })

export const useDaily = () =>
  useQuery({ queryKey: queryKeys.analyticsDaily, queryFn: api.analyticsDaily, ...polled })

export const useAnalyticsExceptions = () =>
  useQuery({
    queryKey: queryKeys.analyticsExceptions,
    queryFn: api.analyticsExceptions,
    ...polled,
  })

export const useAudit = () =>
  useQuery({ queryKey: queryKeys.audit, queryFn: () => api.audit(), ...polled })

export const useExceptions = () =>
  useQuery({ queryKey: queryKeys.exceptions, queryFn: () => api.exceptions(), ...polled })

/** A conflict detail describes a mismatch that already happened, so it never
    goes stale while the row is on screen. staleTime: Infinity is what makes
    re-expanding a row free -- the cache here replaces the hand-rolled
    detail-cache object the vanilla dashboard carried. */
export const useExceptionDetail = (key: string | null) =>
  useQuery({
    queryKey: queryKeys.exceptionDetail(key ?? ''),
    queryFn: () => api.exceptionDetail(key!),
    enabled: key !== null,
    staleTime: Infinity,
  })

/* Two different refresh scopes, matching what each action can actually change.
   Resolving or rechecking one exception moves that row, the recovered totals
   and the audit trail -- nothing else. A pipeline run can change everything. */
const ROW_ACTION_KEYS = [queryKeys.exceptions, queryKeys.funnel, queryKeys.audit]
const PIPELINE_KEYS = [
  queryKeys.exceptions,
  queryKeys.funnel,
  queryKeys.audit,
  queryKeys.analyticsDaily,
  queryKeys.analyticsExceptions,
  queryKeys.integrationStatus,
]

function useInvalidating<TArgs, TData>(
  mutationFn: (args: TArgs) => Promise<TData>,
  keys: readonly (readonly string[])[],
) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn,
    onSuccess: () => {
      for (const queryKey of keys) qc.invalidateQueries({ queryKey })
    },
  })
}

export const useReconcile = () =>
  useInvalidating(
    (actualSource: 'gateway' | 'bank_statement') => api.reconcile(actualSource),
    PIPELINE_KEYS,
  )

export const useRoute = () => useInvalidating(() => api.route(), PIPELINE_KEYS)

export const useSyncPayouts = () => useInvalidating(() => api.syncPayouts(), PIPELINE_KEYS)

export const useResolveException = () =>
  useInvalidating((key: string) => api.resolveException(key), ROW_ACTION_KEYS)

export const useRecheckException = () =>
  useInvalidating((key: string) => api.recheckException(key), ROW_ACTION_KEYS)

