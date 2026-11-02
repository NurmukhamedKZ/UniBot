# uninet.kbtu.kz — как устроен сайт (разведка 2026-09-16)

Всё делается чистым HTTP (`packages/core/uninet.py`), браузер не нужен.

## Вход
- `https://uninet.kbtu.kz/login` → кнопка `[data-testid=sso-login]` → Keycloak.
- Keycloak: realm `uninet`, public client `uninet-frontend`, redirect `https://uninet.kbtu.kz/callback`,
  authorization code + PKCE (S256). Password grant на клиенте выключен (`unauthorized_client`).
- Форма: `form#kc-form-login` (action с session_code), поля `#username`, `#password`, кнопка `#kc-login`.
  Успех → 302 на callback с `code=`; неверный пароль → 200 с той же формой.
- Перед `key.kbtu.kz` стоит WAF (openresty, с ~2026-11-02): POST формы без браузерного
  `User-Agent` → `403 Forbidden` от nginx, до Keycloak не доходит (Origin/Referer не помогают).
  Поэтому все httpx-клиенты в `uninet.py` шлют `UA`.
- Токены: access 300 с, refresh 1800 с. API: `Authorization: Bearer <access>`.

## API (`https://uninet.kbtu.kz/api`)
| Запрос | Что отдаёт |
|---|---|
| `GET /courses/student` | `{courses: [{id, code, name, teacher, department}]}` — только курсы этого студента |
| `GET /courses/{id}` | `{course, weeks: [{id, weekNumber, theme, unlocked, unlockDate, dueDate, current}], currentWeek}` |
| `GET /courses/week/{weekId}` | `{week, content: [{contentType: VIDEO_LECTURE, videos: [{id, title, url, durationSec, requiredSec, watchProgressSec, cumulativeWatchSec, completed}], tests, files}]}` |
| `POST /courses/video/{videoId}/progress` | body `{positionSec, deltaSec}`, ответ 200 без тела |

UI-путь недели: `/student/course-materials/{courseId}/{weekNumber}`. Язык названий — по `Accept-Language`.

## Просмотр (разведка 2026-11-03, новый WSP-фронт `/student/`)
С ~ноября 2026 засчитывается только реально «просмотренное» время; перемотка в конец
больше не работает («Перемотанное не засчитывается»).
- `completed` ставит сервер на тике, после которого `cumulativeWatchSec >= requiredSec`;
  `requiredSec` = 90% `durationSec`.
- Плеер шлёт `{positionSec, deltaSec}` раз в 5 с; `deltaSec` — проигранные секунды, перемотка
  (шаг > `min(скорость, 2) + 1` с за секунду) не считается, на скрытой вкладке плеер на паузе.
  Скорость в плеере открывается только после `completed`.
- Сервер засчитывает `deltaSec` не больше реального времени с прошлого тика **этого юзера**
  (+~2 с допуска), бюджет общий на все видео. Проверено: delta 60 сразу после тика → 0;
  20 через 10 с → 12; 40 через 40 с → 40; два видео одновременно → одно получает всё,
  второе 0. Позиция не проверяется (тик с позицией назад засчитан).
- UniBot: воркер «смотрит» неделю в реальном времени — по одному видео, тик раз в 30 с
  с `deltaSec` = прошедшему времени (`Uninet.watch_week`). Неделя занимает ~90% суммарной длины видео.
- Внутри видео бывают квизы (`tests[].videoId` + `timestampSec`) — плеер встаёт на паузу,
  их можно пропустить; на засчитывание видео не влияют.
- Отдельно есть poll-api `/respondent/videos/{id}/progress/` (`delta_sec` ≤ 600) — это
  вступительные тесты, к лекциям не относится.

## Видео (Kinescope)
- `url` видео — `https://kinescope.io/<id>`.
- `GET https://kinescope.io/embed/<id>` — только с `Referer: https://uninet.kbtu.kz/` (иначе 403);
  в HTML подписанный `https://kinescope.io/<uuid>/master.m3u8?expires=…&sign=…`.
- master.m3u8: отдельная аудиодорожка `#EXT-X-MEDIA:TYPE=AUDIO … URI="media.m3u8?…type=audio…"`, без DRM.
  `ffmpeg -headers "Referer: https://kinescope.io/" -i <audio m3u8> -vn -ac 1 -ar 16000 -b:a 32k out.mp3`.
