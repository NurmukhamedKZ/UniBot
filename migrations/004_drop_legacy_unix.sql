-- UniX (кредиты, оплата, автопросмотр по расписанию) выведен в legacy/,
-- эти таблицы и колонки актуальным продуктом больше не читаются и не пишутся.

DROP TABLE credit_ledger;
DROP TABLE payments;
DROP TABLE referrals;
DROP TABLE questions;

-- lessons жила только как пустая FK-заглушка под transcripts.lesson_id
-- (см. ensure_lesson_exists) — реальных данных туда никто не писал.
ALTER TABLE transcripts DROP CONSTRAINT transcripts_lesson_id_fkey;
DROP TABLE lessons;
DROP TABLE courses;

-- один активный lesson/course-watch на юзера — правило старого автопросмотра,
-- сейчас enqueue делает только type='transcribe' без course_id.
DROP INDEX idx_watch_tasks_one_active_per_user;
ALTER TABLE watch_tasks DROP COLUMN course_id;
ALTER TABLE watch_tasks DROP COLUMN progress;
