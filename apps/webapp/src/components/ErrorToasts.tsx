import { useEffect, useRef, useState } from 'react'
import { apiErrorEvents } from '../api'

interface Toast {
  id: number
  message: string
}

const AUTO_DISMISS_MS = 6000

/**
 * Listens for every failed API call (apiErrorEvents, dispatched from
 * apiFetch) and renders it as a dismissible toast. Catches errors from
 * plain `useQuery` calls (useMe, courses, referral, etc.) that don't render
 * their own `isError` state, and network failures that never reach the
 * backend at all -- both were previously silent.
 */
export function ErrorToasts() {
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(0)

  useEffect(() => {
    function onError(e: Event) {
      const message = (e as CustomEvent<string>).detail
      const id = nextId.current++
      setToasts((prev) => [...prev, { id, message }])
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id))
      }, AUTO_DISMISS_MS)
    }
    apiErrorEvents.addEventListener('error', onError)
    return () => apiErrorEvents.removeEventListener('error', onError)
  }, [])

  if (toasts.length === 0) return null

  return (
    <div className="fixed left-0 right-0 top-2 z-[100] flex flex-col items-center gap-2 px-4">
      {toasts.map((t) => (
        <div
          key={t.id}
          className="flex w-full max-w-sm items-start gap-2 rounded-lg border border-red-400/40 bg-red-950/90 px-3 py-2 text-sm text-red-100 shadow-lg"
        >
          <span className="flex-1 break-words">{t.message}</span>
          <button
            onClick={() => setToasts((prev) => prev.filter((x) => x.id !== t.id))}
            className="shrink-0 text-red-200/70 hover:text-red-100"
            aria-label="Закрыть"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  )
}
