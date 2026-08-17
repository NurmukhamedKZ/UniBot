import { NavLink, Outlet, Route, Routes } from 'react-router-dom'
import { CreditBadge } from './components/CreditBadge'
import { ErrorToasts } from './components/ErrorToasts'
import { TaskProgressBar } from './components/TaskProgressBar'
import { OnboardingGate } from './pages/Onboarding'
import { Catalog } from './pages/Catalog'
import { CoursePage } from './pages/Course'
import { Lesson } from './pages/Lesson'
import { Profile } from './pages/Profile'
import { Settings } from './pages/Settings'
import { Paywall } from './pages/Paywall'
import { WebApp } from './telegram'

// TEMP debug banner -- remove once the initData issue is diagnosed.
function DebugBanner() {
  const tg = (window as unknown as { Telegram?: { WebApp?: unknown } }).Telegram
  const lines = [
    `href: ${window.location.href}`,
    `hash: ${window.location.hash || '(empty)'}`,
    `window.Telegram exists: ${!!tg}`,
    `window.Telegram.WebApp exists: ${!!tg?.WebApp}`,
    `WebApp.initData typeof: ${typeof WebApp.initData}`,
    `WebApp.initData length: ${WebApp.initData?.length ?? 'n/a'}`,
    `WebApp.platform: ${WebApp.platform}`,
    `WebApp.version: ${WebApp.version}`,
    `initDataUnsafe keys: ${Object.keys(WebApp.initDataUnsafe ?? {}).join(',') || '(empty)'}`,
  ]
  return (
    <pre className="whitespace-pre-wrap break-all bg-black p-2 text-[10px] text-lime-400">
      {lines.join('\n')}
    </pre>
  )
}

const TAB_LINK_CLASS = ({ isActive }: { isActive: boolean }) =>
  `flex-1 py-2 text-center text-sm ${isActive ? 'text-[var(--tg-accent)] font-medium' : 'text-[var(--tg-hint)]'}`

function Layout() {
  return (
    <div className="flex min-h-screen flex-col pb-14">
      <header className="flex items-center justify-between border-b border-[var(--tg-border)] px-4 py-2">
        <span className="font-semibold text-[var(--tg-text)]">UniBot</span>
        <CreditBadge />
      </header>

      <TaskProgressBar />

      <main className="flex-1">
        <Outlet />
      </main>

      <nav className="fixed bottom-0 left-0 right-0 flex border-t border-[var(--tg-border)] bg-[var(--tg-secondary-bg)]">
        <NavLink to="/catalog" className={TAB_LINK_CLASS}>
          Каталог
        </NavLink>
        <NavLink to="/profile" className={TAB_LINK_CLASS}>
          Профиль
        </NavLink>
        <NavLink to="/paywall" className={TAB_LINK_CLASS}>
          Кредиты
        </NavLink>
      </nav>
    </div>
  )
}

function App() {
  return (
    <>
      <DebugBanner />
      <ErrorToasts />
      <Routes>
        <Route path="/" element={<OnboardingGate />} />
        <Route element={<Layout />}>
          <Route path="/catalog" element={<Catalog />} />
          <Route path="/courses/:id" element={<CoursePage />} />
          <Route path="/lessons/:id" element={<Lesson />} />
          <Route path="/profile" element={<Profile />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/paywall" element={<Paywall />} />
        </Route>
      </Routes>
    </>
  )
}

export default App
