/** Response shapes of the UniBot API (apps/api/routers/uninet.py, me.py). */

export interface Course {
  id: number
  code: string
  name: string
  teacher: string
  department: string
}

export interface CoursesResponse {
  mine: Course[] | null
  others: Course[]
}

export interface Week {
  id: number
  weekNumber: number
  theme: string
  unlocked: boolean
  current?: boolean
  unlockDate: string
  dueDate: string | null
}

export interface CourseDetail {
  course: { id: number }
  weeks: Week[]
}

export interface Video {
  id: number
  title: string
  duration_sec: number
  completed: boolean
  kinescope_id: string
  has_transcript: boolean
  watched_sec: number | null
  required_sec: number | null
}

export interface WeekDetail {
  week: Week
  videos: Video[]
  watch_status: TranscriptState['status']
}

export interface TranscriptState {
  transcript: string | null
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled' | null
}

export interface Me {
  credentials_set: boolean
  email: string | null
}
