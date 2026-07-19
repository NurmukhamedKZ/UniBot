import { Routes, Route } from 'react-router-dom'

// Task 16 replaces this placeholder with the real screens (Onboarding,
// Catalog, Course, Lesson, Profile, Settings, Paywall) and routes.
function Placeholder() {
  return (
    <div className="flex min-h-screen items-center justify-center px-6 text-center">
      <p className="text-[var(--tg-hint)]">UniBot webapp scaffold — screens land in Task 16.</p>
    </div>
  )
}

function App() {
  return (
    <Routes>
      <Route path="*" element={<Placeholder />} />
    </Routes>
  )
}

export default App
