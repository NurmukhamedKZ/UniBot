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
    <div className="px-4 py-5">
      <h1 className="mb-4 text-[26px] font-normal leading-tight tracking-[-0.325px]" style={{ color: 'var(--ink)' }}>
        Профиль
      </h1>

      {isLoading && <p style={{ color: 'var(--muted)' }}>Загрузка…</p>}

      {me && (
        <>
          <div className="card mb-4 flex items-center justify-between p-4">
            <div>
              <p className="section-label mb-1">Баланс</p>
              <p className="num text-[28px] font-medium leading-none" style={{ color: 'var(--ink)' }}>
                {me.balance} <span className="text-base font-normal">кред.</span>
              </p>
            </div>
            <Link to="/paywall" className="btn-primary" style={{ height: 40, padding: '10px 16px' }}>
              Пополнить
            </Link>
          </div>

          {referral && (
            <div className="card mb-4 p-4">
              <p className="mb-1 text-[16px] font-semibold" style={{ color: 'var(--ink)' }}>
                Пригласить друзей
              </p>
              <p className="num mb-3 text-sm" style={{ color: 'var(--body)' }}>
                Приглашено: {referral.invited}. Начислено бонусов: {referral.credited}.
              </p>
              <button
                onClick={() => WebApp.openTelegramLink(referral.link)}
                className="btn-secondary"
                style={{ height: 40 }}
              >
                Поделиться ссылкой
              </button>
            </div>
          )}

          <div className="mb-5">
            <h2 className="section-label mb-2">История</h2>
            {me.history.length === 0 && <p style={{ color: 'var(--muted)' }}>Пока пусто.</p>}
            <ul>
              {me.history.map((entry) => (
                <li
                  key={entry.id}
                  className="flex items-center justify-between px-1 py-2.5 text-sm"
                  style={{ borderBottom: '1px solid var(--hairline-soft)' }}
                >
                  <span style={{ color: 'var(--ink)' }}>{REASON_LABELS[entry.reason] ?? entry.reason}</span>
                  <span className="num" style={{ color: entry.delta >= 0 ? 'var(--success)' : 'var(--muted)' }}>
                    {entry.delta >= 0 ? '+' : ''}
                    {entry.delta}
                  </span>
                </li>
              ))}
            </ul>
          </div>

          <div className="mb-4">
            <h2 className="section-label mb-2">Просмотрено</h2>
            {me.watched.length === 0 && <p style={{ color: 'var(--muted)' }}>Пока пусто.</p>}
            <ul>
              {me.watched.map((task) => (
                <li
                  key={task.id}
                  className="px-1 py-2.5 text-sm"
                  style={{ color: 'var(--body)', borderBottom: '1px solid var(--hairline-soft)' }}
                >
                  {task.type === 'course' ? 'Курс' : 'Урок'} {task.lesson_id ?? task.course_id} · {task.status}
                </li>
              ))}
            </ul>
          </div>

          <Link to="/settings" className="block text-center text-sm font-medium" style={{ color: 'var(--brand)' }}>
            Настройки аккаунта UniX
          </Link>
        </>
      )}
    </div>
  )
}
