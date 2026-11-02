-- Автопросмотр недели вернулся очередью: uninet засчитывает только реальное время
-- просмотра (docs/uninet.md), неделя смотрится десятки минут. lesson_id = id недели uninet.
ALTER TABLE watch_tasks DROP CONSTRAINT watch_tasks_type_check;
ALTER TABLE watch_tasks ADD CONSTRAINT watch_tasks_type_check
    CHECK (type IN ('lesson', 'course', 'transcribe', 'watch'));
-- повторное нажатие «Просмотреть» не ставит неделю в очередь второй раз
CREATE UNIQUE INDEX idx_watch_tasks_one_active_watch
    ON watch_tasks (user_id, lesson_id) WHERE type = 'watch' AND status IN ('queued', 'running');
