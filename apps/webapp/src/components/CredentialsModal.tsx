import { useState, type FormEvent } from 'react'
import { ApiError, apiPut, isCredentialsError } from '../api'
import { useInvalidateAll } from '../queries'

interface CredentialsModalProps {
  open: boolean
  onClose: () => void
}

/** Ввод логина/пароля KBTU. Бэкенд сразу проверяет их входом в uninet. */
export function CredentialsModal({ open, onClose }: CredentialsModalProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const invalidateAll = useInvalidateAll()

  if (!open) return null

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      await apiPut('/me/credentials', { email, password })
      setEmail('')
      setPassword('')
      invalidateAll()
      onClose()
    } catch (err) {
      if (isCredentialsError(err)) setError('Неверный логин или пароль KBTU.')
      else if (err instanceof ApiError && err.status === 502) setError('uninet сейчас недоступен, попробуйте позже.')
      else setError('Не удалось сохранить. Попробуйте снова.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center">
      <div
        className="w-full max-w-sm p-5 sm:rounded-[12px]"
        style={{ background: 'var(--card)', borderRadius: '12px 12px 0 0', borderTop: '1px solid var(--hairline)' }}
      >
        <h2 className="text-[18px] font-semibold" style={{ color: 'var(--ink)' }}>
          Вход KBTU
        </h2>
        <p className="mt-1 text-sm" style={{ color: 'var(--body)' }}>
          Тот же логин и пароль, что на uninet.kbtu.kz. Пароль хранится зашифрованным и используется только
          для входа на uninet от вашего имени.
        </p>
        <form className="mt-4 flex flex-col gap-3" onSubmit={handleSubmit}>
          <div className="flex flex-col gap-2">
            <label className="text-sm" style={{ color: 'var(--body)' }} htmlFor="kbtu-email">
              Email
            </label>
            <input
              id="kbtu-email"
              type="email"
              required
              autoComplete="username"
              placeholder="a_student@kbtu.kz"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="field"
            />
          </div>
          <div className="flex flex-col gap-2">
            <label className="text-sm" style={{ color: 'var(--body)' }} htmlFor="kbtu-password">
              Пароль
            </label>
            <input
              id="kbtu-password"
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="field"
            />
          </div>
          {error && (
            <p className="text-sm" style={{ color: 'var(--error)' }}>
              {error}
            </p>
          )}
          <div className="mt-2 flex gap-2">
            <button type="button" onClick={onClose} className="btn-secondary flex-1">
              Отмена
            </button>
            <button type="submit" disabled={saving} className="btn-primary flex-1">
              {saving ? 'Проверяем…' : 'Войти'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
