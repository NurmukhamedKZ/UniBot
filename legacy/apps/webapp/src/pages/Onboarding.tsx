import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

const ONBOARDING_KEY = 'onboarding_done'

const SLIDES = [
  {
    title: 'Автопросмотр лекций',
    body: 'UniBot смотрит видео и сдаёт тест на UniX за вас. Один кредит списывается только за успешно завершённый урок.',
  },
  {
    title: 'Транскрибы и ответы бесплатны',
    body: 'Текст любой лекции и накопленные вопросы с ответами доступны всем, без кредитов и оплаты.',
  },
  {
    title: 'Начнём',
    body: 'Каталог курсов уже наполняется. Кредиты нужны только для автопросмотра.',
  },
]

export function Onboarding() {
  const [step, setStep] = useState(0)
  const navigate = useNavigate()

  function finish() {
    localStorage.setItem(ONBOARDING_KEY, '1')
    navigate('/catalog', { replace: true })
  }

  const slide = SLIDES[step]
  const isLast = step === SLIDES.length - 1

  return (
    <div className="flex min-h-screen flex-col justify-between px-6 py-8">
      <button onClick={finish} className="self-end text-sm font-medium" style={{ color: 'var(--muted)' }}>
        Пропустить
      </button>

      <div className="flex flex-col items-center gap-4 text-center">
        <h1
          className="max-w-sm text-[32px] font-normal leading-[1.2] tracking-[-0.64px]"
          style={{ color: 'var(--ink)' }}
        >
          {slide.title}
        </h1>
        <p className="max-w-xs text-base leading-relaxed" style={{ color: 'var(--body)' }}>
          {slide.body}
        </p>
      </div>

      <div className="flex flex-col gap-5">
        <div className="flex justify-center gap-2">
          {SLIDES.map((s, i) => (
            <span
              key={s.title}
              className="h-1.5 rounded-full transition-all"
              style={{
                width: i === step ? 24 : 8,
                background: i === step ? 'var(--brand)' : 'var(--hairline-strong)',
              }}
            />
          ))}
        </div>
        <button onClick={() => (isLast ? finish() : setStep((s) => s + 1))} className="btn-primary w-full">
          {isLast ? 'В каталог' : 'Далее'}
        </button>
      </div>
    </div>
  )
}

/** Route element for `/`: skip straight to the catalog if onboarding was already seen. */
export function OnboardingGate() {
  const navigate = useNavigate()
  const alreadyDone = localStorage.getItem(ONBOARDING_KEY) != null

  useEffect(() => {
    if (alreadyDone) navigate('/catalog', { replace: true })
  }, [alreadyDone, navigate])

  if (alreadyDone) return null
  return <Onboarding />
}
