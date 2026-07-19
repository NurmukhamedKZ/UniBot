import { useState } from 'react'
import { apiPost } from '../api'
import { WebApp } from '../telegram'
import { PACKAGES, type CreateInvoiceResponse, type PackageId } from '../types'

const TRIBUTE_LINKS: Record<PackageId, string | undefined> = {
  S: import.meta.env.VITE_TRIBUTE_LINK_S,
  M: import.meta.env.VITE_TRIBUTE_LINK_M,
  L: import.meta.env.VITE_TRIBUTE_LINK_L,
}

const PACKAGE_ORDER: PackageId[] = ['S', 'M', 'L']

function perVideoPrice(id: PackageId): number {
  return Math.round(PACKAGES[id].price_kzt / PACKAGES[id].credits)
}

export function Paywall() {
  const [busy, setBusy] = useState<PackageId | null>(null)
  const [error, setError] = useState<string | null>(null)
  const sPerVideo = perVideoPrice('S')

  function payTribute(id: PackageId) {
    const link = TRIBUTE_LINKS[id]
    if (!link) {
      // eslint-disable-next-line no-console
      console.warn(`Tribute link for package ${id} is not configured (VITE_TRIBUTE_LINK_${id}).`)
      setError('Оплата картой временно недоступна.')
      return
    }
    WebApp.openLink(link)
  }

  async function payStars(id: PackageId) {
    setBusy(id)
    setError(null)
    try {
      const { invoice_url } = await apiPost<CreateInvoiceResponse>('/payments/stars/create-invoice', {
        package: id,
      })
      WebApp.openInvoice(invoice_url)
    } catch {
      setError('Не удалось создать счёт. Попробуйте ещё раз.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="px-4 py-4">
      <h1 className="mb-1 text-xl font-semibold text-[var(--tg-text)]">Пополнить кредиты</h1>
      <p className="mb-4 text-sm text-[var(--tg-hint)]">1 кредит = 1 автопросмотренный урок. Кредиты не сгорают.</p>
      {error && <p className="mb-3 text-sm text-red-400">{error}</p>}

      <div className="flex flex-col gap-3">
        {PACKAGE_ORDER.map((id) => {
          const pkg = PACKAGES[id]
          const isL = id === 'L'
          const crossedOut = id !== 'S' ? pkg.credits * sPerVideo : null

          return (
            <div
              key={id}
              className={`relative rounded-xl border p-4 ${
                isL
                  ? 'border-[var(--tg-accent)] bg-[var(--tg-secondary-bg)] ring-2 ring-[var(--tg-accent)]'
                  : 'border-[var(--tg-border)] bg-[var(--tg-secondary-bg)]'
              }`}
            >
              {isL && (
                <span className="absolute -top-3 left-4 rounded-full bg-[var(--tg-accent)] px-2 py-0.5 text-xs font-semibold text-white">
                  Лучший выбор · выгоднее в 5 раз
                </span>
              )}

              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-[var(--tg-hint)]">Пакет {id}</p>
                  <p className="text-lg font-semibold text-[var(--tg-text)]">{pkg.credits} видео</p>
                </div>
                <div className="text-right">
                  {crossedOut && (
                    <p className="text-xs text-[var(--tg-hint)] line-through">{crossedOut.toLocaleString('ru-RU')} ₸</p>
                  )}
                  <p className="text-xl font-bold text-[var(--tg-text)]">{pkg.price_kzt.toLocaleString('ru-RU')} ₸</p>
                  <p className="text-xs text-[var(--tg-hint)]">{perVideoPrice(id)} ₸/видео</p>
                </div>
              </div>

              <div className="mt-3 flex gap-2">
                <button
                  onClick={() => payTribute(id)}
                  className="flex-1 rounded-lg border border-[var(--tg-border)] py-2 text-sm font-medium text-[var(--tg-text)]"
                >
                  Картой
                </button>
                <button
                  onClick={() => payStars(id)}
                  disabled={busy === id}
                  className="flex-1 rounded-lg bg-[var(--tg-button)] py-2 text-sm font-medium text-[var(--tg-button-text)] disabled:opacity-60"
                >
                  {busy === id ? 'Открываем…' : `${pkg.stars} ⭐`}
                </button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
