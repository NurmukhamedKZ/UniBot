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

  if (isLoading) return <p className="px-4 py-4 text-[var(--tg-hint)]">Загрузка…</p>
  if (isError || !data) return <p className="px-4 py-4 text-red-400">Урок не найден.</p>

  return (
    <div className="px-4 py-4">
      <h1 className="mb-4 text-xl font-semibold text-[var(--tg-text)]">{data.title}</h1>

      <div className="mb-4 flex flex-col gap-2">
        <button
          onClick={startLessonWatch}
          disabled={starting}
          className="rounded-lg bg-[var(--tg-button)] py-3 font-medium text-[var(--tg-button-text)] disabled:opacity-60"
        >
          {starting ? 'Запускаем…' : 'Автопросмотр — 1 кредит'}
        </button>
        <button
          onClick={() => id && WebApp.openLink(`https://uni-x.almv.kz/platform/lessons/${id}`)}
          className="rounded-lg border border-[var(--tg-border)] py-3 font-medium text-[var(--tg-text)]"
        >
          Открыть видео в UniX
        </button>
        {inlineError && <p className="text-sm text-red-400">{inlineError}</p>}
      </div>

      <section className="mb-6">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-[var(--tg-hint)]">Транскриб</h2>
        {data.transcript ? (
          <button
            onClick={() => downloadTranscript(data.title, data.transcript!)}
            className="rounded-lg border border-[var(--tg-border)] px-3 py-2 text-sm text-[var(--tg-text)]"
          >
            Скачать .txt
          </button>
        ) : (
          <button
            onClick={requestTranscript}
            disabled={transcribing}
            className="rounded-lg border border-[var(--tg-border)] px-3 py-2 text-sm text-[var(--tg-text)] disabled:opacity-60"
          >
            {transcribing ? 'Запрашиваем…' : 'Получить транскриб'}
          </button>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-[var(--tg-hint)]">Вопросы и ответы</h2>
        {data.questions.length === 0 && <p className="text-[var(--tg-hint)]">Пока нет накопленных вопросов.</p>}
        <div className="flex flex-col gap-2">
          {data.questions.map((q, i) => (
            <div key={i} className="rounded-lg border border-[var(--tg-border)] bg-[var(--tg-secondary-bg)]">
              <button
                onClick={() => setOpenIndex(openIndex === i ? null : i)}
                className="flex w-full items-center justify-between px-3 py-2 text-left text-sm text-[var(--tg-text)]"
              >
                <span>{q.question}</span>
                <span className="ml-2 shrink-0 text-[var(--tg-hint)]">{openIndex === i ? '−' : '+'}</span>
              </button>
              {openIndex === i && (
                <ul className="flex flex-col gap-1 px-3 pb-3">
                  {q.answers.map((answer, ai) => (
                    <li
                      key={ai}
                      className={`rounded px-2 py-1 text-sm ${
                        ai === q.llm_answer_index
                          ? 'bg-[var(--tg-accent)] text-white'
                          : 'text-[var(--tg-text)]'
                      }`}
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
