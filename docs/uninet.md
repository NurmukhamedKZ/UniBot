# uninet.kbtu.kz — как устроен сайт (разведка 2026-09-16)

Всё делается чистым HTTP (`packages/core/uninet.py`), браузер не нужен.

## Вход
- `https://uninet.kbtu.kz/login` → кнопка `[data-testid=sso-login]` → Keycloak.
- Keycloak: realm `uninet`, public client `uninet-frontend`, redirect `https://uninet.kbtu.kz/callback`,
  authorization code + PKCE (S256). Password grant на клиенте выключен (`unauthorized_client`).
- Форма: `form#kc-form-login` (action с session_code), поля `#username`, `#password`, кнопка `#kc-login`.
  Успех → 302 на callback с `code=`; неверный пароль → 200 с той же формой.
- Токены: access 300 с, refresh 1800 с. API: `Authorization: Bearer <access>`.

## API (`https://uninet.kbtu.kz/api`)
| Запрос | Что отдаёт |
|---|---|
| `GET /courses/student` | `{courses: [{id, code, name, teacher, department}]}` — только курсы этого студента |
| `GET /courses/{id}` | `{course, weeks: [{id, weekNumber, theme, unlocked, unlockDate, dueDate, current}], currentWeek}` |
| `GET /courses/week/{weekId}` | `{week, content: [{contentType: VIDEO_LECTURE, videos: [{id, title, url, durationSec, watchProgressSec, cumulativeWatchSec, completed}], tests, files}]}` |
| `POST /courses/video/{videoId}/progress` | body `{positionSec, deltaSec}`, ответ 200 без тела |

UI-путь недели: `/student/course-materials/{courseId}/{weekNumber}`. Язык названий — по `Accept-Language`.

## Просмотр
Плеер шлёт progress каждые 5 с (`deltaSec` — сколько секунд реально проиграно с прошлого тика).
`completed` ставится, когда позиция дошла до конца: у перемотанных в конец видео `cumulativeWatchSec` 2–12 с,
а `completed = true`. UniBot шлёт один тик `{positionSec: durationSec, deltaSec: 5}`.
Тестов у видео нет (поле `tests` бывает у контента, но в видеолекциях пустое).

## Видео (Kinescope)
- `url` видео — `https://kinescope.io/<id>`.
- `GET https://kinescope.io/embed/<id>` — только с `Referer: https://uninet.kbtu.kz/` (иначе 403);
  в HTML подписанный `https://kinescope.io/<uuid>/master.m3u8?expires=…&sign=…`.
- master.m3u8: отдельная аудиодорожка `#EXT-X-MEDIA:TYPE=AUDIO … URI="media.m3u8?…type=audio…"`, без DRM.
  `ffmpeg -headers "Referer: https://kinescope.io/" -i <audio m3u8> -vn -ac 1 -ar 16000 -b:a 32k out.mp3`.
