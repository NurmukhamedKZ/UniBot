import { useNavigate } from 'react-router-dom'
import { useMe } from '../queries'

/** Small balance pill, clickable -> paywall. Renders nothing until /me loads. */
export function CreditBadge() {
  const { data } = useMe()
  const navigate = useNavigate()

  if (!data) return null

  return (
    <button
      onClick={() => navigate('/paywall')}
      className="flex items-center gap-1 rounded-full border border-[var(--tg-border)] bg-[var(--tg-secondary-bg)] px-3 py-1 text-sm font-medium text-[var(--tg-text)]"
    >
      <span aria-hidden>⚡</span>
      {data.balance}
    </button>
  )
}
