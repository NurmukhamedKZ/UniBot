-- Кэш каталога uninet: заполняется, когда юзер с кредами открывает курсы/недели.
-- Юзер без кредов видит отсюда все курсы, по которым у нас что-то есть, и готовые транскрибы.
CREATE TABLE uninet_courses (
    id         int PRIMARY KEY,
    info       jsonb,             -- {id, code, name, teacher, department} из /courses/student
    weeks      jsonb,             -- weeks из /courses/{id}
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE uninet_weeks (
    id         int PRIMARY KEY,
    week       jsonb NOT NULL,
    videos     jsonb NOT NULL,    -- [{id, title, duration_sec, kinescope_id}] без личного completed
    updated_at timestamptz NOT NULL DEFAULT now()
);
