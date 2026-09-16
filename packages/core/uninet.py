"""Клиент uninet.kbtu.kz (KBTU Student Portal) — чистый HTTP, без браузера.

Вход: Keycloak realm ``uninet``, public client ``uninet-frontend``,
authorization code + PKCE (password grant на клиенте выключен). Форма логина
Keycloak: ``#kc-form-login`` с полями ``username``/``password``.
API: ``https://uninet.kbtu.kz/api``, ``Authorization: Bearer <access_token>``
(access живёт 5 мин, refresh — 30 мин). Подробности — docs/uninet.md.
"""

from __future__ import annotations

import base64
import hashlib
import html
import re
import secrets
import time
from urllib.parse import urljoin

import httpx

KEYCLOAK = "https://key.kbtu.kz/realms/uninet/protocol/openid-connect"
API = "https://uninet.kbtu.kz/api"
CLIENT_ID = "uninet-frontend"
REDIRECT_URI = "https://uninet.kbtu.kz/callback"
TIMEOUT = 20


class InvalidCredentials(Exception):
    """Keycloak не пустил с этим логином/паролем."""


class UninetUnavailable(Exception):
    """uninet/Keycloak не отвечает или ответил неожиданно."""


class Uninet:
    """Сессия одного студента. Сам логинится и обновляет токен по мере надобности."""

    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self._access: str | None = None
        self._refresh: str | None = None
        self._access_exp = 0.0
        self._refresh_exp = 0.0

    async def login(self) -> None:
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as c:
                r = await c.get(f"{KEYCLOAK}/auth", params={
                    "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI, "response_type": "code",
                    "scope": "openid profile", "state": secrets.token_hex(8),
                    "code_challenge": challenge, "code_challenge_method": "S256",
                })
                form = re.search(r'id="kc-form-login"[^>]*action="([^"]+)"', r.text)
                if form is None:
                    raise UninetUnavailable(f"keycloak login form not found (HTTP {r.status_code})")
                r = await c.post(html.unescape(form.group(1)),
                                 data={"username": self.email, "password": self.password},
                                 follow_redirects=False)
                code = re.search(r"[?&]code=([^&]+)", r.headers.get("location", ""))
                if code is None:
                    # Неверный пароль: Keycloak перерисовывает форму (200) вместо редиректа.
                    if r.status_code == 200:
                        raise InvalidCredentials()
                    raise UninetUnavailable(f"unexpected keycloak response HTTP {r.status_code}")
                r = await c.post(f"{KEYCLOAK}/token", data={
                    "grant_type": "authorization_code", "client_id": CLIENT_ID, "code": code.group(1),
                    "redirect_uri": REDIRECT_URI, "code_verifier": verifier,
                })
                self._store_tokens(r)
        except httpx.HTTPError as exc:
            raise UninetUnavailable(str(exc)) from exc

    def _store_tokens(self, r: httpx.Response) -> None:
        if r.status_code != 200:
            raise UninetUnavailable(f"token endpoint HTTP {r.status_code}")
        t = r.json()
        now = time.time()
        self._access, self._refresh = t["access_token"], t["refresh_token"]
        self._access_exp = now + t["expires_in"] - 30
        self._refresh_exp = now + t["refresh_expires_in"] - 30

    async def _token(self) -> str:
        now = time.time()
        if self._access and now < self._access_exp:
            return self._access
        if self._refresh and now < self._refresh_exp:
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT) as c:
                    r = await c.post(f"{KEYCLOAK}/token", data={
                        "grant_type": "refresh_token", "client_id": CLIENT_ID, "refresh_token": self._refresh,
                    })
                if r.status_code == 200:
                    self._store_tokens(r)
                    return self._access
            except httpx.HTTPError:
                pass
        await self.login()
        return self._access

    async def _request(self, method: str, path: str, **kw):
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT) as c:
                    r = await c.request(method, API + path, headers={
                        "Authorization": f"Bearer {await self._token()}", "Accept": "application/json",
                    }, **kw)
            except httpx.HTTPError as exc:
                raise UninetUnavailable(str(exc)) from exc
            if r.status_code == 401 and attempt == 0:
                self._access = self._refresh = None  # сессию отозвали — перелогиниться один раз
                continue
            if r.status_code >= 400:
                raise UninetUnavailable(f"{method} {path} -> HTTP {r.status_code}")
            return r.json() if r.content else None

    async def courses(self) -> list[dict]:
        """[{id, code, name, teacher, department}] — курсы текущего семестра студента."""
        return (await self._request("GET", "/courses/student"))["courses"]

    async def course(self, course_id: int) -> dict:
        """{course: {...}, weeks: [{id, weekNumber, theme, unlocked, dueDate, ...}], currentWeek}"""
        return await self._request("GET", f"/courses/{course_id}")

    async def week(self, week_id: int) -> dict:
        """{week: {...}, content: [{contentType, videos: [{id, title, url, durationSec, completed, ...}]}]}"""
        return await self._request("GET", f"/courses/week/{week_id}")

    async def mark_watched(self, video: dict) -> None:
        """Засчитать видео просмотренным: как будто юзер перемотал плеер в конец.

        Плеер uninet шлёт ``{positionSec, deltaSec}`` каждые 5 с; сервер ставит
        ``completed`` по позиции, дошедшей до конца (у реально перемотанных
        видео cumulativeWatchSec = 2–12 с, а completed = true). deltaSec = 5 —
        один обычный тик плеера, без выдуманного «просмотрено целиком».
        """
        await self._request("POST", f"/courses/video/{video['id']}/progress",
                            json={"positionSec": video["durationSec"], "deltaSec": 5})


