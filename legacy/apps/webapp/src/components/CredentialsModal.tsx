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
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center">
      <div
        className="w-full max-w-sm p-5 sm:rounded-[12px]"
        style={{ background: 'var(--card)', borderRadius: '12px 12px 0 0', borderTop: '1px solid var(--hairline)' }}
      >
        <h2 className="text-[18px] font-semibold" style={{ color: 'var(--ink)' }}>
          Данные UniX
        </h2>
        <p className="mt-1 text-sm" style={{ color: 'var(--body)' }}>
          Логин и пароль от uni-x.almv.kz нужны для автопросмотра от вашего имени. Пароль хранится в
          зашифрованном виде и используется только для входа на UniX.
        </p>
        <form className="mt-4 flex flex-col gap-3" onSubmit={handleSubmit}>
          <div className="flex flex-col gap-2">
            <label className="text-sm" style={{ color: 'var(--body)' }} htmlFor="unix-email">
              Email
            </label>
            <input
              id="unix-email"
              type="email"
              required
              placeholder="email@university.kz"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="field"
            />
          </div>
          <div className="flex flex-col gap-2">
            <label className="text-sm" style={{ color: 'var(--body)' }} htmlFor="unix-password">
              Пароль
            </label>
            <input
              id="unix-password"
              type="password"
              required
              placeholder="Ваш пароль от UniX"
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
              {saving ? 'Сохраняем…' : 'Сохранить'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
