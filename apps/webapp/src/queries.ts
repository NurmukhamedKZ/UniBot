import { useQuery, useQueryClient, type Query } from '@tanstack/react-query'
import { apiGet } from './api'
import type { MeResponse, WatchTask } from './types'

export const ME_KEY = ['me'] as const
export const ACTIVE_TASK_KEY = ['activeTask'] as const

export function useMe() {
  return useQuery({ queryKey: ME_KEY, queryFn: () => apiGet<MeResponse>('/me') })
}

/**
 * `GET /tasks/active` returns 204 (empty body -> undefined via apiFetch) when
 * there's no active task. Poll every 5s only while a task is active, per
 * Task 16 brief -- idle users don't hammer the endpoint. Any successful
 * `POST /watch` should invalidate ACTIVE_TASK_KEY to restart polling
 * immediately instead of waiting for the next natural refetch.
 */
export function useActiveTask() {
  return useQuery({
    queryKey: ACTIVE_TASK_KEY,
    queryFn: () => apiGet<WatchTask | undefined>('/tasks/active'),
    refetchInterval: (query: Query<WatchTask | undefined>) => (query.state.data ? 5000 : false),
  })
}

export function useInvalidateActiveTask() {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: ACTIVE_TASK_KEY })
}

export function useInvalidateMe() {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: ME_KEY })
}
