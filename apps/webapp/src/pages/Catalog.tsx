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
    <div className="px-4 py-4">
      <h1 className="mb-4 text-xl font-semibold text-[var(--tg-text)]">Каталог курсов</h1>

      {isLoading && <p className="text-[var(--tg-hint)]">Загрузка…</p>}
      {isError && <p className="text-red-400">Не удалось загрузить каталог.</p>}
      {data && data.length === 0 && (
        <p className="text-[var(--tg-hint)]">Каталог пока пуст, загляните позже.</p>
      )}

      <div className="grid grid-cols-2 gap-3">
        {data?.map((course) => (
          <Link
            key={course.course_id}
            to={`/courses/${course.course_id}`}
            className="flex flex-col overflow-hidden rounded-xl border border-[var(--tg-border)] bg-[var(--tg-secondary-bg)]"
          >
            <div className="aspect-video w-full bg-[var(--tg-border)]">
              {course.cover && (
                <img src={course.cover} alt="" className="h-full w-full object-cover" loading="lazy" />
              )}
            </div>
            <div className="flex flex-1 flex-col gap-1 p-3">
              <span className="line-clamp-2 text-sm font-medium text-[var(--tg-text)]">{course.title}</span>
              {course.lessons_count != null && (
                <span className="text-xs text-[var(--tg-hint)]">{course.lessons_count} уроков</span>
              )}
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}
