import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import { apiGet, apiPost, ApiError, isNoCredentials, isNoCredits } from '../api'
import { CredentialsModal } from '../components/CredentialsModal'
import { useInvalidateActiveTask } from '../queries'
import { WebApp } from '../telegram'
import type { LessonDetail } from '../types'

function downloadTranscript(title: string, transcript: string) {
  const blob = new Blob([transcript], { type: 'text/plain' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${title || 'lesson'}.txt`.replace(/[/\\?%*:|"<>]/g, '_')
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export function Lesson() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const invalidateActiveTask = useInvalidateActiveTask()

  const [showCreds, setShowCreds] = useState(false)
  const [starting, setStarting] = useState(false)
  const [inlineError, setInlineError] = useState<string | null>(null)
  const [transcribing, setTranscribing] = useState(false)
  const [openIndex, setOpenIndex] = useState<number | null>(null)

  const { data, isLoading, isError } = useQuery({
    queryKey: ['lesson', id],
    queryFn: () => apiGet<LessonDetail>(`/lessons/${id}`),
    enabled: !!id,
  })

  async function startLessonWatch() {
    if (!id) return
    setStarting(true)
    setInlineError(null)
    try {
      await apiPost('/watch', { type: 'lesson', id })
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

  async function requestTranscript() {
    if (!id) return
    setTranscribing(true)
    try {
      const res = await apiPost<{ transcript?: string; task_id?: string }>(`/lessons/${id}/transcribe`)
      if (res.transcript) {
        queryClient.setQueryData<LessonDetail | undefined>(['lesson', id], (prev) =>
          prev ? { ...prev, transcript: res.transcript! } : prev,
        )
      } else {
        setInlineError('Транскрибация запущена, загляните сюда чуть позже.')
      }
    } catch {
      setInlineError('Не удалось запросить транскрибацию.')
    } finally {
      setTranscribing(false)
    }
  }

  if (isLoading) return <p className="px-4 py-5" style={{ color: 'var(--muted)' }}>Загрузка…</p>
  if (isError || !data) return <p className="px-4 py-5" style={{ color: 'var(--error)' }}>Урок не найден.</p>

  return (
    <div className="px-4 py-5">
      <h1 className="mb-4 text-[26px] font-normal leading-tight tracking-[-0.325px]" style={{ color: 'var(--ink)' }}>
        {data.title}
      </h1>

      <div className="mb-6 flex flex-col gap-2">
        <button onClick={startLessonWatch} disabled={starting} className="btn-primary w-full">
          {starting ? 'Запускаем…' : 'Автопросмотр · 1 кредит'}
        </button>
        <button
          onClick={() => id && WebApp.openLink(`https://uni-x.almv.kz/platform/lessons/${id}`)}
          className="btn-secondary w-full"
        >
          Открыть видео в UniX
        </button>
        {inlineError && (
          <p className="text-sm" style={{ color: 'var(--error)' }}>
            {inlineError}
          </p>
        )}
      </div>

      <section className="mb-6">
        <h2 className="section-label mb-2">Транскриб</h2>
        {data.transcript ? (
          <div className="card p-4">
            <p className="mb-3 line-clamp-4 text-sm" style={{ color: 'var(--body)' }}>
              {data.transcript}
            </p>
            <button onClick={() => downloadTranscript(data.title, data.transcript!)} className="btn-secondary">
              Скачать .txt
            </button>
          </div>
        ) : (
          <button onClick={requestTranscript} disabled={transcribing} className="btn-secondary">
            {transcribing ? 'Запрашиваем…' : 'Получить транскриб'}
          </button>
        )}
      </section>

      <section>
        <h2 className="section-label mb-2">Вопросы и ответы</h2>
        {data.questions.length === 0 && <p style={{ color: 'var(--muted)' }}>Пока нет накопленных вопросов.</p>}
        <div className="flex flex-col gap-2">
          {data.questions.map((q, i) => (
            <div key={i} className="card overflow-hidden">
              <button
                onClick={() => setOpenIndex(openIndex === i ? null : i)}
                className="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left text-sm"
                style={{ color: 'var(--ink)' }}
              >
                <span>{q.question}</span>
                <span className="num shrink-0" style={{ color: 'var(--muted)' }}>
                  {openIndex === i ? '−' : '+'}
                </span>
              </button>
              {openIndex === i && (
                <ul className="flex flex-col gap-1 px-3 pb-3">
                  {q.answers.map((answer, ai) => (
                    <li
                      key={ai}
                      className="rounded-[4px] px-2 py-1 text-sm"
                      style={
                        ai === q.llm_answer_index
                          ? { background: 'var(--brand)', color: 'var(--on-brand)' }
                          : { color: 'var(--body)' }
                      }
                    >
                      {answer}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
      </section>

      <CredentialsModal open={showCreds} onClose={() => setShowCreds(false)} onSaved={startLessonWatch} />
    </div>
  )
}
