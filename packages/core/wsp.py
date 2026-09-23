"""Клиент wsp.kbtu.kz/RegistrationOnline («Отметка посещения») — чистый HTTP.

Сайт на Vaadin 7: HTML — пустой бутстрап, весь UI приходит JSON-ом (UIDL).
Порядок: GET страницы (JSESSIONID + appId) → POST ``?v-<ts>`` с
``v-browserDetails`` (дерево компонентов + csrf) → первый rpc досылает форму
логина (ComboBox логина, PasswordField, Panel с Enter-действием) → логин двумя
rpc → повторный bootstrap отдаёт состояние страницы. Логин — не email, а
``n_xxxxx`` (часть email до @), пароль тот же, что у uninet.

Открытая пара на странице — Label ``Дисциплина<br>Преподаватель<br>Л3 (12:00 - 13:00)<br>
Осталось 49 мин.`` и Button «Отметиться» в одном контейнере. Отметка — ``click`` по этой
кнопке через ``ButtonServerRpc`` в той же сессии; id компонентов между сессиями не
стабильны, поэтому пара узнаётся по тексту (см. ``Lesson.key``).
"""

from __future__ import annotations

import enum
import hashlib
import json
import re
import ssl
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import certifi
import httpx

from packages.core.uninet import InvalidCredentials, UninetUnavailable

BASE = "https://wsp.kbtu.kz/RegistrationOnline"
# Заглушка пустой страницы: ru (казахского перевода нет, kk показывает ru) и en.
NO_DISCIPLINES = {"Нет доступных дисциплин", "No disciplines available"}
# Заголовок страницы и подписи кнопок — в уведомлении не нужны (там своя кнопка).
SKIP_TEXTS = {"Отметка посещения", "Attendance mark", "Отметиться"}
LOGIN_VIEW = "kz.kbtu.officeregistrar.widget.api.LoginView"
BUTTON = "com.vaadin.ui.Button"
BUTTON_RPC = "com.vaadin.shared.ui.button.ButtonServerRpc"
# MouseEventDetails левого клика — Vaadin требует его аргументом click().
CLICK = {"button": "LEFT", "clientX": 0, "clientY": 0, "relativeX": 0, "relativeY": 0,
         "type": 1, "altKey": False, "ctrlKey": False, "metaKey": False, "shiftKey": False}
TIMEOUT = 20
# callback_data кнопок Telegram: MARK_PREFIX + Lesson.key; MARK_DONE — уже отмеченная пара.
MARK_PREFIX = "wsp:"
MARK_DONE = "wsp:done"

# wsp.kbtu.kz не отдаёт промежуточный сертификат Sectigo (браузеры догружают его по AIA,
# Python — нет), поэтому кладём его к certifi сами.
_SSL = ssl.create_default_context(cafile=certifi.where())
_SSL.load_verify_locations(Path(__file__).with_name("sectigo_dv_r36.pem"))


def wsp_login(email: str) -> str:
    return email.split("@", 1)[0]


def texts(uidl: dict) -> list[str]:
    """Все тексты/подписи компонентов из UIDL-состояния."""
    return [v[k] for v in uidl.get("state", {}).values() for k in ("text", "caption")
            if isinstance(v.get(k), str) and v[k]]


def attendance_text(page_texts: list[str]) -> str:
    """Дисциплины открытой отметки: без заголовка и подписей кнопок, ``<br>`` → перенос."""
    return "\n\n".join(t.replace("<br>", "\n") for t in page_texts if t not in SKIP_TEXTS)


def is_open(page_texts: list[str]) -> bool:
    """Отметка открыта, если страница не показывает заглушку «нет дисциплин»."""
    return NO_DISCIPLINES.isdisjoint(page_texts)


class Lesson(NamedTuple):
    """Открытая для отметки пара на странице wsp."""

    label: str   # "Дисциплина<br>Преподаватель<br>Л3 (12:00 - 13:00)<br>Осталось 49 мин."
    button: str  # id кнопки «Отметиться» — действителен только в своей сессии

    @property
    def key(self) -> str:
        """Id пары, стабильный между сессиями: без меняющейся строки «Осталось N мин.»."""
        return hashlib.sha256("<br>".join(self.label.split("<br>")[:3]).encode()).hexdigest()[:16]

    @property
    def title(self) -> str:
        """«Дисциплина Л3» — коротко для кнопки в Telegram."""
        parts = self.label.split("<br>")
        slot = parts[2].split(" ", 1)[0] if len(parts) > 2 else ""
        return f"{parts[0]} {slot}".strip()


class MarkResult(enum.Enum):
    MARKED = "marked"            # после клика пары больше нет среди открытых
    UNCONFIRMED = "unconfirmed"  # клик ушёл, но пара всё ещё открыта
    CLOSED = "closed"            # пары уже нет: окно закрылось или уже отмечено


def _types(uidl: dict) -> dict[str, str]:
    """{id компонента: имя класса Vaadin}."""
    names = {v: k for k, v in uidl.get("typeMappings", {}).items()}
    return {cid: names.get(int(t)) for cid, t in uidl.get("types", {}).items()}


def _component_ids(uidl: dict) -> dict[str, str]:
    """{имя класса Vaadin: id компонента} — чтобы не хардкодить id формы логина."""
    return {name: cid for cid, name in _types(uidl).items()}


