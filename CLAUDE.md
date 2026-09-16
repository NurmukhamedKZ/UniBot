# UniBot

Бесплатный Telegram Mini App для студентов KBTU: отмечает видеолекции на
uninet.kbtu.kz просмотренными и делает транскрибы лекций. Как устроен сайт
(вход, API, видео) — docs/uninet.md. Старый продукт (UniX, кредиты, оплата,
Playwright/LLM-тесты) — в legacy/, не собирается и не тестируется.

## Устройство
- `packages/core/uninet.py` — HTTP-клиент uninet (Keycloak PKCE-логин, курсы,
  недели, progress) + аудио из Kinescope. `packages/core/transcribe.py` — ffmpeg → OpenAI.
- api: каталог читается вживую из uninet по кредам юзера (у каждого свои курсы),
  автопросмотр — синхронный POST progress. Креды — Fernet в `users.kbtu_*`.
- worker: только очередь `watch_tasks type='transcribe'`, `lesson_id` = id видео Kinescope.
  Транскрибы кэшируются в `transcripts` и общие для всех.
- Запуск: docker compose up -d --build. Тесты: pytest tests/ -v (нужен Postgres,
  `TEST_DATABASE_URL`, по умолчанию localhost:5433).
- Прод: Contabo VPS (общий с другими проектами), вход через хостовый nginx,
  api на 127.0.0.1:8001. Домен API: api.workkeldi.com; фронтенд — на Vercel.

## Деплой — архитектура и грабли
- webapp → **Vercel**, проект `unibot-webapp`, root dir `apps/webapp`.
  api/bot/worker → **VPS** в `docker compose`, домен `api.workkeldi.com`
  через хостовый nginx как чистый reverse-proxy на `127.0.0.1:8001` (без
  раздачи статики, без `/api`-префикса — роуты в `apps/api/routers/*.py`
  объявлены без него).
- `WEBAPP_URL` в `.env` на VPS = URL вебаппа на Vercel (нужен для CORS
  allowlist в `apps/api/main.py` и для кнопки бота) — НЕ домен VPS.
- Vercel CLI деплоить только из **корня репо** (`.vercel/project.json` там
  → `unibot-webapp`). Запуск `vercel deploy` из `apps/webapp` без линка
  создаёт отдельный левый проект вместо деплоя в существующий.
- BotFather → Menu Button / Mini App URL должен быть стабильный домен-алиас
  (`https://unibot-webapp.vercel.app`), НЕ замороженный URL конкретного
  деплоя (`unibot-webapp-<hash>-....vercel.app`) — на такой URL новый код
  после `vercel deploy --prod` никогда не долетает.
- `apps/webapp/vercel.json` держит SPA-фолбэк (`rewrites` → `/index.html`).
  Без него прямой заход/reload на любой путь кроме `/` (BrowserRouter,
  `/courses/:id/weeks/:weekId` и т.п.) даёт 404 от Vercel.
- `docker-compose.yml`'s `POSTGRES_PASSWORD` и пароль в `.env`'s
  `DATABASE_URL` — синхронизировать вручную (сервис `postgres` не читает
  `.env`, только hardcoded env в compose). Если разъехались уже ПОСЛЕ
  первого `up` — пароль внутри тома не поменяется сам, нужен либо
  `ALTER USER ... WITH PASSWORD`, либо `docker compose down -v` (только
  пока в БД нет данных, которые жалко терять).

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

**SSH access:**
```bash
ssh root@109.199.125.111
```
