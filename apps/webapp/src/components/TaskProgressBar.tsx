import { useState } from 'react'
import { apiPost } from '../api'
import { useActiveTask, useInvalidateActiveTask, useInvalidateMe } from '../queries'

function describeTask(task: { type: string; status: string; progress: Record<string, unknown> }): string {
  if (task.type === 'course') {
    const current = typeof task.progress.current === 'number' ? task.progress.current : undefined
    const total = typeof task.progress.total === 'number' ? task.progress.total : undefined
    if (task.progress.stopped === 'no_credits') return 'Курс остановлен: закончились кредиты'
    if (current !== undefined && total !== undefined) return `Автопросмотр курса: урок ${current}/${total}`
    return 'Автопросмотр курса…'
  }
  if (task.status === 'queued') return 'Урок в очереди…'
  return 'Автопросмотр урока…'
}

/**
 * Global bar showing the user's active lesson/course watch task, with a
 * cancel button. Renders nothing when there's no active task. Mounted once
 * at the top of App so it's visible across every route.
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
    <div className="sticky top-0 z-40 flex items-center justify-between gap-3 border-b border-[var(--tg-border)] bg-[var(--tg-secondary-bg)] px-4 py-2 text-sm">
      <span className="text-[var(--tg-text)]">{describeTask(task)}</span>
      {!isTerminal && (
        <button
          onClick={handleCancel}
          disabled={cancelling || cancelRequested}
          className="shrink-0 rounded-full border border-[var(--tg-border)] px-3 py-1 text-[var(--tg-hint)] disabled:opacity-60"
        >
          {cancelRequested ? 'Отменяется…' : 'Отменить'}
        </button>
      )}
    </div>
  )
}
