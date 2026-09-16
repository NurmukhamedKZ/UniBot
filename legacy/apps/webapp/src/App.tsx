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

const TAB_LINK_CLASS = 'flex-1 text-center text-sm'

function Layout() {
  return (
    <div className="flex min-h-screen flex-col pb-16">
      <header
        className="sticky top-0 z-40 flex items-center justify-between px-4 py-3"
        style={{ background: 'var(--canvas)', borderBottom: '1px solid var(--hairline)' }}
      >
        <span className="text-[17px] font-semibold tracking-tight" style={{ color: 'var(--ink)' }}>
          Uni<span style={{ color: 'var(--brand)' }}>Bot</span>
        </span>
        <CreditBadge />
      </header>

      <TaskProgressBar />

      <main className="flex-1">
        <Outlet />
      </main>

      <nav
        className="fixed bottom-0 left-0 right-0 flex"
        style={{ background: 'var(--canvas)', borderTop: '1px solid var(--hairline)' }}
      >
        {(
          [
            ['/catalog', 'Каталог'],
            ['/profile', 'Профиль'],
            ['/paywall', 'Кредиты'],
          ] as const
        ).map(([to, label]) => (
          <NavLink key={to} to={to} className={TAB_LINK_CLASS}>
            {({ isActive }) => (
              <span
                className="flex flex-col items-center gap-1 py-2.5"
                style={{
                  color: isActive ? 'var(--ink)' : 'var(--muted)',
                  fontWeight: isActive ? 600 : 400,
                }}
              >
                <span
                  className="h-[3px] w-6 rounded-full"
                  style={{ background: isActive ? 'var(--brand)' : 'transparent' }}
                />
                {label}
              </span>
            )}
          </NavLink>
        ))}
      </nav>
    </div>
  )
}

function App() {
  return (
    <>
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
