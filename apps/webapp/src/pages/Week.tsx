import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { apiGet, apiPost } from '../api'
import { QueryState } from '../components/QueryState'
import { openExternalLink } from '../telegram'
import type { TranscriptState, Video, WeekDetail } from '../types'

function formatDuration(sec: number) {
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`
}

export function WeekPage() {
  const { courseId, weekId } = useParams<{ courseId: string; weekId: string }>()
  const qc = useQueryClient()
  const [watching, setWatching] = useState(false)

  const { data, isLoading, error } = useQuery({
    queryKey: ['week', weekId],
    queryFn: () => apiGet<WeekDetail>(`/weeks/${weekId}`),
    enabled: !!weekId,
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

  const allDone = data?.videos.every((v) => v.completed)

  return (
    <div className="px-4 py-5">
      <h1 className="mb-4 text-[26px] font-normal leading-tight tracking-[-0.325px]" style={{ color: 'var(--ink)' }}>
        {data ? `Неделя ${data.week.weekNumber}: ${data.week.theme}` : 'Неделя'}
      </h1>

      <QueryState isLoading={isLoading} error={error} />

      {data && (
        <>
          <div className="mb-5 flex flex-col gap-2">
            <button onClick={watchWeek} disabled={watching || allDone} className="btn-primary w-full">
              {allDone ? 'Все видео просмотрены' : watching ? 'Отмечаем…' : 'Просмотреть все видео недели'}
            </button>
            <button
              onClick={() =>
                openExternalLink(`https://uninet.kbtu.kz/student/course-materials/${courseId}/${data.week.weekNumber}`)
              }
              className="btn-secondary w-full"
            >
              Открыть на uninet
            </button>
          </div>

          {data.videos.length === 0 && <p style={{ color: 'var(--muted)' }}>На этой неделе нет видео.</p>}
          <ul className="flex flex-col gap-3">
            {data.videos.map((video) => (
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
                <Transcript video={video} />
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

function downloadText(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
  const a = document.createElement('a')
  a.href = url
  a.download = `${name}.txt`.replace(/[/\\?%*:|"<>]/g, '_')
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

const PENDING = ['queued', 'running']

function Transcript({ video }: { video: Video }) {
  const qc = useQueryClient()
  const key = ['transcript', video.kinescope_id]
  const [requested, setRequested] = useState(false)
  const [expanded, setExpanded] = useState(false)

  const { data } = useQuery({
    queryKey: key,
    queryFn: () => apiGet<TranscriptState>(`/videos/${video.kinescope_id}/transcript`),
    enabled: video.has_transcript || requested,
    refetchInterval: (q) => (PENDING.includes(q.state.data?.status ?? '') ? 5000 : false),
  })

  async function request() {
    const res = await apiPost<TranscriptState>(`/videos/${video.kinescope_id}/transcribe`)
    qc.setQueryData(key, res)
    setRequested(true)
  }

  if (data?.transcript) {
    return (
      <div className="flex flex-col gap-2">
        {expanded && (
          <p className="whitespace-pre-wrap text-sm" style={{ color: 'var(--body)' }}>
            {data.transcript}
          </p>
        )}
        <div className="flex gap-2">
          <button onClick={() => setExpanded(!expanded)} className="btn-secondary flex-1" style={{ height: 36 }}>
            {expanded ? 'Скрыть текст' : 'Транскриб'}
          </button>
          <button
            onClick={() => downloadText(video.title, data.transcript!)}
            className="btn-secondary flex-1"
            style={{ height: 36 }}
          >
            Скачать .txt
          </button>
        </div>
      </div>
    )
  }

  if (PENDING.includes(data?.status ?? '')) {
    return (
      <button disabled className="btn-secondary w-full" style={{ height: 36 }}>
        Транскрибируем… ~1 мин
      </button>
    )
  }

  return (
    <button onClick={request} className="btn-secondary w-full" style={{ height: 36 }}>
      {data?.status === 'failed' ? 'Не получилось — повторить' : 'Сделать транскриб'}
    </button>
  )
}
