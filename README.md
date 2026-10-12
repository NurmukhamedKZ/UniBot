# UniBot

Бесплатный Telegram Mini App для студентов KBTU:

- отмечает видеолекции на [uninet.kbtu.kz](https://uninet.kbtu.kz) просмотренными;
- делает транскрибы лекций (аудио из Kinescope → OpenAI), транскрибы общие для всех;
- следит за [wsp.kbtu.kz](https://wsp.kbtu.kz/RegistrationOnline) и присылает уведомление,
  когда открылась отметка посещаемости, с кнопкой «✅ Отметиться».

> Неофициальный проект, не связан с KBTU. Пользуйтесь на свой страх и риск
> и в рамках правил университета.

## Как устроено

| Часть | Где | Что делает |
|---|---|---|
| `apps/api` | FastAPI | бэкенд Mini App, авторизация по Telegram `initData` |
| `apps/bot` | aiogram | кнопка Mini App, уведомления и отметка на wsp |
| `apps/worker` | asyncio | очередь транскрибов, опрос wsp раз в 2 мин |
| `apps/webapp` | React + Vite | сам Mini App |
| `packages/core` | | HTTP-клиенты uninet (Keycloak PKCE), wsp (Vaadin), транскрибация |
| `migrations/` | Postgres | SQL-миграции, применяет api на старте |

Как работает uninet изнутри (вход, API, видео) — [docs/uninet.md](docs/uninet.md).
`legacy/` — старый код (UniX, платная модель), не собирается и не тестируется.

## Безопасность кредов

Чтобы ходить в uninet/wsp от имени студента, бэкенд хранит его логин и пароль KBTU,
зашифрованные Fernet-ключом `ENCRYPTION_KEY`. Кто держит инстанс — тот технически
может их расшифровать. Поднимаете свой инстанс — берегите ключ и базу.

Нашли уязвимость — напишите автору в приватные сообщения, не в публичный issue.

## Запуск локально

Нужны Docker, Python 3.13, Node 22+, ffmpeg и Telegram-бот от [@BotFather](https://t.me/BotFather).

```bash
cp .env.example .env   # заполнить TELEGRAM_TOKEN, OPENAI_API_KEY, ENCRYPTION_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # → ENCRYPTION_KEY
docker compose up -d --build   # postgres + api (127.0.0.1:8001) + bot + worker

cd apps/webapp
cp .env.example .env.local     # VITE_API_URL=http://localhost:8001
npm install && npm run dev
```

Mini App открывается только из Telegram по HTTPS — для локальной проверки
пробросьте dev-сервер туннелем (ngrok уже разрешён в `vite.config.ts`) и укажите
его URL в `WEBAPP_URL` и в BotFather.

## Переменные окружения

| Переменная | Зачем |
|---|---|
| `TELEGRAM_TOKEN` | токен бота |
| `WEBAPP_URL` | адрес Mini App (CORS + кнопка в боте) |
| `DATABASE_URL` | Postgres |
| `OPENAI_API_KEY` | транскрибация |
| `ENCRYPTION_KEY` | Fernet-ключ для кредов KBTU |
| `VITE_API_URL` | (webapp) адрес api; в проде `/api`, см. `apps/webapp/vercel.json` |

## Тесты

Нужен живой Postgres (моков нет):

```bash
docker run -d --name unibot-test-pg -p 5433:5432 \
  -e POSTGRES_USER=unibot -e POSTGRES_PASSWORD=unibot -e POSTGRES_DB=unibot postgres:16
pip install -r requirements.txt
python -m pytest tests/ -v   # TEST_DATABASE_URL, по умолчанию postgresql://unibot:unibot@localhost:5433/unibot
```

## Деплой

Сейчас: api/bot/worker + Postgres на Railway, webapp на Vercel. Для своего
инстанса поменяйте адрес api в `apps/webapp/vercel.json` (rewrite `/api/*`).

## Лицензия

[MIT](LICENSE)
