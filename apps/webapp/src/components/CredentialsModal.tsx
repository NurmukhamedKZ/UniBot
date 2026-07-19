import { useState, type FormEvent } from 'react'
import { apiPut } from '../api'
import { useInvalidateMe } from '../queries'

interface CredentialsModalProps {
  open: boolean
  onClose: () => void
  /** Called after credentials are saved successfully. */
  onSaved?: () => void
}

/**
 * Shared modal for entering UniX login/password. Used by Settings (manual
 * edit) and opened automatically by Lesson/Course when POST /watch 409s
 * with no_credentials.
 */
export function CredentialsModal({ open, onClose, onSaved }: CredentialsModalProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const invalidateMe = useInvalidateMe()

  if (!open) return null

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      await apiPut('/me/unix-credentials', { email, password })
      invalidateMe()
      setEmail('')
      setPassword('')
      onSaved?.()
      onClose()
    } catch {
      setError('Не удалось сохранить. Проверьте данные и попробуйте снова.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 sm:items-center">
      <div className="w-full max-w-sm rounded-t-2xl border border-[var(--tg-border)] bg-[var(--tg-secondary-bg)] p-5 sm:rounded-2xl">
        <h2 className="text-lg font-semibold">Данные UniX</h2>
        <p className="mt-1 text-sm text-[var(--tg-hint)]">
          Логин и пароль от uni-x.almv.kz нужны для автопросмотра от вашего имени. Пароль хранится
          в зашифрованном виде и используется только для входа на UniX.
        </p>
        <form className="mt-4 flex flex-col gap-3" onSubmit={handleSubmit}>
          <input
            type="email"
            required
            placeholder="Email на UniX"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="rounded-lg border border-[var(--tg-border)] bg-[var(--tg-bg)] px-3 py-2 text-[var(--tg-text)] outline-none"
          />
          <input
            type="password"
            required
            placeholder="Пароль"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="rounded-lg border border-[var(--tg-border)] bg-[var(--tg-bg)] px-3 py-2 text-[var(--tg-text)] outline-none"
          />
          {error && <p className="text-sm text-red-400">{error}</p>}
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 rounded-lg border border-[var(--tg-border)] py-2 text-[var(--tg-text)]"
            >
              Отмена
            </button>
            <button
              type="submit"
              disabled={saving}
              className="flex-1 rounded-lg bg-[var(--tg-button)] py-2 font-medium text-[var(--tg-button-text)] disabled:opacity-60"
            >
              {saving ? 'Сохраняем…' : 'Сохранить'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
