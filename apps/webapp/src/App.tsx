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
