import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { apiGet } from '../api'
import type { Course } from '../types'

export function Catalog() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['courses'],
    queryFn: () => apiGet<Course[]>('/courses'),
  })

  return (
    <div className="px-4 py-5">
      <h1 className="mb-1 text-[26px] font-normal leading-tight tracking-[-0.325px]" style={{ color: 'var(--ink)' }}>
        Каталог курсов
      </h1>
      <p className="mb-5 text-sm" style={{ color: 'var(--body)' }}>
        Транскрибы и ответы бесплатны. Кредит нужен только для автопросмотра.
      </p>

      {isLoading && <p style={{ color: 'var(--muted)' }}>Загрузка…</p>}
      {isError && <p style={{ color: 'var(--error)' }}>Не удалось загрузить каталог.</p>}
      {data && data.length === 0 && <p style={{ color: 'var(--muted)' }}>Каталог пока пуст, загляните позже.</p>}

      <div className="grid grid-cols-2 gap-3">
        {data?.map((course) => (
          <Link key={course.course_id} to={`/courses/${course.course_id}`} className="card active:scale-[0.98]">
            <div
              className="w-full overflow-hidden rounded-t-[11px]"
              style={{ aspectRatio: '16 / 10', background: 'var(--surface-strong)' }}
            >
              {course.cover && (
                <img src={course.cover} alt="" className="h-full w-full object-cover" loading="lazy" />
              )}
            </div>
            <div className="flex flex-1 flex-col gap-1 p-3">
              <span className="line-clamp-2 text-sm font-medium" style={{ color: 'var(--ink)' }}>
                {course.title}
              </span>
              {course.lessons_count != null && (
                <span className="num text-xs" style={{ color: 'var(--muted)' }}>
                  {course.lessons_count} уроков
                </span>
              )}
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}
