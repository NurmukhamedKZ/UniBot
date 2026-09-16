import { useState } from 'react'
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { apiGet, apiPost } from '../api'
import { QueryState } from '../components/QueryState'
import type { TranscriptState, Video, WeekDetail } from '../types'

function formatDuration(sec: number) {
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`
}

/**
 * Копирование, которое работает в WebView Telegram: Clipboard API бывает
 * недоступен или заблокирован, тогда -- старый путь через textarea + execCommand.
 * Вызывать синхронно из обработчика клика (нужен user gesture).
 */
function copyText(text: string): boolean {
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    ta.setSelectionRange(0, text.length)
    const ok = document.execCommand('copy')
    ta.remove()
    if (ok) return true
  } catch {
    // ниже попробуем Clipboard API
  }
  navigator.clipboard?.writeText(text).catch(() => {})
  return !!navigator.clipboard
}

function useCopied() {
  const [copied, setCopied] = useState(false)
  return [
    copied,
    (text: string) => {
      if (copyText(text)) {
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
      }
    },
  ] as const
}

const PENDING = ['queued', 'running']
const isPending = (s?: TranscriptState) => PENDING.includes(s?.status ?? '')

const transcriptKey = (kid: string) => ['transcript', kid]

export function WeekPage() {
  const { weekId } = useParams<{ weekId: string }>()
  const qc = useQueryClient()
  const [watching, setWatching] = useState(false)
  const [requested, setRequested] = useState<Set<string>>(new Set())
  const [copiedAll, copyAll] = useCopied()

  const { data, isLoading, error } = useQuery({
    queryKey: ['week', weekId],
    queryFn: () => apiGet<WeekDetail>(`/weeks/${weekId}`),
    enabled: !!weekId,
  })
  const videos = data?.videos ?? []

  const transcripts = useQueries({
    queries: videos.map((v) => ({
      queryKey: transcriptKey(v.kinescope_id),
      queryFn: () => apiGet<TranscriptState>(`/videos/${v.kinescope_id}/transcript`),
      enabled: v.has_transcript || requested.has(v.kinescope_id),
      refetchInterval: (q: { state: { data?: TranscriptState } }) => (isPending(q.state.data) ? 5000 : false),
    })),
  })

  async function watchWeek() {
    setWatching(true)
    try {
      await apiPost(`/weeks/${weekId}/watch`)
      await qc.invalidateQueries({ queryKey: ['week', weekId] })
    } finally {
      setWatching(false)
    }
  }

  async function requestTranscripts(targets: Video[]) {
    setRequested((prev) => new Set([...prev, ...targets.map((v) => v.kinescope_id)]))
    await Promise.all(
      targets.map(async (v) => {
        const res = await apiPost<TranscriptState>(`/videos/${v.kinescope_id}/transcribe`)
        qc.setQueryData(transcriptKey(v.kinescope_id), res)
      }),
    )
  }

  const texts = transcripts.map((t) => t.data?.transcript ?? null)
  const missing = videos.filter((_, i) => !texts[i] && !isPending(transcripts[i].data))
  const anyPending = transcripts.some((t) => isPending(t.data))
  const allReady = videos.length > 0 && texts.every(Boolean)
  const allWatched = videos.every((v) => v.completed)

  function copyAllTranscripts() {
    copyAll(
      videos
        .map((v, i) => `<Start Lesson ${i + 1}>\n${v.title}\n\n${texts[i]}\n</End Lesson ${i + 1}>`)
        .join('\n\n'),
    )
  }

  return (
    <div className="px-4 py-5">
      <h1 className="mb-4 text-[26px] font-normal leading-tight tracking-[-0.325px]" style={{ color: 'var(--ink)' }}>
        {data ? `Неделя ${data.week.weekNumber}: ${data.week.theme}` : 'Неделя'}
      </h1>

      <QueryState isLoading={isLoading} error={error} />

      {data && (
        <>
          <div className="mb-5 flex flex-col gap-2">
            <button onClick={watchWeek} disabled={watching || allWatched} className="btn-primary w-full">
              {allWatched ? 'Все видео просмотрены' : watching ? 'Отмечаем…' : 'Просмотреть все видео недели'}
            </button>
            {videos.length > 0 &&
              (allReady ? (
                <button onClick={copyAllTranscripts} className="btn-secondary w-full">
                  {copiedAll ? 'Скопировано ✓' : 'Скопировать все транскрибы'}
                </button>
              ) : (
                <button
                  onClick={() => requestTranscripts(missing)}
                  disabled={missing.length === 0}
                  className="btn-secondary w-full"
                >
                  {missing.length === 0 && anyPending ? 'Транскрибируем все…' : 'Транскрибировать все лекции'}
                </button>
              ))}
          </div>

          {videos.length === 0 && <p style={{ color: 'var(--muted)' }}>На этой неделе нет видео.</p>}
          <ul className="flex flex-col gap-3">
            {videos.map((video, i) => (
              <li key={video.id} className="card p-4">
                <div className="mb-3 flex items-start justify-between gap-3">
                  <span className="text-sm" style={{ color: 'var(--ink)' }}>
                    {video.title}
                  </span>
                  <span
                    className="num shrink-0 text-xs"
                    style={{ color: video.completed ? 'var(--success)' : 'var(--muted)' }}
                  >
                    {video.completed ? '✓ ' : ''}
                    {formatDuration(video.duration_sec)}
                  </span>
                </div>
                <Transcript state={transcripts[i].data} onRequest={() => requestTranscripts([video])} />
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

function Transcript({ state, onRequest }: { state?: TranscriptState; onRequest: () => void }) {
  const [expanded, setExpanded] = useState(false)
  const [copied, copy] = useCopied()

  if (state?.transcript) {
    const text = state.transcript
    return (
      <div className="flex flex-col gap-2">
        {expanded && (
          <p className="select-text whitespace-pre-wrap text-sm" style={{ color: 'var(--body)' }}>
            {text}
          </p>
        )}
        <div className="flex gap-2">
          <button onClick={() => setExpanded(!expanded)} className="btn-secondary flex-1" style={{ height: 36 }}>
            {expanded ? 'Скрыть текст' : 'Показать текст'}
          </button>
          <button onClick={() => copy(text)} className="btn-secondary flex-1" style={{ height: 36 }}>
            {copied ? 'Скопировано ✓' : 'Скопировать'}
          </button>
        </div>
      </div>
    )
  }

  if (isPending(state)) {
    return (
      <button disabled className="btn-secondary w-full" style={{ height: 36 }}>
        Транскрибируем… ~1 мин
      </button>
    )
  }

  return (
    <button onClick={onRequest} className="btn-secondary w-full" style={{ height: 36 }}>
      {state?.status === 'failed' ? 'Не получилось — повторить' : 'Сделать транскриб'}
    </button>
  )
}
