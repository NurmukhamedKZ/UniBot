import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { apiGet, apiPost } from '../api'
import { QueryState } from '../components/QueryState'
import type { Course, CourseDetail } from '../types'

export function CoursePage() {
  const { id } = useParams<{ id: string }>()
  const qc = useQueryClient()
  const [watching, setWatching] = useState(false)
  const [result, setResult] = useState<string | null>(null)

  const { data: courses } = useQuery({ queryKey: ['courses'], queryFn: () => apiGet<Course[]>('/courses') })
  const course = courses?.find((c) => String(c.id) === id)

  const { data, isLoading, error } = useQuery({
    queryKey: ['course', id],
    queryFn: () => apiGet<CourseDetail>(`/courses/${id}`),
    enabled: !!id,
  })

  async function watchCourse() {
    setWatching(true)
    setResult(null)
    try {
      const res = await apiPost<{ marked: number }>(`/courses/${id}/watch`)
      setResult(res.marked ? `Готово: отмечено ${res.marked} видео.` : 'Все открытые видео уже просмотрены.')
      qc.invalidateQueries({ queryKey: ['week'] })
    } catch {
      setResult(null)
    } finally {
      setWatching(false)
    }
  }

  return (
    <div className="px-4 py-5">
      <h1 className="mb-1 text-[26px] font-normal leading-tight tracking-[-0.325px]" style={{ color: 'var(--ink)' }}>
        {course?.name ?? 'Курс'}
      </h1>
      {course && (
        <p className="num mb-4 text-sm" style={{ color: 'var(--body)' }}>
          {course.code} · {course.teacher}
        </p>
      )}

      <QueryState isLoading={isLoading} error={error} />

      {data && (
        <>
          <div className="mb-5 flex flex-col gap-2">
            <button onClick={watchCourse} disabled={watching} className="btn-primary w-full">
              {watching ? 'Отмечаем…' : 'Просмотреть все открытые недели'}
            </button>
            {result && (
              <p className="text-sm" style={{ color: 'var(--success)' }}>
                {result}
              </p>
            )}
          </div>

          <ul className="flex flex-col">
            {data.weeks.map((week) => (
              <li key={week.id} style={{ borderBottom: '1px solid var(--hairline-soft)' }}>
                {week.unlocked ? (
                  <Link
                    to={`/courses/${id}/weeks/${week.id}`}
                    className="flex items-center justify-between gap-3 rounded-lg px-1 py-3 active:bg-[var(--canvas-soft)]"
                  >
                    <WeekRow num={week.weekNumber} theme={week.theme} current={week.current} />
                  </Link>
                ) : (
                  <div className="flex items-center justify-between gap-3 px-1 py-3 opacity-50">
                    <WeekRow num={week.weekNumber} theme={week.theme} />
                    <span className="text-xs" style={{ color: 'var(--muted)' }}>
                      откроется {new Date(week.unlockDate).toLocaleDateString('ru-RU')}
                    </span>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

function WeekRow({ num, theme, current }: { num: number; theme: string; current?: boolean }) {
  return (
    <span className="flex items-center gap-2 text-sm" style={{ color: 'var(--ink)' }}>
      <span className="num" style={{ color: 'var(--muted)' }}>
        {num}.
      </span>
      {theme}
      {current && <span className="badge-pill">сейчас</span>}
    </span>
  )
}
