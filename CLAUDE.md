# UniBot

Telegram Mini App: автопросмотр лекций UniX (Playwright + LLM) за кредиты
+ бесплатные транскрибы и ответы на тесты. Полный контекст: docs/plan.md
(секция «Контекст для ИИ-агентов») и docs/spec.md.

## Правила
- Логику Playwright/LLM в packages/core/*.py НЕ менять — только вызовы БД.
- Кредит списывается только за успешно завершённый урок.
- Запуск: docker compose up -d --build. Тесты: pytest tests/ -v.
- Прод: Contabo VPS (общий с другими проектами), вход через хостовый nginx,
  api на 127.0.0.1:8001. Домен API: api.workkeldi.com; фронтенд — на Vercel.


**SSH access:**
```bash
ssh root@109.199.125.111
```

@docs/plan.md
@docs/spec.md