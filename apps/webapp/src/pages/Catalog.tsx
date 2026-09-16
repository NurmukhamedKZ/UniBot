import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { apiGet } from '../api'
import { CredentialsModal } from '../components/CredentialsModal'
import { QueryState } from '../components/QueryState'
import { useMe } from '../queries'
import type { Course } from '../types'

export function Catalog() {
  const { data: me } = useMe()
  const anonymous = me?.credentials_set === false
  const [loginOpen, setLoginOpen] = useState(false)
  const { data, isLoading, error } = useQuery({
    queryKey: ['courses'],
    queryFn: () => apiGet<Course[]>('/courses'),
  })

  return (
    <div className="px-4 py-5">
      <h1 className="mb-1 text-[26px] font-normal leading-tight tracking-[-0.325px]" style={{ color: 'var(--ink)' }}>
        {anonymous ? 'Курсы' : 'Мои курсы'}
      </h1>
      <p className="mb-5 text-sm" style={{ color: 'var(--body)' }}>
        {anonymous
          ? 'Готовые транскрибы можно копировать без входа.'
          : 'Курсы текущего семестра с uninet.kbtu.kz.'}
      </p>

      {anonymous && (
        <div className="card mb-5 p-4">
          <p className="mb-1 text-[16px] font-semibold" style={{ color: 'var(--ink)' }}>
            Нет вашего курса?
          </p>
          <p className="mb-4 text-sm" style={{ color: 'var(--body)' }}>
            Войдите через аккаунт KBTU — появятся все ваши курсы, автопросмотр и новые транскрибы.
          </p>
          <button onClick={() => setLoginOpen(true)} className="btn-primary w-full">
            Войти
          </button>
          <CredentialsModal open={loginOpen} onClose={() => setLoginOpen(false)} />
        </div>
      )}

      <QueryState isLoading={isLoading} error={error} />
      {data && data.length === 0 && !anonymous && <p style={{ color: 'var(--muted)' }}>В этом семестре курсов нет.</p>}

      <div className="flex flex-col gap-3">
        {data?.map((course) => (
          <Link key={course.id} to={`/courses/${course.id}`} className="card flex flex-col gap-1 p-4 active:scale-[0.99]">
            <span className="num text-xs" style={{ color: 'var(--muted)' }}>
              {course.code}
            </span>
            <span className="text-[15px] font-medium" style={{ color: 'var(--ink)' }}>
              {course.name}
            </span>
            <span className="text-xs" style={{ color: 'var(--body)' }}>
              {course.teacher}
            </span>
          </Link>
        ))}
      </div>
    </div>
  )
}
