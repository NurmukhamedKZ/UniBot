CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_id   bigint UNIQUE NOT NULL,
    username      text,
    first_name    text,
    unix_email    text,
    unix_password_enc text,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE payments (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users(id),
    provider     text NOT NULL CHECK (provider IN ('tribute','stars')),
    external_id  text UNIQUE NOT NULL,
    package      text NOT NULL CHECK (package IN ('S','M','L')),
    credits      int  NOT NULL,
    amount       numeric NOT NULL,
    currency     text NOT NULL,
    status       text NOT NULL DEFAULT 'paid',
    raw          jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE watch_tasks (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id),
    type        text NOT NULL CHECK (type IN ('lesson','course','transcribe')),
    lesson_id   text,
    course_id   text,
    status      text NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued','running','done','failed','cancelled')),
    progress    jsonb NOT NULL DEFAULT '{}',
    error       text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    started_at  timestamptz,
    finished_at timestamptz
);
CREATE INDEX idx_watch_tasks_queued ON watch_tasks (created_at) WHERE status = 'queued';
CREATE UNIQUE INDEX idx_watch_tasks_one_active_per_user
    ON watch_tasks (user_id)
    WHERE status IN ('queued','running') AND type IN ('lesson','course');
-- одна активная ПЛАТНАЯ задача на юзера; transcribe-задачи не ограничены

CREATE TABLE credit_ledger (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid NOT NULL REFERENCES users(id),
    delta      int  NOT NULL,
    reason     text NOT NULL CHECK (reason IN ('purchase','bonus','referral','watch')),
    payment_id uuid REFERENCES payments(id),
    task_id    uuid REFERENCES watch_tasks(id),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_ledger_user ON credit_ledger (user_id);

CREATE TABLE referrals (
    referrer_id uuid NOT NULL REFERENCES users(id),
    referred_id uuid UNIQUE NOT NULL REFERENCES users(id),
    credited    bool NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE courses (
    course_id     text PRIMARY KEY,
    title         text NOT NULL,
    cover         text,
    lessons_count int,
    scraped_at    timestamptz
);

CREATE TABLE lessons (
    lesson_id text PRIMARY KEY,
    course_id text REFERENCES courses(course_id),
    title     text,
    position  int,
    has_test  bool
);

CREATE TABLE transcripts (
    lesson_id  text PRIMARY KEY REFERENCES lessons(lesson_id),
    text       text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE questions (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    lesson_id        text NOT NULL REFERENCES lessons(lesson_id),
    question         text NOT NULL,
    answers          jsonb NOT NULL,
    llm_answer_index int
);
CREATE INDEX idx_questions_lesson ON questions (lesson_id);

CREATE TABLE schema_migrations (filename text PRIMARY KEY, applied_at timestamptz DEFAULT now());
