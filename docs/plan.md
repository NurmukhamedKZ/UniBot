# UniBot Refactor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Новый продукт UniBot: Telegram Mini App + кредитная модель + self-hosted инфра, с переносом логики автопросмотра/транскрибации как есть.

**Architecture:** 3 процесса (FastAPI api, aiogram bot, Playwright worker) + nginx + Postgres 16 + Redis в docker compose на Contabo. Очередь задач — таблица `watch_tasks` в Postgres (`FOR UPDATE SKIP LOCKED`). Mini App (React+Vite) общается только с API, авторизация через Telegram initData.

**Tech Stack:** Python 3.13, FastAPI, aiogram 3, asyncpg, Playwright, React 18 + Vite + Tailwind, @twa-dev/sdk, Postgres 16, Redis, nginx, Docker Compose.

**Спека:** `docs/superpowers/specs/2026-07-18-unibot-refactor-design.md` (в старом репо AutoLessonRegistration).

## Global Constraints

- Логику `watch_unix()`, `_do_test`, транскрибации НЕ менять — только вызовы БД.
- Кредит списывается за успешно завершённый урок, не при постановке задачи.
- Идемпотентность платежей: `payments.external_id UNIQUE`.
- Транскрибы и Q&A публичны и бесплатны, без лимитов.
- Пароли UniX — только Fernet-шифрованные (`ENCRYPTION_KEY`).
- Данные старого бота не мигрируются.
- Пакеты: S=10/1490₸/250⭐, M=50/2490₸/400⭐, L=100/2990₸/500⭐ (см. `packages/shared/config.py`).
- Referral-бонус — после первого завершённого автопросмотра приглашённого.
- Mini App требует HTTPS → домен `app.workkeldi.com` → A-запись на 109.199.125.111.
- VPS общий с otclick/Langfuse/ClickHouse: свободно ~3 GB RAM → swap 4G обязателен,
  ≤2 параллельных Chromium.

---

## Контекст для ИИ-агентов (прочитать перед ЛЮБОЙ фазой)

### Что за продукт

