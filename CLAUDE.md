# UniBot

Бесплатный Telegram Mini App для студентов KBTU: отмечает видеолекции на
uninet.kbtu.kz просмотренными и делает транскрибы лекций. Как устроен сайт
(вход, API, видео) — docs/uninet.md. Старый продукт (UniX, кредиты, оплата,
Playwright/LLM-тесты) — в legacy/, не собирается и не тестируется.

## Устройство
- `packages/core/uninet.py` — HTTP-клиент uninet (Keycloak PKCE-логин, курсы,
  недели, progress) + аудио из Kinescope. `packages/core/transcribe.py` — ffmpeg → OpenAI.
- api: с кредами каталог читается вживую из uninet (у каждого свои курсы) и кэшируется
  в `uninet_courses`/`uninet_weeks`; без кредов отдаётся этот кэш (готовые транскрибы),
  новые транскрибы и автопросмотр (синхронный POST progress) — только с кредами
  (409 no_credentials). Креды — Fernet в `users.kbtu_*`.
- worker: очередь `watch_tasks type='transcribe'`, `lesson_id` = id видео Kinescope.
  Транскрибы кэшируются в `transcripts` и общие для всех.
- worker же раз в 2 мин логинится в wsp.kbtu.kz/RegistrationOnline (`packages/core/wsp.py`,
  Vaadin UIDL по HTTP, логин = email до @) за каждого юзера с кредами; нет заглушки
  «Нет доступных дисциплин» → бот шлёт уведомление (один раз на открытие, флаг `users.wsp_open`).
  wsp не отдаёт промежуточный сертификат — он лежит в `packages/core/sectigo_dv_r36.pem`.
- Запуск: docker compose up -d --build. Тесты: pytest tests/ -v (нужен Postgres,
  `TEST_DATABASE_URL`, по умолчанию localhost:5433).

## Деплой
- **Railway** (с 2026-09-16), проект `UniBot`: сервисы `api`, `bot`, `worker` (Dockerfile
  через переменную `RAILWAY_DOCKERFILE_PATH=Dockerfile.<svc>`) + `Postgres`
  (`DATABASE_URL=${{Postgres.DATABASE_URL}}`). Деплой: `railway up --service <svc> --detach`
  из корня репо (GitHub-автодеплоя нет). API: `https://api-production-e1f33.up.railway.app`, порт 8000.
  Миграции гоняет api на старте; worker, стартовав раньше, падает один раз и перезапускается.
- Старый стек на Contabo VPS (`/home/app/unibot`, api.workkeldi.com) остановлен
  `docker compose stop`, не удалён. НЕ запускать бота в двух местах: один токен →
  TelegramConflictError на getUpdates.
- webapp → **Vercel**, проект `unibot-webapp`, root dir `apps/webapp`, **автодеплой из
  push в main**. `VITE_API_URL` (production env на Vercel) = домен api на Railway;
  `WEBAPP_URL` в переменных Railway = `https://unibot-webapp.vercel.app` (CORS + кнопка бота).
- Vercel CLI — только из **корня репо** (`.vercel/project.json` → `unibot-webapp`),
  иначе создастся левый проект.
- BotFather → Mini App URL — стабильный алиас `https://unibot-webapp.vercel.app`,
  не URL конкретного деплоя.
- `apps/webapp/vercel.json` держит SPA-фолбэк (`rewrites` → `/index.html`), без него
  reload на `/courses/:id/weeks/:weekId` даёт 404.

## Telegram
- НЕ читать `WebApp.initData` из `@twa-dev/sdk` — пакет не обновлялся больше
  года и содержит свою устаревшую копию `telegram-web-app.js`, которая на
  актуальных клиентах Telegram (проверено: Desktop 9.6) оставляет
  `initData`/`.platform`/`.version` как `undefined`, даже когда
  `location.hash` реально содержит валидный подписанный `tgWebAppData`.
  `@twa-dev/sdk` можно оставить для косметики (`.ready()`, `.expand()`,
  `themeParams`) — но initData брать через `@telegram-apps/bridge`'s
  `retrieveRawInitData()` (см. `apps/webapp/src/telegram.ts`), она
  перебирает несколько источников (location.href → Performance Navigation
  Timing API → localStorage) и переживает `history.replaceState`.
- Внешние ссылки — `openExternalLink` из `telegram.ts`, не `WebApp.openLink`
  (тот же устаревший bridge, молча не срабатывает).
- Локально Mini App проверяется подставным `#tgWebAppData=...`: bridge требует
  в initData `signature` и `user.first_name`, и нужна полная загрузка страницы
  (смена только хэша не пересоздаёт performance entry).

**SSH (старый VPS):** `ssh -i ~/.ssh/id_ed25519_hermes root@109.199.125.111`
