import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { apiGet } from '../api'
import { useMe } from '../queries'
import { WebApp } from '../telegram'
import type { ReferralResponse } from '../types'

const REASON_LABELS: Record<string, string> = {
  purchase: 'Покупка',
  bonus: 'Бонус',
  referral: 'Реферальный бонус',
  watch: 'Автопросмотр',
}

export function Profile() {
  const { data: me, isLoading } = useMe()
  const { data: referral } = useQuery({
    queryKey: ['referral'],
    queryFn: () => apiGet<ReferralResponse>('/referral'),
  })

  return (
    <div className="px-4 py-4">
      <h1 className="mb-4 text-xl font-semibold text-[var(--tg-text)]">Профиль</h1>

      {isLoading && <p className="text-[var(--tg-hint)]">Загрузка…</p>}

      {me && (
        <>
          <div className="mb-4 flex items-center justify-between rounded-xl border border-[var(--tg-border)] bg-[var(--tg-secondary-bg)] p-4">
            <div>
              <p className="text-sm text-[var(--tg-hint)]">Баланс</p>
              <p className="text-2xl font-semibold text-[var(--tg-text)]">{me.balance} кредитов</p>
            </div>
            <Link
              to="/paywall"
              className="rounded-lg bg-[var(--tg-button)] px-3 py-2 text-sm font-medium text-[var(--tg-button-text)]"
            >
              Пополнить
            </Link>
          </div>

          {referral && (
            <div className="mb-4 rounded-xl border border-[var(--tg-border)] bg-[var(--tg-secondary-bg)] p-4">
              <p className="mb-1 text-sm font-semibold text-[var(--tg-text)]">Пригласить друзей</p>
              <p className="mb-3 text-sm text-[var(--tg-hint)]">
                Приглашено: {referral.invited} · Начислено бонусов: {referral.credited}
              </p>
              <button
                onClick={() => WebApp.openTelegramLink(referral.link)}
                className="rounded-lg border border-[var(--tg-border)] px-3 py-2 text-sm text-[var(--tg-text)]"
              >
                Поделиться ссылкой
              </button>
            </div>
          )}

          <div className="mb-4">
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-[var(--tg-hint)]">История</h2>
            {me.history.length === 0 && <p className="text-[var(--tg-hint)]">Пока пусто.</p>}
            <ul className="flex flex-col gap-1">
              {me.history.map((entry) => (
                <li
                  key={entry.id}
                  className="flex items-center justify-between rounded-lg border border-[var(--tg-border)] px-3 py-2 text-sm"
                >
                  <span className="text-[var(--tg-text)]">{REASON_LABELS[entry.reason] ?? entry.reason}</span>
                  <span className={entry.delta >= 0 ? 'text-green-400' : 'text-[var(--tg-hint)]'}>
                    {entry.delta >= 0 ? '+' : ''}
                    {entry.delta}
                  </span>
                </li>
              ))}
            </ul>
          </div>

          <div>
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-[var(--tg-hint)]">Просмотрено</h2>
            {me.watched.length === 0 && <p className="text-[var(--tg-hint)]">Пока пусто.</p>}
            <ul className="flex flex-col gap-1">
              {me.watched.map((task) => (
                <li key={task.id} className="rounded-lg border border-[var(--tg-border)] px-3 py-2 text-sm text-[var(--tg-text)]">
                  {task.type === 'course' ? 'Курс' : 'Урок'} {task.lesson_id ?? task.course_id} — {task.status}
                </li>
              ))}
            </ul>
          </div>

          <Link to="/settings" className="mt-4 block text-center text-sm text-[var(--tg-link)]">
            Настройки аккаунта UniX
          </Link>
        </>
      )}
    </div>
  )
}
