import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

const ONBOARDING_KEY = 'onboarding_done'

const SLIDES = [
  {
    title: 'Автопросмотр лекций',
    body: 'UniBot смотрит видео и сдаёт тест на UniX за вас — 1 кредит за урок.',
  },
  {
    title: 'Транскрибы и ответы бесплатно',
    body: 'Текст любой лекции и накопленные вопросы/ответы доступны всем, без кредов и оплаты.',
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
    <div className="flex min-h-screen flex-col justify-between px-6 py-10">
      <button onClick={finish} className="self-end text-sm text-[var(--tg-hint)]">
        Пропустить
      </button>

      <div className="flex flex-col items-center gap-4 text-center">
        <h1 className="text-2xl font-semibold text-[var(--tg-text)]">{slide.title}</h1>
        <p className="max-w-xs text-[var(--tg-hint)]">{slide.body}</p>
      </div>

      <div className="flex flex-col gap-4">
        <div className="flex justify-center gap-2">
          {SLIDES.map((s, i) => (
            <span
              key={s.title}
              className={`h-1.5 w-6 rounded-full ${i === step ? 'bg-[var(--tg-accent)]' : 'bg-[var(--tg-border)]'}`}
            />
          ))}
        </div>
        <button
          onClick={() => (isLast ? finish() : setStep((s) => s + 1))}
          className="rounded-lg bg-[var(--tg-button)] py-3 font-medium text-[var(--tg-button-text)]"
        >
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