def lessons(uidl: dict) -> list[Lesson]:
    """Открытые пары: Label с ``<br>`` и Button, лежащие в одном контейнере."""
    types, state = _types(uidl), uidl.get("state", {})
    found = []
    for kids in uidl.get("hierarchy", {}).values():
        label = next((state[k]["text"] for k in kids
                      if "<br>" in str(state.get(k, {}).get("text", ""))), None)
        button = next((k for k in kids
                       if types.get(k) == BUTTON and state.get(k, {}).get("enabled", True)), None)
        if label and button:
            found.append(Lesson(label, button))
    return found


async def _bootstrap(c: httpx.AsyncClient, app_id: str) -> tuple[int, dict]:
    now = int(time.time() * 1000)
    r = await c.post(f"{BASE}?v-{now}", data={
        "v-browserDetails": 1, "theme": "r5", "v-appId": app_id, "v-sh": 900, "v-sw": 1440,
        "v-cw": 1200, "v-ch": 800, "v-curdate": now, "v-tzo": -300, "v-dstd": 0, "v-rtzo": -300,
        "v-dston": "false", "v-vw": 1200, "v-vh": 0, "v-loc": BASE, "v-wn": f"{app_id}-0.1",
    })
    r.raise_for_status()
    d = r.json()
    return d["v-uiId"], json.loads(d["uidl"])


async def _rpc(c: httpx.AsyncClient, ui_id: int, csrf: str, sync: int, rpc: list) -> dict:
    r = await c.post(f"{BASE}/UIDL/?v-uiId={ui_id}", json={
        "csrfToken": csrf, "rpc": rpc, "syncId": sync, "clientId": sync, "wsver": "7.7.13",
    })
    r.raise_for_status()
    d = json.loads(r.text.removeprefix("for(;;);"))
    return d[0] if isinstance(d, list) else d


@dataclass
class _Session:
    client: httpx.AsyncClient
    app_id: str
    ui_id: int
    csrf: str
    page: dict  # UIDL страницы «Отметка посещения» после логина

    async def reload(self) -> None:
        """Новый bootstrap в той же сессии — как F5 в браузере."""
        self.ui_id, self.page = await _bootstrap(self.client, self.app_id)
        self.csrf = self.page.get("Vaadin-Security-Key", self.csrf)

    async def click(self, button: str) -> None:
        await _rpc(self.client, self.ui_id, self.csrf, self.page.get("syncId", 0),
                   [[button, BUTTON_RPC, "click", [CLICK]]])


@asynccontextmanager
async def _session(email: str, password: str) -> AsyncIterator[_Session]:
    """Залогиненная сессия wsp. Сетевые сбои и смена вёрстки → ``UninetUnavailable``."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, verify=_SSL, follow_redirects=True) as c:
            app = re.search(r'initApplication\("([^"]+)"', (await c.get(BASE)).text)
            if app is None:
                raise UninetUnavailable("wsp: vaadin app id not found")
            ui_id, boot = await _bootstrap(c, app.group(1))
            csrf = boot["Vaadin-Security-Key"]
            storage = next(k for k, v in boot["state"].items() if "generatedUuid" in v)
            form = await _rpc(c, ui_id, csrf, 0, [[
                storage, "com.r5.core.web.addon.client.localstorage.LocalStorageServerRpc",
                "updateUuid", [boot["state"][storage]["generatedUuid"]],
            ]])
            ids = _component_ids(form)
            combo, pwd = ids.get("com.vaadin.ui.ComboBox"), ids.get("com.vaadin.ui.PasswordField")
            if combo is None or pwd is None:
                raise UninetUnavailable("wsp: login form not found")
            # Enter в поле пароля обрабатывает Panel — прародитель PasswordField.
            panel = next(p for p, kids in form["hierarchy"].items()
                         if any(pwd in form["hierarchy"].get(k, []) for k in kids))
            await _rpc(c, ui_id, csrf, 1, [[combo, "v", "v", ["newitem", ["s", wsp_login(email)]]]])
            await _rpc(c, ui_id, csrf, 2, [
                [pwd, "v", "v", ["text", ["s", password]]],
                [pwd, "v", "v", ["c", ["i", len(password)]]],
                [panel, "v", "v", ["actiontarget", ["c", pwd]]],
                [panel, "v", "v", ["action", ["s", "1"]]],
            ])
            session = _Session(c, app.group(1), ui_id, csrf, {})
            await session.reload()
            if LOGIN_VIEW in session.page.get("typeMappings", {}):
                # Неверный пароль: после логина снова рисуется LoginView (от языка не зависит).
                raise InvalidCredentials()
            yield session
    except (httpx.HTTPError, KeyError, ValueError, StopIteration) as exc:
        raise UninetUnavailable(f"wsp: {exc!r}") from exc


async def open_page(email: str, password: str) -> dict:
    """Залогиниться и вернуть UIDL страницы «Отметка посещения» (для ``texts``/``lessons``)."""
    # ponytail: логинимся на каждую проверку (5 запросов); держать сессию — если wsp начнёт ругаться.
    async with _session(email, password) as s:
        return s.page


async def mark(email: str, password: str, key: str) -> MarkResult:
    """Нажать «Отметиться» у пары ``key`` (см. ``Lesson.key``) от имени студента."""
    async with _session(email, password) as s:
        lesson = next((x for x in lessons(s.page) if x.key == key), None)
        if lesson is None:
            return MarkResult.CLOSED
        await s.click(lesson.button)
        await s.reload()
        still_open = any(x.key == key for x in lessons(s.page))
        return MarkResult.UNCONFIRMED if still_open else MarkResult.MARKED
