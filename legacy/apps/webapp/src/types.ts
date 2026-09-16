/**
 * Shared response shapes for the UniBot API (apps/api/routers/*.py).
 * Kept permissive on fields the backend doesn't pin down exactly
 * (ledger/task history rows) via an index signature -- callers should read
 * only the documented fields.
 */

export interface Course {
  course_id: string
  title: string
  cover: string | null
  lessons_count: number | null
  scraped_at: string | null
}

export interface CourseLesson {
  lesson_id: string
  course_id: string
  title: string | null
  position: number | null
  has_test: boolean | null
  has_transcript: boolean
  questions_count: number
}

export interface Question {
  question: string
  answers: string[]
  llm_answer_index: number | null
}

export interface LessonDetail {
  title: string
  transcript: string | null
  questions: Question[]
}

export type TaskType = 'lesson' | 'course' | 'transcribe'
export type TaskStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'

export interface CourseTaskProgress {
  current?: number
  total?: number
  scores?: unknown[]
  stopped?: 'no_credits'
  cancel_requested?: boolean
  [key: string]: unknown
}

export interface WatchTask {
  id: string
  user_id: string
  type: TaskType
  lesson_id: string | null
  course_id: string | null
  status: TaskStatus
  progress: CourseTaskProgress
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface LedgerEntry {
  id: string
  delta: number
  reason: string
  created_at: string
  [key: string]: unknown
}

export interface WatchedTaskSummary {
  id: string
  type: TaskType
  lesson_id: string | null
  course_id: string | null
  status: TaskStatus
  created_at: string
  finished_at: string | null
  [key: string]: unknown
}

export interface MeResponse {
  balance: number
  unix_email_set: boolean
  history: LedgerEntry[]
  watched: WatchedTaskSummary[]
}

export interface ReferralResponse {
  link: string
  invited: number
  credited: number
}

export interface CreateInvoiceResponse {
  invoice_url: string
}

export type PackageId = 'S' | 'M' | 'L'

export interface PackageInfo {
  credits: number
  price_kzt: number
  stars: number
}

/** packages/shared/config.py PACKAGES -- no endpoint exposes these, hardcode per brief. */
export const PACKAGES: Record<PackageId, PackageInfo> = {
  S: { credits: 8, price_kzt: 600, stars: 100 },
  M: { credits: 40, price_kzt: 2800, stars: 470 },
  L: { credits: 100, price_kzt: 4500, stars: 750 },
}
