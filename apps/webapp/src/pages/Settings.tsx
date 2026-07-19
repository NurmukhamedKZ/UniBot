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
    <div className="px-4 py-4">
      <h1 className="mb-4 text-xl font-semibold text-[var(--tg-text)]">Настройки</h1>

      <div className="mb-4 rounded-xl border border-[var(--tg-border)] bg-[var(--tg-secondary-bg)] p-4">
        <p className="mb-1 text-sm font-semibold text-[var(--tg-text)]">Данные UniX</p>
        <p className="mb-3 text-sm text-[var(--tg-hint)]">
          Пароль шифруется (Fernet) и используется только для входа на uni-x.almv.kz от вашего
          имени во время автопросмотра. Мы не показываем и не передаём его никому.
        </p>

        {isLoading ? (
          <p className="text-[var(--tg-hint)]">Загрузка…</p>
        ) : (
          <>
            <p className="mb-3 text-sm text-[var(--tg-text)]">
              Статус: {me?.unix_email_set ? 'данные сохранены' : 'не заданы'}
            </p>
            <div className="flex gap-2">
              <button
                onClick={() => setShowCreds(true)}
                className="rounded-lg bg-[var(--tg-button)] px-3 py-2 text-sm font-medium text-[var(--tg-button-text)]"
              >
                {me?.unix_email_set ? 'Изменить' : 'Указать данные'}
              </button>
              {me?.unix_email_set && (
                <button
                  onClick={handleDelete}
                  disabled={deleting}
                  className="rounded-lg border border-[var(--tg-border)] px-3 py-2 text-sm text-[var(--tg-text)] disabled:opacity-60"
                >
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
