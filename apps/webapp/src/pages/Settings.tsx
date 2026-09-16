import { useState } from 'react'
import { apiDelete } from '../api'
import { CredentialsModal } from '../components/CredentialsModal'
import { useInvalidateAll, useMe } from '../queries'

export function Settings() {
  const { data: me, isLoading } = useMe()
  const invalidateAll = useInvalidateAll()
  const [showCreds, setShowCreds] = useState(false)
  const [deleting, setDeleting] = useState(false)

  async function handleDelete() {
    setDeleting(true)
    try {
      await apiDelete('/me/credentials')
      invalidateAll()
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="px-4 py-5">
      <h1 className="mb-4 text-[26px] font-normal leading-tight tracking-[-0.325px]" style={{ color: 'var(--ink)' }}>
        Настройки
      </h1>

      <div className="card p-4">
        <p className="mb-1 text-[16px] font-semibold" style={{ color: 'var(--ink)' }}>
          Аккаунт KBTU
        </p>
        <p className="mb-4 text-sm" style={{ color: 'var(--body)' }}>
          Пароль шифруется и используется только для входа на uninet.kbtu.kz от вашего имени. Мы не показываем и
          не передаём его никому.
        </p>

        {isLoading ? (
          <p style={{ color: 'var(--muted)' }}>Загрузка…</p>
        ) : (
          <>
            <p className="mb-3 flex items-center gap-2 text-sm" style={{ color: 'var(--body)' }}>
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{ background: me?.credentials_set ? 'var(--success)' : 'var(--muted)' }}
                aria-hidden
              />
              {me?.credentials_set ? me.email : 'Не задан'}
            </p>
            <div className="flex gap-2">
              <button onClick={() => setShowCreds(true)} className="btn-primary" style={{ height: 40 }}>
                {me?.credentials_set ? 'Изменить' : 'Войти'}
              </button>
              {me?.credentials_set && (
                <button onClick={handleDelete} disabled={deleting} className="btn-secondary" style={{ height: 40 }}>
                  {deleting ? 'Удаляем…' : 'Выйти'}
                </button>
              )}
            </div>
          </>
        )}
      </div>

      <CredentialsModal open={showCreds} onClose={() => setShowCreds(false)} />
    </div>
  )
}
