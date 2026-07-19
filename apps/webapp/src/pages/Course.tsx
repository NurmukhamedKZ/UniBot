import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { apiGet, apiPost, isNoCredentials, isNoCredits, ApiError } from '../api'
import { CredentialsModal } from '../components/CredentialsModal'
import { useInvalidateActiveTask } from '../queries'
import type { Course, CourseLesson } from '../types'

export function CoursePage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const invalidateActiveTask = useInvalidateActiveTask()
  const [showCreds, setShowCreds] = useState(false)
  const [starting, setStarting] = useState(false)
  const [inlineError, setInlineError] = useState<string | null>(null)

  const { data: courses } = useQuery({ queryKey: ['courses'], queryFn: () => apiGet<Course[]>('/courses') })
  const course = courses?.find((c) => c.course_id === id)

  const { data: lessons, isLoading } = useQuery({
    queryKey: ['course', id],
    queryFn: () => apiGet<CourseLesson[]>(`/courses/${id}`),
    enabled: !!id,
  })

  async function startCourseWatch() {
    if (!id) return
    setStarting(true)
    setInlineError(null)
    try {
      await apiPost('/watch', { type: 'course', id })
      invalidateActiveTask()
    } catch (err) {
      if (isNoCredentials(err)) setShowCreds(true)
      else if (isNoCredits(err)) navigate('/paywall')
      else if (err instanceof ApiError && err.body.error === 'task_already_active') {
        setInlineError('У вас уже есть активная задача автопросмотра.')
      } else {
        setInlineError('Не получилось запустить автопросмотр. Попробуйте позже.')
      }
    } finally {
      setStarting(false)
    }
  }

  return (
    <div className="px-4 py-4">
      <h1 className="mb-1 text-xl font-semibold text-[var(--tg-text)]">{course?.title ?? 'Курс'}</h1>
      {course?.lessons_count != null && (
        <p className="mb-4 text-sm text-[var(--tg-hint)]">{course.lessons_count} уроков</p>
      )}

      <button
        onClick={startCourseWatch}
        disabled={starting}
        className="mb-4 w-full rounded-lg bg-[var(--tg-button)] py-3 font-medium text-[var(--tg-button-text)] disabled:opacity-60"
      >
        {starting ? 'Запускаем…' : 'Смотреть весь курс'}
      </button>
      {inlineError && <p className="mb-4 text-sm text-red-400">{inlineError}</p>}

      {isLoading && <p className="text-[var(--tg-hint)]">Загрузка уроков…</p>}

      <ul className="flex flex-col gap-2">
        {lessons?.map((lesson) => (
          <li key={lesson.lesson_id}>
            <Link
              to={`/lessons/${lesson.lesson_id}`}
              className="flex items-center justify-between rounded-lg border border-[var(--tg-border)] bg-[var(--tg-secondary-bg)] px-3 py-2"
            >
              <span className="text-sm text-[var(--tg-text)]">
                {lesson.position != null ? `${lesson.position}. ` : ''}
                {lesson.title ?? lesson.lesson_id}
              </span>
              <span className="flex gap-1 text-xs text-[var(--tg-hint)]">
                {lesson.has_transcript && <span title="Есть транскриб">📄</span>}
                {lesson.questions_count > 0 && <span title="Есть вопросы">❓{lesson.questions_count}</span>}
              </span>
            </Link>
          </li>
        ))}
      </ul>

      <CredentialsModal open={showCreds} onClose={() => setShowCreds(false)} onSaved={startCourseWatch} />
    </div>
  )
}
