import { useState } from 'react'
import { apiDelete } from '../api'
import { CredentialsModal } from '../components/CredentialsModal'
import { useMe, useInvalidateMe } from '../queries'

export function Settings() {
  const { data: me, isLoading } = useMe()
  const invalidateMe = useInvalidateMe()
  const [showCreds, setShowCreds] = useState(false)
  const [deleting, setDeleting] = useState(false)

  async function handleDelete() {
    setDeleting(true)
    try {
      await apiDelete('/me/unix-credentials')
      invalidateMe()
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
          Данные UniX
        </p>
        <p className="mb-4 text-sm" style={{ color: 'var(--body)' }}>
          Пароль шифруется (Fernet) и используется только для входа на uni-x.almv.kz от вашего имени
          во время автопросмотра. Мы не показываем и не передаём его никому.
        </p>

        {isLoading ? (
          <p style={{ color: 'var(--muted)' }}>Загрузка…</p>
        ) : (
          <>
            <p className="mb-3 flex items-center gap-2 text-sm" style={{ color: 'var(--body)' }}>
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{ background: me?.unix_email_set ? 'var(--success)' : 'var(--muted)' }}
                aria-hidden
              />
              {me?.unix_email_set ? 'Данные сохранены' : 'Не заданы'}
            </p>
            <div className="flex gap-2">
              <button onClick={() => setShowCreds(true)} className="btn-primary" style={{ height: 40 }}>
                {me?.unix_email_set ? 'Изменить' : 'Указать данные'}
              </button>
              {me?.unix_email_set && (
                <button onClick={handleDelete} disabled={deleting} className="btn-secondary" style={{ height: 40 }}>
                  {deleting ? 'Удаляем…' : 'Удалить'}
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
