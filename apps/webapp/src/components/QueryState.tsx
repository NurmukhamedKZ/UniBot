import { useState } from 'react'
import { ApiError, isCredentialsError } from '../api'
import { CredentialsModal } from './CredentialsModal'

/** Общая заглушка для страниц, читающих uninet: загрузка / нет кредов / ошибка. */
export function QueryState({ isLoading, error }: { isLoading: boolean; error: unknown }) {
  const [open, setOpen] = useState(false)

  if (isLoading) return <p style={{ color: 'var(--muted)' }}>Загрузка…</p>
  if (isCredentialsError(error)) {
    const bad = error.body.error === 'bad_credentials'
    return (
      <div className="card p-4">
        <p className="mb-1 text-[16px] font-semibold" style={{ color: 'var(--ink)' }}>
          {bad ? 'uninet не принял ваш пароль' : 'Войдите через аккаунт KBTU'}
        </p>
        <p className="mb-4 text-sm" style={{ color: 'var(--body)' }}>
          {bad
            ? 'Возможно, пароль сменился. Введите актуальные данные.'
            : 'Этого курса в UniBot пока нет. Войдите через uninet.kbtu.kz — откроются все ваши курсы.'}
        </p>
        <button onClick={() => setOpen(true)} className="btn-primary w-full">
          Войти
        </button>
        <CredentialsModal open={open} onClose={() => setOpen(false)} />
      </div>
    )
  }
  if (error) {
    const down = error instanceof ApiError && error.status === 502
    return <p style={{ color: 'var(--error)' }}>{down ? 'uninet сейчас недоступен.' : 'Не удалось загрузить.'}</p>
  }
  return null
}
