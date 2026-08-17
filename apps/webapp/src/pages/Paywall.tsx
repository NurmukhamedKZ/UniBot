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
  const lDiscountPct = Math.round((1 - perVideoPrice('L') / sPerVideo) * 100)

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
    <div className="px-4 py-5">
      <h1 className="mb-1 text-[26px] font-normal leading-tight tracking-[-0.325px]" style={{ color: 'var(--ink)' }}>
        Пополнить кредиты
      </h1>
      <p className="mb-5 text-sm" style={{ color: 'var(--body)' }}>
        1 кредит = 1 автопросмотренный урок. Кредиты не сгорают.
      </p>
      {error && (
        <p className="mb-3 text-sm" style={{ color: 'var(--error)' }}>
          {error}
        </p>
      )}

      <div className="flex flex-col gap-3">
        {PACKAGE_ORDER.map((id) => {
          const pkg = PACKAGES[id]
          const isL = id === 'L'
          const crossedOut = id !== 'S' ? pkg.credits * sPerVideo : null
          const fg = isL ? 'var(--canvas)' : 'var(--ink)'
          const fgBody = isL ? 'var(--hairline-strong)' : 'var(--body)'
          const fgMuted = isL ? 'var(--surface-strong)' : 'var(--muted)'

          return (
            <div
              key={id}
              className="relative rounded-[12px] p-4"
              style={
                isL
                  ? { background: 'var(--ink)', color: fg }
                  : { background: 'var(--card)', border: '1px solid var(--hairline)', color: fg }
              }
            >
              {isL && (
                <span
                  className="absolute -top-2.5 left-4 rounded-full px-2 py-0.5 text-[11px] font-semibold"
                  style={{ background: 'var(--brand)', color: 'var(--on-brand)' }}
                >
                  Выгоднее на {lDiscountPct}%
                </span>
              )}

              <div className="flex items-end justify-between">
                <div>
                  <p className="section-label mb-1" style={isL ? { color: fgBody } : undefined}>
                    Пакет {id}
                  </p>
                  <p className="num text-[22px] font-medium leading-none">
                    {pkg.credits} <span className="text-sm font-normal">видео</span>
                  </p>
                </div>
                <div className="text-right">
                  {crossedOut && (
                    <p className="num text-xs line-through" style={{ color: fgMuted }}>
                      {crossedOut.toLocaleString('ru-RU')} ₸
                    </p>
                  )}
                  <p className="num text-xl font-medium">{pkg.price_kzt.toLocaleString('ru-RU')} ₸</p>
                  <p className="num text-xs" style={{ color: fgMuted }}>
                    {perVideoPrice(id)} ₸/видео
                  </p>
                </div>
              </div>

              <div className="mt-4 flex gap-2">
                <button
                  onClick={() => payTribute(id)}
                  className={isL ? 'flex-1' : 'btn-secondary flex-1'}
                  style={
                    isL
                      ? {
                          height: 40,
                          borderRadius: 'var(--r-md)',
                          border: `1px solid ${fgBody}`,
                          color: fg,
                          background: 'transparent',
                          fontSize: 14,
                          fontWeight: 500,
                        }
                      : { height: 40 }
                  }
                >
                  Картой
                </button>
                <button
                  onClick={() => payStars(id)}
                  disabled={busy === id}
                  className="btn-primary flex-1"
                  style={{ height: 40 }}
                >
                  {busy === id ? 'Открываем…' : `${pkg.stars} Stars`}
                </button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
