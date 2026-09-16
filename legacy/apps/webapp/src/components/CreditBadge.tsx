import { useNavigate } from 'react-router-dom'
import { useMe } from '../queries'

/** Small balance pill, clickable -> paywall. Renders nothing until /me loads. */
export function CreditBadge() {
  const { data } = useMe()
  const navigate = useNavigate()

  if (!data) return null

  return (
    <button onClick={() => navigate('/paywall')} className="badge-pill num" aria-label="Баланс кредитов">
      {data.balance} кред.
    </button>
  )
}
