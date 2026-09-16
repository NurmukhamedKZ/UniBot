import { useState } from 'react'
import { apiPost } from '../api'
import { useActiveTask, useInvalidateActiveTask, useInvalidateMe } from '../queries'

type TaskView = {
  id: string
  type: string
  status: string
  progress: Record<string, unknown>
}

function pillStyle(task: TaskView): { background: string; color: string } {
  if (task.type === 'course' && task.progress.stopped === 'no_credits')
    return { background: 'var(--error)', color: '#fff' }
  if (task.status === 'queued') return { background: 'var(--tl-queued)', color: 'var(--ink)' }
  if (task.status === 'done') return { background: 'var(--tl-done)', color: '#fff' }
  return { background: 'var(--tl-running)', color: 'var(--ink)' }
}

function describeTask(task: TaskView): string {
  if (task.type === 'course') {
    if (task.progress.stopped === 'no_credits') return 'Курс остановлен: закончились кредиты'
    const current = typeof task.progress.current === 'number' ? task.progress.current : undefined
    const total = typeof task.progress.total === 'number' ? task.progress.total : undefined
    if (current !== undefined && total !== undefined) return `Урок ${current}/${total}`
    return 'Автопросмотр курса'
  }
  if (task.status === 'queued') return 'В очереди'
  if (task.status === 'done') return 'Готово'
  return 'Автопросмотр урока'
}

/**
 * Global bar showing the user's active lesson/course watch task, with a
 * cancel button. The pastel stage pill is the brand's timeline language,
 * scoped to this autowatch surface only. Renders nothing without an
 * active task. Mounted once at the top of App so it's visible on every
 * route.
 */
export function TaskProgressBar() {
  const { data: task } = useActiveTask()
  const invalidateActiveTask = useInvalidateActiveTask()
  const invalidateMe = useInvalidateMe()
  const [cancelling, setCancelling] = useState(false)

  if (!task) return null

  const cancelRequested = Boolean(task.progress?.cancel_requested)
  const isTerminal = task.status === 'done' || task.status === 'failed' || task.status === 'cancelled'

  async function handleCancel() {
    setCancelling(true)
    try {
      await apiPost(`/tasks/${task!.id}/cancel`)
      invalidateActiveTask()
      invalidateMe()
    } finally {
      setCancelling(false)
    }
  }

  return (
    <div
      className="flex items-center justify-between gap-3 px-4 py-2.5"
      style={{ background: 'var(--canvas-soft)', borderBottom: '1px solid var(--hairline)' }}
    >
      <div className="flex min-w-0 items-center gap-2.5">
        <span className="timeline-pill shrink-0" style={pillStyle(task)}>
          Автопросмотр
        </span>
        <span className="num truncate text-sm" style={{ color: 'var(--body)' }}>
          {describeTask(task)}
        </span>
      </div>
      {!isTerminal && (
        <button
          onClick={handleCancel}
          disabled={cancelling || cancelRequested}
          className="shrink-0 rounded-full px-3 py-1 text-sm disabled:opacity-60"
          style={{ border: '1px solid var(--hairline-strong)', color: 'var(--body)', background: 'var(--card)' }}
        >
          {cancelRequested ? 'Отменяется…' : 'Отменить'}
        </button>
      )}
    </div>
  )
}
