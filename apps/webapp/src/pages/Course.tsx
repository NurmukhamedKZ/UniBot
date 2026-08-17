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
    <div className="px-4 py-5">
      <h1 className="mb-1 text-[26px] font-normal leading-tight tracking-[-0.325px]" style={{ color: 'var(--ink)' }}>
        {course?.title ?? 'Курс'}
      </h1>
      {course?.lessons_count != null && (
        <p className="num mb-4 text-sm" style={{ color: 'var(--body)' }}>
          {course.lessons_count} уроков
        </p>
      )}

      <div className="mb-5 flex flex-col gap-2">
        <button onClick={startCourseWatch} disabled={starting} className="btn-primary w-full">
          {starting ? 'Запускаем…' : 'Смотреть весь курс'}
        </button>
        {inlineError && (
          <p className="text-sm" style={{ color: 'var(--error)' }}>
            {inlineError}
          </p>
        )}
      </div>

      {isLoading && <p style={{ color: 'var(--muted)' }}>Загрузка уроков…</p>}

      <ul className="flex flex-col">
        {lessons?.map((lesson) => (
          <li key={lesson.lesson_id} style={{ borderBottom: '1px solid var(--hairline-soft)' }}>
            <Link
              to={`/lessons/${lesson.lesson_id}`}
              className="flex items-center justify-between gap-3 rounded-lg px-1 py-3 active:bg-[var(--canvas-soft)]"
            >
              <span className="text-sm" style={{ color: 'var(--ink)' }}>
                {lesson.position != null && <span className="num" style={{ color: 'var(--muted)' }}>{lesson.position}. </span>}
                {lesson.title ?? lesson.lesson_id}
              </span>
              <span className="num flex shrink-0 gap-2 text-[11px]" style={{ color: 'var(--muted)' }}>
                {lesson.has_transcript && <span className="badge-pill">TXT</span>}
                {lesson.questions_count > 0 && <span className="badge-pill">Q&amp;A {lesson.questions_count}</span>}
              </span>
            </Link>
          </li>
        ))}
      </ul>

      <CredentialsModal open={showCreds} onClose={() => setShowCreds(false)} onSaved={startCourseWatch} />
    </div>
  )
}