def week_videos(week: dict) -> list[dict]:
    return [v for item in week.get("content", []) for v in item.get("videos") or []]


def kinescope_id(video_url: str) -> str:
    """https://kinescope.io/uoV6BsPGRw4tHKniAgpTwh -> uoV6BsPGRw4tHKniAgpTwh"""
    return video_url.rstrip("/").rsplit("/", 1)[-1]


async def kinescope_audio_url(kid: str) -> str:
    """HLS-плейлист аудиодорожки видео Kinescope (для ffmpeg).

    Embed-страница отдаётся только с Referer uninet (иначе 403), внутри —
    подписанный ``master.m3u8``; аудио там отдельной дорожкой ``TYPE=AUDIO``,
    так что видео-сегменты качать не нужно. Кредов студента не требуется.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(f"https://kinescope.io/embed/{kid}", headers={"Referer": "https://uninet.kbtu.kz/"})
        r.raise_for_status()
        master = re.search(r'https://kinescope\.io/[^"\s]+?master\.m3u8[^"\s]*', r.text)
        if master is None:
            raise UninetUnavailable(f"kinescope {kid}: master.m3u8 not found")
        master_url = master.group(0).replace("\\u0026", "&").replace("&amp;", "&")
        r = await c.get(master_url, headers={"Referer": "https://kinescope.io/"})
        r.raise_for_status()
    audio = re.search(r'#EXT-X-MEDIA:TYPE=AUDIO[^\n]*URI="([^"]+)"', r.text)
    if audio is None:
        raise UninetUnavailable(f"kinescope {kid}: no audio track in master playlist")
    return urljoin(master_url, audio.group(1))


_sessions: dict[tuple[str, str], Uninet] = {}


def session(email: str, password: str) -> Uninet:
    """Процессный кэш сессий, чтобы не логиниться в Keycloak на каждый запрос API.

    Ключ включает пароль: смена кредов = новая сессия.
    """
    # ponytail: словарь без вытеснения, для тысяч юзеров хватит; LRU — если память станет заметной.
    key = (email, password)
    if key not in _sessions:
        _sessions[key] = Uninet(email, password)
    return _sessions[key]
