import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet } from './api'
import type { Me } from './types'

export const ME_KEY = ['me'] as const

export function useMe() {
  return useQuery({ queryKey: ME_KEY, queryFn: () => apiGet<Me>('/me') })
}

/** После смены кредов всё, что читалось из uninet, надо перечитать. */
export function useInvalidateAll() {
  const qc = useQueryClient()
  return () => qc.invalidateQueries()
}
