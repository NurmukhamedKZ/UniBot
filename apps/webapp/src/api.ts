import { WebApp } from './telegram'

/**
 * Thrown for any non-2xx response. `body` is the parsed JSON error payload
 * (falls back to `{}` if the response wasn't JSON) -- callers match on
 * `status` + `body.error` for the specific cases the backend defines
 * (402 `no_credits`, 409 `no_credentials` / `task_already_active`, etc.),
 * per docs/plan.md's `/watch` contract.
 */
export class ApiError extends Error {
  status: number
  body: Record<string, unknown>

  constructor(status: number, body: Record<string, unknown>) {
    super(typeof body.error === 'string' ? body.error : `request failed with ${status}`)
    this.status = status
    this.body = body
  }
}

/** 402 `{error: "no_credits"}` -- balance is 0, show the paywall. */
export function isNoCredits(err: unknown): err is ApiError {
  return err instanceof ApiError && err.status === 402 && err.body.error === 'no_credits'
}

/** 409 `{error: "no_credentials"}` -- UniX creds not set yet, show the credentials modal. */
export function isNoCredentials(err: unknown): err is ApiError {
  return err instanceof ApiError && err.status === 409 && err.body.error === 'no_credentials'
}

/**
 * Fired for every failed request (ApiError from a non-2xx response, or a
 * network/CORS failure that never reached the backend at all) so a single
 * global listener (see ErrorToasts.tsx) can surface it, regardless of
 * whether the calling page bothers to check `isError` / catch the promise.
 */
export const apiErrorEvents = new EventTarget()

export function describeApiError(err: unknown): string {
  if (err instanceof ApiError) {
    const detail = err.body.detail
    if (typeof detail === 'string') return detail
    if (typeof err.body.error === 'string') return err.body.error
    return `Ошибка ${err.status}`
  }
  if (err instanceof Error) return err.message
  return 'Неизвестная ошибка'
}

/**
 * Fetch wrapper for the UniBot API. Always sends the raw Telegram
 * `initData` string so the backend's `current_user` dependency
 * (apps/api/auth.py) can HMAC-validate it and auto-create the user -- no
 * separate login step, per docs/spec.md's auth model.
 *
 * `path` is relative to `/api` (e.g. `/courses`, `/me`); the dev server
 * proxies that prefix to the API container (see vite.config.ts), and the
 * production nginx server-block does the same (docs/plan.md Task 2).
 */
export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('X-Telegram-Init-Data', WebApp.initData)
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  let res: Response
  try {
    res = await fetch(`/api${path}`, { ...init, headers })
  } catch (err) {
    const networkErr = new Error(
      `Сеть: не удалось связаться с сервером (${path}). ${err instanceof Error ? err.message : ''}`.trim(),
    )
    apiErrorEvents.dispatchEvent(new CustomEvent('error', { detail: networkErr.message }))
    throw networkErr
  }

  if (res.status === 204) return undefined as T

  let body: unknown = {}
  const text = await res.text()
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = { raw: text }
    }
  }

  if (!res.ok) {
    const apiErr = new ApiError(res.status, (body as Record<string, unknown>) ?? {})
    apiErrorEvents.dispatchEvent(
      new CustomEvent('error', { detail: `${path}: ${describeApiError(apiErr)}` }),
    )
    throw apiErr
  }

  return body as T
}

export function apiGet<T>(path: string): Promise<T> {
  return apiFetch<T>(path)
}

export function apiPost<T>(path: string, data?: unknown): Promise<T> {
  return apiFetch<T>(path, { method: 'POST', body: data !== undefined ? JSON.stringify(data) : undefined })
}

export function apiPut<T>(path: string, data?: unknown): Promise<T> {
  return apiFetch<T>(path, { method: 'PUT', body: data !== undefined ? JSON.stringify(data) : undefined })
}

export function apiDelete<T>(path: string): Promise<T> {
  return apiFetch<T>(path, { method: 'DELETE' })
}
