# UniBot — дизайн полного рефакторинга

Дата: 2026-07-18. Статус: утверждён.

## Цель

Заменить unix_auto_bot новым продуктом **UniBot**: Telegram Mini App вместо команд,
кредитная модель вместо подписок, оплата Tribute + Stars, self-hosted инфра на
Contabo VPS (без Railway). Логика автопросмотра/транскрибации переносится без изменений.

## Контекст (для читателя без истории проекта)

**UniX** (https://uni-x.almv.kz) — учебная платформа вуза: студент обязан
смотреть видеолекции и после каждой сдавать тест (5 вопросов, 4 варианта).
UniBot автоматизирует это: Playwright «досматривает» видео от имени студента
(его логин/пароль UniX), LLM решает тест. Дополнительно бот раздаёт всем
транскрибы лекций и накопленные вопросы/ответы — бесплатно.

Предшественник — прод-бот unix_auto_bot (aiogram-команды, подписки free/basic/pro
на Telegram Stars, Postgres/Redis на Railway). Его боевые файлы
`unix_service.py` (автопросмотр: 5 сценариев состояния урока, LLM со structured
output `answer_index 0..3`, готовый метод `scrape_course`) и
`transcribe_service.py` (кэш в БД → скачивание видео 360p → gpt-4o-mini-transcribe)
переносятся в новый репо как есть; меняются только вызовы БД.

Два типа аккаунтов UniX: креды юзера (шифруются, только его автопросмотр)
и сервисный аккаунт из env (скрейп каталога, транскрибы).

## Ключевые решения

| Решение | Выбор |
|---|---|
| Репозиторий | Новый репо `unibot`; старый бот → заглушка со ссылкой |
| БД / инфра | Self-hosted Postgres 16 + Redis в docker compose на Contabo (Supabase не нужен) |
| Стек | React + Vite + Tailwind (Mini App), FastAPI (API), aiogram (бот), Playwright worker |
| Процессы | 3 контейнера: api / bot / worker + nginx + postgres + redis |
| Монетизация | Кредиты без таймера: S=10/1490₸/250⭐, M=50/2490₸/400⭐, L=100/2990₸/500⭐ |
| Оплата | Tribute (карты) + Telegram Stars |
| Каталог | Сервисный аккаунт UniX скрейпит курсы/уроки ежедневно |
| Транскрибы и Q&A | Публично, бесплатно, без лимитов (включая генерацию новых) |
| Referral | Есть: бонус рефереру после первого завершённого автопросмотра приглашённого |
| Данные старого бота | Не мигрируются, чистая схема |

## 1. Структура репо

```
unibot/
├── apps/
│   ├── webapp/          # React + Vite + Tailwind — Telegram Mini App
│   ├── api/             # FastAPI: auth, каталог, задачи, платежи
│   ├── bot/             # aiogram: /start → кнопка Mini App, уведомления
│   └── worker/          # Playwright автопросмотр + transcribe + скрейп каталога
├── packages/
│   ├── core/            # unix_service.py, transcribe_service.py — перенос как есть
│   ├── db/              # запросы, пул asyncpg
│   └── shared/          # config, crypto (Fernet), модели
├── migrations/          # чистые SQL миграции
├── docker-compose.yml   # nginx, api, bot, worker, postgres, redis
└── Dockerfile.api / Dockerfile.bot / Dockerfile.worker
```

Инфра: всё на Contabo VPS. nginx раздаёт статику webapp, проксирует `/api`,
терминирует TLS. Нужен домен (Telegram Mini App требует HTTPS).

## 2. Схема БД

```
users            id uuid pk, telegram_id bigint unique, username, first_name,
                 unix_email text?, unix_password_enc text? (Fernet), created_at
credit_ledger    id, user_id fk, delta int, reason (purchase|bonus|referral|watch),
                 payment_id?, task_id?, created_at        -- баланс = SUM(delta)
payments         id, user_id, provider (tribute|stars), external_id unique,
                 package (S|M|L), credits int, amount, currency, status, raw jsonb
referrals        referrer_id fk, referred_id fk unique, credited bool, created_at
courses          course_id text pk (unix id), title, cover, lessons_count, scraped_at
lessons          lesson_id text pk (unix id), course_id fk, title, position, has_test
transcripts      lesson_id pk fk, text, created_at
questions        id, lesson_id fk, question, answers jsonb, llm_answer_index
watch_tasks      id, user_id fk, type (lesson|course), lesson_id?, course_id?,
                 status (queued|running|done|failed|cancelled),
                 progress jsonb, error, created_at, started_at, finished_at
```

Правила:
- Кредит списывается за успешно завершённый урок (запись `credit_ledger` c
  reason=watch, delta=-1, task_id), не при постановке задачи.
- Идемпотентность платежей: `payments.external_id unique`.
- Referral: deep link `t.me/<UniBot>?start=ref_<telegram_id>`. Бонус начисляется,
  когда приглашённый завершил первый автопросмотр (`referrals.credited=true`
  ставится в той же транзакции, что и бонусная запись ledger).

## 3. API (FastAPI)

Auth: заголовок `X-Telegram-Init-Data`, HMAC-валидация на каждый запрос,
auto-create юзера. Без JWT.

- `GET /courses` — каталог
- `GET /courses/{id}` — уроки + флаги (есть транскриб, пройден юзером)
- `GET /lessons/{id}` — транскриб + вопросы/ответы (публично)
- `POST /lessons/{id}/transcribe` — генерация, если нет в кэше (задача в очередь)
- `POST /watch` — body: {type: lesson|course, id}; проверяет креды UniX и баланс,
  создаёт watch_task
- `GET /tasks/active`, `POST /tasks/{id}/cancel`
- `GET /me` — профиль, баланс, история просмотров
- `PUT /me/unix-credentials`, `DELETE /me/unix-credentials`
- `GET /referral` — ссылка + статистика
- `POST /payments/stars/create-invoice` — invoice link через Bot API
- `POST /payments/tribute/webhook` — проверка подписи, начисление кредитов

## 4. Mini App (React + Vite)

Стиль: UniX-подобный, тёмный, «дорогой»; Telegram theme vars; @twa-dev/sdk.

Экраны:
1. **Onboarding** — 2–3 слайда, без логина UniX, сразу в каталог
2. **Каталог** — карточки курсов
3. **Курс** — список видео, прогресс, кнопка «Смотреть весь курс»
4. **Видео** — скачать транскриб, вопросы/ответы, «Автопросмотр» (1 кредит)
5. **Профиль** — баланс, история, referral-ссылка
6. **Настройки** — UniX креды (ввод/удаление)
7. **Paywall** — пакеты S/M/L, кнопки Tribute / Stars

Первый автопросмотр без кредов → модалка ввода логина/пароля UniX.
Прогресс: поллинг `GET /tasks/active` + уведомления от бота в чат.

## 5. Оплата

- **Tribute**: цифровой продукт на пакет → оплата картой → webhook → payments +
  ledger → уведомление в чат.
- **Stars**: API создаёт invoice, бот обрабатывает pre_checkout /
  successful_payment → те же начисления.
- Повторный webhook/duplicate charge не задваивает кредиты (unique external_id).

## 6. Worker

- Поллинг `watch_tasks` через `FOR UPDATE SKIP LOCKED`; ≤2–3 параллельных
  Playwright-сессий; 1 активная задача на юзера.
- Тип `course`: уроки последовательно; кредит за каждый завершённый; при нуле
  баланса — остановка (done + пометка «кредиты закончились»).
- `watch_unix()` / `_do_test` / transcribe переносятся без изменений логики;
  меняются только вызовы БД.
- Ежедневный скрейп каталога сервисным аккаунтом (UNIX_EMAIL).
- Уведомления юзеру — напрямую через Bot API (тот же токен).
- При старте worker: задачи `running` → `queued` (кредиты не потеряны, списание
  только по факту завершения урока).

## 7. Ошибки и тесты

- Урок в курсе упал: 2 ретрая → skipped, курс продолжается, кредит не списан.
- Unit-тесты: ledger, идемпотентность платежей, очередь (claim/retry/cancel),
  начисление referral.
- E2E автопросмотра против живого UniX — ручной чеклист.

## 8. Старый бот

Репо AutoLessonRegistration: main урезается до заглушки — на любое сообщение
ответ со ссылкой на UniBot. Подписки/оплата в старом боте отключаются.

## Вне скоупа

- Миграция данных старого бота
- Изменение логики просмотра видео / решения тестов / транскрибации
- Просмотр видео внутри mini app (кнопка «посмотреть видео» ведёт на UniX)