**UniX** (https://uni-x.almv.kz) — учебная платформа казахстанского вуза: студент
обязан смотреть видеолекции и после каждой сдавать тест (всегда 5 вопросов,
4 варианта ответа). **UniBot** — Telegram Mini App, который:

1. **Автопросмотр**: от имени студента (его логин/пароль UniX) через Playwright
   «досматривает» видео и решает тест с помощью LLM. Платно: 1 кредит = 1 урок.
2. **Публичный контент**: транскрибы лекций и накопленные вопросы/ответы —
   бесплатно любому Telegram-пользователю, без логина UniX.

Это перезапуск старого прод-бота (unix_auto_bot: aiogram-команды, подписки,
Railway). Из него взяты ТОЛЬКО два боевых файла — их Playwright/LLM-логика
проверена в проде и **менять её нельзя**, только адаптировать вызовы БД
и уведомления.

### Что уже лежит в репо до начала работ

```
packages/core/unix_service.py        # автопросмотр (перенесён из старого бота)
packages/core/transcribe_service.py  # транскрибация (перенесён из старого бота)
packages/shared/crypto.py            # Fernet-шифрование паролей (перенесён)
packages/shared/log_utils.py         # декоратор log_duration (перенесён,
                                     #   используется внутри unix_service)
docs/spec.md                         # утверждённая спека продукта
docs/plan.md                         # этот план
```
В перенесённых файлах старые импорты (`app.config`, `app.utils.*`, `app.db.queries`)
— чинятся в Task 4 и Task 6.

### Интерфейс packages/core/unix_service.py (как есть, не менять)

```python
class UnixSiteUnavailableError(Exception): ...   # UniX лёг / не отвечает

class TestAnswer(BaseModel):
    answer_index: Literal[0, 1, 2, 3]            # структурированный ответ LLM

class UniXWatcher:
    def __init__(self, email, password, pool, telegram_id,
                 log_callback=None,          # async (str) -> None  — прогресс-текст юзеру
                 edit_callback=None,         # async (str) -> None  — live-обновление «⏳ видео 43%»
                 reset_video_callback=None,  # async () -> None     — сброс live-сообщения
                 stop_event: asyncio.Event | None = None): ...

    async def watch_unix(self, page: Page, lesson_id: str) -> str | None
        # Один урок на УЖЕ залогиненной странице. Возвращает результат теста
        # («5/5 баллов» и т.п.) или None при фатальной ошибке.
        # Внутри 5 сценариев: тест уже открыт / урок без теста / тест уже сдан /
        # видео просмотрено → сразу тест / полный поток (видео → тест).

    async def watch_unix_videos(self, lesson_ids: list) -> list
        # Полный цикл: свой browser+login, серия уроков. ВНИМАНИЕ: внутри вызовы
        # БД старого бота (db.can_watch_video, db.mark_task_*, лимиты подписок) —
        # в UniBot этот метод НЕ использовать; worker строит свой цикл вокруг
        # watch_unix (см. Task 11), а браузер/логин копирует из этого метода
        # (launch-аргументы chromium там уже вылизаны под слабый VPS).

    async def scrape_course(self, course_id: str) -> list[str]
        # Логин → страница курса → раскрыть секции → вернуть lesson_id уроков.
        # Готовый парсер, использовать в скрейпере каталога (Task 13).
```

БД-вызовы внутри `watch_unix` (сохранение вопросов/ответов) идут через
`import app.db.queries as db` — заменить на `packages.db.catalog`
(questions/transcripts), см. Task 6.

### Интерфейс packages/core/transcribe_service.py

```python
class TranscribeService:
    def __init__(self, pool): ...   # креды берёт из env UNIX_EMAIL/UNIX_PASSWORD
    async def get_or_create_transcription(self, lesson_id: str) -> str
        # Кэш в БД → иначе: Playwright-логин сервисным аккаунтом → src видео 360p
        # → скачать → OpenAI gpt-4o-mini-transcribe → сохранить → вернуть текст.
```

### Факты о платформе UniX (для скрейпа и отладки)

- Логин: `https://uni-x.almv.kz/platform/login` — `input[type='email']`,
  `input[type='password']`, `button[type='submit']`; успех = redirect со страницы логина.
- Урок: `/platform/lessons/{lesson_id}`; курс: `/platform/courses/{course_id}`
  (id числовые, в БД храним как text).
- Страница курса: секции-аккордеоны `div.flex.flex-row.items-start.cursor-pointer` —
  кликнуть все, чтобы раскрыть уроки; ссылки уроков ищутся по `a:has(img[src*=...])`
  с base64-встроенной SVG серого кружка (маркер непройденного урока) — готовый
  код в `scrape_course`, не переизобретать.
- Тег видео: `source[size='360']` → прямой URL mp4.
- Список всех курсов (`/platform/courses`) старым кодом НЕ скрейпился — в Task 13
  агенту нужно исследовать разметку самостоятельно (Playwright + инспекция DOM).

### Роли аккаунтов UniX

- **Креды юзера** (шифрованы Fernet в `users.unix_password_enc`) — только его автопросмотр.
- **Сервисный аккаунт** (`UNIX_EMAIL`/`UNIX_PASSWORD` в env) — скрейп каталога
  и генерация транскрибов. Кредов юзеров для этого не использовать.

### Словарь

- **кредит** — право на 1 автопросмотренный урок; баланс = SUM(credit_ledger.delta)
- **автопросмотр** — Playwright смотрит видео + LLM сдаёт тест за студента
- **пакет S/M/L** — разовая покупка кредитов (не подписка, не сгорают)
- **сервисный аккаунт** — наш собственный аккаунт UniX для публичного контента

---

## Фаза 0 — Скелет репо и инфра

### Task 1: Репо и структура

**Files:** Create: весь скелет `unibot/`, `CLAUDE.md`

Предусловие (делает владелец до запуска агента): в репо уже скопированы
`packages/core/unix_service.py`, `packages/core/transcribe_service.py`,
`packages/shared/crypto.py`, `packages/shared/log_utils.py`,
`docs/spec.md`, `docs/plan.md`.

- [x] Создать `CLAUDE.md` в корне:

```markdown
# UniBot

Telegram Mini App: автопросмотр лекций UniX (Playwright + LLM) за кредиты
+ бесплатные транскрибы и ответы на тесты. Полный контекст: docs/plan.md
(секция «Контекст для ИИ-агентов») и docs/spec.md.

## Правила
- Логику Playwright/LLM в packages/core/*.py НЕ менять — только вызовы БД.
- Кредит списывается только за успешно завершённый урок.
- Запуск: docker compose up -d --build. Тесты: pytest tests/ -v.
- Прод: Contabo VPS (общий с другими проектами), вход через хостовый nginx,
  api на 127.0.0.1:8001. Домен: app.workkeldi.com.
```

- [x] `git init unibot`, создать структуру:

```
unibot/
├── apps/{webapp,api,bot,worker}/
├── packages/{core,db,shared}/
├── migrations/
├── docker-compose.yml
├── Dockerfile.api  Dockerfile.bot  Dockerfile.worker
├── nginx/nginx.conf
├── requirements.txt
├── .env.example
└── .gitignore  (.env, node_modules, dist, __pycache__)
```

- [x] `requirements.txt`:

```
fastapi>=0.115
uvicorn[standard]>=0.30
asyncpg==0.31.0
aiogram>=3.26.0
redis>=7.0.0
playwright>=1.58.0
langchain>=1.2.10
langchain-openai>=1.1.12
openai>=1.50
python-dotenv>=1.2.1
cryptography>=43.0.0
requests>=2.32.5
pytest>=8.0
pytest-asyncio>=0.24
httpx>=0.27
```

- [x] `.env.example`:

```
TELEGRAM_TOKEN=
BOT_USERNAME=UniBot            # без @, для referral-ссылок
WEBAPP_URL=https://<домен>
DATABASE_URL=postgresql://unibot:unibot@postgres:5432/unibot
REDIS_URL=redis://redis:6379
OPENAI_API_KEY=
UNIX_EMAIL=                    # сервисный аккаунт: каталог + транскрибы
UNIX_PASSWORD=
ENCRYPTION_KEY=                # Fernet.generate_key()
ADMIN_ID=
TRIBUTE_API_KEY=
TRIBUTE_WEBHOOK_SECRET=
```

- [x] Commit: `chore: repo skeleton`

### Task 2: docker-compose + Dockerfiles

**Files:** Create: `docker-compose.yml`, `Dockerfile.api`, `Dockerfile.bot`, `Dockerfile.worker`, `nginx/nginx.conf`

- [x] `docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_USER: unibot
      POSTGRES_PASSWORD: unibot        # сменить на проде
      POSTGRES_DB: unibot
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U unibot"]
      interval: 5s
      retries: 10

  redis:
    image: redis:7
    restart: unless-stopped
    volumes: ["redisdata:/data"]

  api:
    build: { context: ., dockerfile: Dockerfile.api }
    restart: on-failure
    env_file: .env
    ports: ["127.0.0.1:8001:8000"]   # 8000 на хосте занят otclick; наружу идём через хостовый nginx
    depends_on: { postgres: { condition: service_healthy } }

  bot:
    build: { context: ., dockerfile: Dockerfile.bot }
    restart: on-failure
    env_file: .env
    depends_on: { postgres: { condition: service_healthy } }

  worker:
    build: { context: ., dockerfile: Dockerfile.worker }
    restart: on-failure
    env_file: .env
    depends_on: { postgres: { condition: service_healthy } }

volumes: { pgdata: {}, redisdata: {} }
# nginx-контейнера НЕТ: на VPS уже работает хостовый nginx (80/443, проксирует
# api.otclick.org → 127.0.0.1:8000). UniBot подключается server-блоком к нему.
```

- [x] `Dockerfile.api` (bot аналогично, entrypoint другой; worker — с playwright):

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY packages/ packages/
COPY apps/api/ apps/api/
COPY migrations/ migrations/
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

`Dockerfile.worker` дополнительно: `RUN playwright install chromium --with-deps`, `CMD ["python", "-m", "apps.worker.main"]`.
`Dockerfile.bot`: `CMD ["python", "-m", "apps.bot.main"]`.

- [x] `nginx/unibot.conf` — server-блок для ХОСТОВОГО nginx (кладётся в
  `/etc/nginx/sites-available/unibot` на VPS, симлинк в sites-enabled):

```nginx
server {
    listen 80;
    server_name app.workkeldi.com;       # A-запись → 109.199.125.111

    root /home/app/unibot/apps/webapp/dist;   # статика Mini App
    index index.html;

    location /api/ {
        proxy_pass         http://127.0.0.1:8001/;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }

    location / { try_files $uri /index.html; }   # SPA fallback

    client_max_body_size 5M;
}
```

  TLS: `certbot --nginx -d <домен-unibot>` — certbot сам добавит 443-блок.
  Mini App без HTTPS не работает — обязательный шаг.
- [x] Проверка: `docker compose up postgres redis` — оба healthy; `nginx -t` на VPS проходит.
- [x] Commit: `chore: docker infra`

### Task 3: Миграции

**Files:** Create: `migrations/001_init.sql`, `packages/db/migrate.py`

- [x] `migrations/001_init.sql` — полная схема из спеки:

```sql
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
```

Замечание: `lessons.lesson_id` — FK-цель для transcripts/questions, но воркер может встретить урок раньше скрейпа каталога → перед вставкой transcript/questions всегда `INSERT INTO lessons (lesson_id) ... ON CONFLICT DO NOTHING` (аналог старого `ensure_unix_video_exists`).

- [x] `packages/db/migrate.py` — прогон `migrations/*.sql` по порядку, пропуск применённых (по `schema_migrations`). Вызывается на старте api.
- [x] Проверка: `docker compose run api python -m packages.db.migrate` → таблицы созданы (`\dt` через psql).
- [x] Commit: `feat: db schema + migrator`

---

## Фаза 1 — packages: shared, db, core

### Task 4: packages/shared

**Files:** Create: `packages/shared/config.py`, `packages/shared/crypto.py`

**Produces:** `config.PACKAGES`, `encrypt(str)->str`, `decrypt(str)->str`

- [x] `config.py` — все env как в старом `app/config.py` + :

```python
PACKAGES = {
    # Сетка «как у провайдера»: per-video цена падает 149 → 50 → 30 ₸,
    # разрыв M→L всего 500 ₸ — толкает к L. S = decoy.
    "S": {"credits": 10,  "price_kzt": 1490, "stars": 250},
    "M": {"credits": 50,  "price_kzt": 2490, "stars": 400},
    "L": {"credits": 100, "price_kzt": 2990, "stars": 500},
}
WELCOME_CREDITS = 2        # бонус новому юзеру
REFERRAL_CREDITS = 3       # бонус рефереру
```

- [x] `crypto.py` УЖЕ в репо (боевой файл старого бота, менять логику нельзя).
  Единственная правка — импорт: `from app.config import ENCRYPTION_KEY` →
  `from packages.shared.config import ENCRYPTION_KEY`. Итоговый файл:

```python
from cryptography.fernet import Fernet

from packages.shared.config import ENCRYPTION_KEY

if not ENCRYPTION_KEY:
    raise RuntimeError(
        "ENCRYPTION_KEY not set. Generate one with: "
        "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
    )

_fernet = Fernet(ENCRYPTION_KEY.encode())


def encrypt_password(password: str) -> str:
    return _fernet.encrypt(password.encode()).decode()


def decrypt_password(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode()
```

- [x] `log_utils.py` УЖЕ в репо — поправить импорты аналогично, если ссылаются на `app.*`.
- [x] Commit: `feat: shared config + crypto`

### Task 5: packages/db — data layer

**Files:** Create: `packages/db/pool.py`, `users.py`, `ledger.py`, `payments.py`, `tasks.py`, `catalog.py`, `referrals.py`; Test: `tests/test_ledger.py`, `tests/test_tasks.py`, `tests/test_payments.py`

**Produces (ключевые сигнатуры для api/worker/bot):**

```python
# users.py
async def get_or_create_user(pool, telegram_id, username, first_name) -> Record
async def set_unix_credentials(pool, user_id, email, password_enc) -> None
# ledger.py
async def get_balance(pool, user_id) -> int          # COALESCE(SUM(delta),0)
async def add_entry(pool_or_conn, user_id, delta, reason, payment_id=None, task_id=None) -> None
# payments.py  — ИДЕМПОТЕНТНО, одна транзакция:
async def record_payment_and_credit(pool, user_id, provider, external_id,
                                    package, credits, amount, currency, raw) -> bool
#   INSERT payments ... ON CONFLICT (external_id) DO NOTHING RETURNING id;
#   если id есть → add_entry(delta=+credits, reason='purchase'); return row is not None
# tasks.py
async def enqueue(pool, user_id, type, lesson_id=None, course_id=None) -> uuid   # ловит UniqueViolation → "уже есть активная задача"
async def claim_next(pool) -> Record | None
async def finish(pool, task_id, status, error=None) -> None
async def update_progress(pool, task_id, progress: dict) -> None
async def requeue_stale_running(pool) -> int          # running → queued (на старте воркера)
# referrals.py
async def register(pool, referrer_tid, referred_user_id) -> None
async def credit_if_first_watch(pool, user_id) -> uuid | None   # вернёт referrer user_id если бонус начислен
# catalog.py  — используется core-сервисами (Task 6), API (Task 8) и скрейпером (Task 13)
async def ensure_lesson_exists(pool, lesson_id) -> None          # INSERT ... ON CONFLICT DO NOTHING
async def save_questions(pool, lesson_id, questions: list[dict]) -> None
#   questions: [{question, answers: list[str], llm_answer_index: int|None}]
#   перезапись: DELETE по lesson_id + INSERT (повторный автопросмотр обновляет)
async def get_lesson_content(pool, lesson_id) -> dict            # {title, transcript|None, questions: [...]}
async def get_transcript(pool, lesson_id) -> str | None
async def save_transcript(pool, lesson_id, text) -> None         # внутри ensure_lesson_exists
async def upsert_course(pool, course_id, title, cover, lessons_count) -> None
async def upsert_lesson(pool, lesson_id, course_id, title, position) -> None
async def list_courses(pool) -> list[Record]
async def get_course_lessons(pool, course_id) -> list[Record]    # + has_transcript, questions_count
```

- [x] `claim_next` — сердце очереди:

```python
async def claim_next(pool):
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("""
                SELECT * FROM watch_tasks
                WHERE status = 'queued'
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            """)
            if row is None:
                return None
            await conn.execute(
                "UPDATE watch_tasks SET status='running', started_at=now() WHERE id=$1",
                row["id"])
            return row
```

- [x] Тесты (pytest-asyncio, против локального postgres из compose):
  - `test_balance_is_sum_of_deltas` — +10, -1, -1 → 8
  - `test_duplicate_payment_not_double_credited` — два вызова `record_payment_and_credit` с одним `external_id` → баланс +credits один раз, второй вызов возвращает False
  - `test_second_active_task_rejected` — enqueue дважды → второй падает
  - `test_claim_marks_running`, `test_requeue_stale`
- [x] Run: `pytest tests/ -v` → PASS
- [x] Commit: `feat: db layer + queue + tests`

### Task 6: packages/core — адаптация перенесённых сервисов

**Files:** Modify: `packages/core/unix_service.py`, `packages/core/transcribe_service.py` (УЖЕ лежат в репо — боевой код старого бота, интерфейсы описаны в секции «Контекст для ИИ-агентов»)

- [x] `unix_service.py` — правки ТОЛЬКО в точках интеграции, Playwright/LLM-поток,
  `_do_test`, селекторы, тайминги НЕ трогать:
  - `import app.db.queries as db` → `from packages.db import catalog`;
  - сохранение вопросов/ответов: старые `db.add_question_answer` /
    `db.add_unix_video` / `db.ensure_unix_video_exists` → новые
    `catalog.save_questions(lesson_id, questions)` и
    `catalog.ensure_lesson_exists(lesson_id)` (вызывать ДО save — FK на lessons);
  - `import app.config` → `packages.shared.config`;
    `from app.utils.log_utils import log_duration` →
    `from packages.shared.log_utils import log_duration` (файл уже в репо);
  - метод `watch_unix_videos` пометить docstring-предупреждением «legacy, не
    использовать — цикл строит worker» (внутри вызовы несуществующих db-функций
    старого бота: can_watch_video, mark_task_*; удалить их вместе с методом ИЛИ
    оставить метод как референс браузер-цикла, но убрать из него db-вызовы).
- [x] `transcribe_service.py`:
  - `import app.db.queries as db` → `packages.db.catalog`
    (`get_transcript(lesson_id)` / `save_transcript(lesson_id, text)`
    вместо `get_unix_video` / `save_transcription`);
  - перед сохранением — `ensure_lesson_exists(lesson_id)`.
- [x] Проверка: `python -c "from packages.core.unix_service import UniXWatcher"`
  без ошибок импорта; при наличии кредов — standalone прогон одного lesson_id.
- [x] Commit: `feat: adapt unix_service + transcribe_service to new db layer`

---

## Фаза 2 — API (FastAPI)

### Task 7: Скелет + initData auth

**Files:** Create: `apps/api/main.py`, `apps/api/auth.py`; Test: `tests/test_auth.py`

**Produces:** dependency `current_user` → Record юзера.

- [x] `auth.py` — валидация Telegram WebApp initData:

```python
import hashlib, hmac, json, time
from urllib.parse import parse_qsl
from fastapi import Header, HTTPException

def validate_init_data(init_data: str, bot_token: str, max_age: int = 3600) -> dict:
    data = dict(parse_qsl(init_data))
    received_hash = data.pop("hash", "")
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise HTTPException(401, "bad initData signature")
    if time.time() - int(data.get("auth_date", 0)) > max_age:
        raise HTTPException(401, "initData expired")
    return json.loads(data["user"])
```

Dependency `current_user`: читает заголовок `X-Telegram-Init-Data`, валидирует, `get_or_create_user`; при создании нового юзера — welcome-кредиты (`add_entry(delta=WELCOME_CREDITS, reason='bonus')`) и обработка `start_param=ref_<tid>` → `referrals.register`.

- [x] Тест: `test_bad_hash_401`, `test_valid_initdata_creates_user_with_welcome_credits` (initData собрать в тесте той же HMAC-схемой).
- [x] `main.py`: lifespan → пул asyncpg + прогон миграций; подключение роутеров.
- [x] Commit: `feat: api skeleton + initData auth`

### Task 8: Каталог + контент endpoints

**Files:** Create: `apps/api/routers/catalog.py`

- [x] `GET /courses` → список из `courses`.
- [x] `GET /courses/{course_id}` → уроки + для каждого: `has_transcript` (EXISTS transcripts), `questions_count`.
- [x] `GET /lessons/{lesson_id}` → `{title, transcript, questions: [{question, answers, llm_answer_index}]}`. Публично (auth всё равно требуем — юзер телеграмный, но кредиты не нужны).
- [x] `POST /lessons/{lesson_id}/transcribe` → если transcript есть — 200 сразу; иначе enqueue задача `type='transcribe'` → 202 `{task_id}`.
- [x] Commit: `feat: catalog endpoints`

### Task 9: Watch + tasks endpoints

**Files:** Create: `apps/api/routers/watch.py`

- [x] `POST /watch` body `{type: "lesson"|"course", id: str}`:
  1. креды UniX заданы? нет → 409 `{error: "no_credentials"}`
  2. `get_balance` > 0? нет → 402 `{error: "no_credits"}`
  3. `enqueue(...)`; UniqueViolation → 409 `{error: "task_already_active"}`
  4. → 201 `{task_id}`
- [x] `GET /tasks/active` → активная задача юзера + `progress` (для поллинга из webapp).
- [x] `POST /tasks/{id}/cancel` → status queued→cancelled; running → выставить `progress.cancel_requested=true` (worker проверяет между уроками).
- [x] Commit: `feat: watch endpoints`

### Task 10: Профиль + креды + referral + платежи-endpoints

**Files:** Create: `apps/api/routers/me.py`, `apps/api/routers/payments.py`

- [x] `GET /me` → `{balance, unix_email_set: bool, history: последние ledger-записи, watched: завершённые задачи}`.
- [x] `PUT /me/unix-credentials` `{email, password}` → `encrypt()` → сохранить. `DELETE` → NULL.
- [x] `GET /referral` → `{link: f"https://t.me/{BOT_USERNAME}?startapp=ref_{tid}", invited: n, credited: m}`.
- [x] `POST /payments/stars/create-invoice` `{package}` → через Bot API `createInvoiceLink` (currency XTR) → `{invoice_url}`.
- [x] `POST /payments/tribute/webhook` — БЕЗ auth-dependency; проверка подписи `TRIBUTE_WEBHOOK_SECRET` (HMAC заголовка по документации Tribute); маппинг продукта → package; `record_payment_and_credit(provider='tribute', external_id=<id платежа Tribute>)`; уведомление юзеру через Bot API sendMessage. Всегда 200 при дубликате.
- [x] Тест: `test_tribute_webhook_duplicate_is_noop`.
- [x] Commit: `feat: me + payments endpoints`

---

## Фаза 3 — Worker

### Task 11: Цикл воркера + lesson-задача

**Files:** Create: `apps/worker/main.py`, `apps/worker/executor.py`

**Consumes:** `claim_next`, `finish`, `update_progress`, `ledger.add_entry`, `UniXWatcher`.

- [ ] `main.py`: на старте `requeue_stale_running`; далее вечный цикл: `claim_next()` → если None, `asyncio.sleep(3)`; семафор `asyncio.Semaphore(2)` на параллельные Playwright.
- [ ] `apps/worker/notify.py` — callbacks для UniXWatcher. Боевой паттерн из
  старого бота (лайв-сообщение «⏳ видео 43%» редактируется, а не спамится),
  адаптирован под aiogram `Bot` без диспетчера (или чистый HTTPS Bot API):

```python
import logging

logger = logging.getLogger(__name__)


def make_callbacks(bot, chat_id: int):
    """log/edit/reset callbacks для UniXWatcher поверх Bot API."""
    video_msg_id: list[int | None] = [None]

    async def log_callback(text: str) -> None:
        await bot.send_message(chat_id, text, parse_mode="HTML")

    async def edit_callback(text: str) -> None:
        if video_msg_id[0] is None:
            sent = await bot.send_message(chat_id, text, parse_mode="HTML")
            video_msg_id[0] = sent.message_id
        else:
            try:
                await bot.edit_message_text(
                    text, chat_id=chat_id, message_id=video_msg_id[0], parse_mode="HTML"
                )
            except Exception as e:
                if "message is not modified" not in str(e).lower():
                    logger.warning("edit_callback failed: %s", e)

    async def reset_video_callback() -> None:
        video_msg_id[0] = None

    return log_callback, edit_callback, reset_video_callback
```

- [ ] `executor.py` — `run_lesson(task)`:
  1. креды юзера из БД → `decrypt_password` →
     `UniXWatcher(email, password, pool, telegram_id,
     log_callback, edit_callback, reset_video_callback)` из `make_callbacks`
  2. свой browser-цикл: `chromium.launch(...)` с args как в legacy-методе
     `watch_unix_videos` (вылизаны под слабый VPS: --disable-dev-shm-usage,
     --no-sandbox, --mute-audio, --js-flags=--max-old-space-size=256, viewport
     1280x720, ignore_https_errors) → `watcher._login(page, email, password)` →
     `watcher.watch_unix(page, lesson_id)` (2 ретрая при исключении;
     `UnixSiteUnavailableError` — отдельное сообщение «UniX недоступен»)
  3. успех → транзакция: `add_entry(delta=-1, reason='watch', task_id)` + `finish(done)`; затем `referrals.credit_if_first_watch(user_id)` → если начислено, уведомить реферера
  4. провал после ретраев → `finish(failed, error)`, кредит НЕ списан
  5. уведомление юзеру через Bot API (raw HTTPS запрос sendMessage — aiogram-полинг тут не нужен)
- [ ] Commit: `feat: worker loop + lesson execution`

### Task 12: Course-задача

**Files:** Modify: `apps/worker/executor.py`

- [x] `run_course(task)`: уроки курса из `lessons` по `position`; пропуск уже пройденных (по `credit_ledger`/progress); для каждого:
  - баланс == 0 → `finish(done)`, progress `{stopped: "no_credits"}`, уведомление с кнопкой paywall
  - `progress.cancel_requested` → `finish(cancelled)`
  - урок: 2 ретрая → skipped (в progress), курс продолжается
  - после каждого урока `update_progress({current: i, total: n, scores: [...]})`
- [x] Commit: `feat: course batch execution`

### Task 13: Transcribe-задача + скрейп каталога

**Files:** Modify: `apps/worker/executor.py`; Create: `apps/worker/scraper.py`

- [ ] `run_transcribe(task)`: `TranscribeService` (сервисный аккаунт UNIX_EMAIL) → сохранение в `transcripts`. Бесплатно, кредиты не трогаем.
- [ ] `scraper.py`: раз в 24ч (asyncio-таск в main.py), сервисный аккаунт:
  1. Список курсов: открыть `/platform/courses`, извлечь id/название/обложку
     карточек. Готового парсера НЕТ — исследовать DOM через Playwright
     (страница SPA, ждать рендера; см. факты о платформе в секции контекста).
     Upsert в `courses`.
  2. Уроки каждого курса: `UniXWatcher.scrape_course(course_id)` — ГОТОВЫЙ метод
     в `packages/core/unix_service.py` (раскрывает секции-аккордеоны, достаёт
     lesson_id). Названия уроков добрать со страницы курса тем же прогоном.
     Upsert в `lessons` (course_id, position по порядку на странице).
  3. Ошибка скрейпа одного курса не валит остальные (try/except на курс, лог).
- [ ] Commit: `feat: transcribe task + catalog scraper`

---

## Фаза 4 — Bot (aiogram, тонкий)

### Task 14: /start + Stars

**Files:** Create: `apps/bot/main.py`

- [x] `/start`: приветствие + inline-кнопка `WebAppInfo(url=WEBAPP_URL)`. Deep link `start=ref_<tid>` тоже регистрирует referral (дубль к startapp — кто каким путём зашёл).
- [x] Меню-кнопка чата: `set_chat_menu_button` → WebApp.
- [ ] Stars — рабочий флоу из прода старого бота (детали, которые легко
  ошибиться): валюта `XTR`, `provider_token=""` (пустая строка!), сумма в
  Stars без умножения. Обработчики:

```python
from aiogram.types import LabeledPrice, PreCheckoutQuery, Message
from packages.shared.config import PACKAGES, ADMIN_ID

# инвойс (вызывается и из API через create_invoice_link, и по кнопке)
async def send_package_invoice(bot, chat_id: int, package: str):
    p = PACKAGES[package]
    await bot.send_invoice(
        chat_id=chat_id,
        title=f"Пакет {package} — {p['credits']} видео",
        description=f"{p['credits']} автопросмотров, не сгорают",
        payload=f"pkg_{package}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=f"Пакет {package}", amount=p["stars"])],
    )

@router.pre_checkout_query()
async def handle_pre_checkout(pre_checkout: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout.id, ok=True)

@router.message(F.successful_payment)
async def handle_successful_payment(message: Message):
    pay = message.successful_payment
    package = pay.invoice_payload.removeprefix("pkg_")   # "S"|"M"|"L"
    credited = await record_payment_and_credit(
        pool, user_id=...,                      # по message.from_user.id
        provider="stars",
        external_id=pay.telegram_payment_charge_id,
        package=package,
        credits=PACKAGES[package]["credits"],
        amount=pay.total_amount, currency="XTR",
        raw=pay.model_dump(),
    )
    # credited=False → дубликат, кредиты не задваивать, но юзеру всё равно ответить
    # → сообщение с новым балансом + уведомление админу (ADMIN_ID):
    #   username, пакет, сумма Stars, charge_id (нужен для возвратов)
```
- [x] Всё остальное (любой текст) → «Открой приложение 👇» + кнопка.
- [x] Commit: `feat: thin bot`

---

## Фаза 5 — Webapp (React + Vite)

### Task 15: Scaffold + API-клиент + тема

**Files:** Create: `apps/webapp/` (vite scaffold)

- [ ] `npm create vite@latest webapp -- --template react-ts`, `+ tailwindcss @twa-dev/sdk react-router-dom @tanstack/react-query`.
- [ ] `src/api.ts`: fetch-обёртка, всегда шлёт `X-Telegram-Init-Data: WebApp.initData`; обработка 402 (→ paywall) и 409 no_credentials (→ модалка кредов).
- [ ] Тема: тёмная, UniX-подобная; CSS-переменные из `WebApp.themeParams`; `WebApp.expand()` на старте.
- [ ] Commit: `feat: webapp scaffold`

### Task 16: Экраны

**Files:** Create: `src/pages/{Onboarding,Catalog,Course,Lesson,Profile,Settings,Paywall}.tsx`, `src/components/{CreditBadge,TaskProgressBar,CredentialsModal}.tsx`

- [ ] **Onboarding** — 2–3 слайда (localStorage-флаг), → Catalog.
- [ ] **Catalog** — `GET /courses`, карточки с обложками.
- [ ] **Course** — `GET /courses/{id}`; кнопка «Смотреть весь курс» → `POST /watch {type:course}`; список уроков со статусами.
- [ ] **Lesson** — транскриб (кнопка скачать .txt через Blob), аккордеон Q&A с подсветкой llm-ответа, кнопка «Автопросмотр — 1 кредит», «Открыть видео в UniX» (внешняя ссылка).
- [ ] **TaskProgressBar** — глобальный, поллинг `GET /tasks/active` каждые 5с пока есть активная задача; кнопка отмены.
- [ ] **Profile** — баланс, история, referral-ссылка (share через `WebApp.openTelegramLink`).
- [ ] **Settings** — форма кредов UniX (PUT/DELETE), предупреждение о шифровании.
- [ ] **Paywall** — 3 карточки пакетов; Tribute → `WebApp.openLink(tribute_url)`; Stars → `POST create-invoice` → `WebApp.openInvoice(url)`.
  Психология сетки: крупно ₸/видео на каждой карточке (149 / 50 / 30 ₸);
  L подсвечен «Лучший выбор» + бейдж «Выгоднее в 5 раз»; у M и L зачёркнутая
  цена по тарифу S (напр. ~~7 450 ₸~~ → 2 490 ₸ у M); порядок карточек S→M→L,
  чтобы глаз шёл по нарастающей выгоде.
- [ ] Проверка: `npm run build` → dist; локально через `WebApp` mock.
- [ ] Commit: `feat: webapp screens`

---

## Фаза 6 — Деплой + заглушка старого бота

### Task 17: Деплой на Contabo (VPS общий с otclick)

На VPS уже живут: otclick (api на 127.0.0.1:8000, postgres:17, redis), Langfuse +
ClickHouse + MinIO, старый unixauto-bot. UniBot ставится рядом, не трогая их.

- [ ] Память: замер 2026-07-18 — 3.1 Gi available из 7.8, swap 0. ОБЯЗАТЕЛЬНО
  добавить swap 4G до первого автопросмотра (иначе OOM-killer может прибить
  соседние сервисы при пике Chromium):

```bash
fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

  Worker: старт с 2 Playwright-сессий; если swap активно используется — снизить до 1.
- [ ] Домен: `app.workkeldi.com` → A-запись на 109.199.125.111 (домен свободен,
  проекты не смешиваются с otclick).
- [ ] Проект в `/home/app/unibot`; `.env` на сервере (пароль postgres не дефолтный!).
- [ ] `docker compose up -d --build` — постгрес/редис UniBot свои, внутренняя сеть,
  с otclick-овскими postgres:17/redis:7 не конфликтуют (порты наружу не публикуются).
- [ ] `nginx/unibot.conf` → `/etc/nginx/sites-available/unibot`, симлинк,
  `nginx -t && systemctl reload nginx`, затем `certbot --nginx -d <домен>`.
- [ ] `npm run build` webapp → dist попадает в путь из server-блока.
- [ ] BotFather: новый бот @UniBot (или имя свободное), меню-кнопка, `/setdomain` для WebApp.
- [ ] Tribute: создать 3 цифровых продукта (S/M/L), webhook URL `https://<домен>/api/payments/tribute/webhook`.
- [ ] Smoke-тест: onboarding → каталог → транскриб → ввод кредов → автопросмотр 1 урока → покупка Stars (тестово) → referral-ссылка.

### Task 18: Заглушка старого бота

**Files (старый репо AutoLessonRegistration):** Modify: `app/bot/main.py`

- [ ] Урезать: единственный handler на любое сообщение/команду →
  «Мы переехали! Новый бот: t.me/<UniBot>» + inline-кнопка. Отключить платежи/watch.
- [ ] Деплой старого репо на VPS (`git pull && docker compose up -d --build`).
- [ ] Разовая рассылка «мы переехали» по всей базе старого бота — в старом репо
  уже есть готовый `scripts/broadcast.py`, запускать оттуда (не переносить).

---

## Порядок и зависимости

```
Фаза 0 → Фаза 1 → { Фаза 2, Фаза 3, Фаза 4 — параллельно можно } → Фаза 5 → Фаза 6
```

Критический путь: Task 3 (схема) и Task 5 (db layer) — от них зависит всё.
Ручная проверка после каждой фазы; unit-тесты — Task 5, 7, 10 обязательны.
