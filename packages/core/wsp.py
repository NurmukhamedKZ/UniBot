"""Клиент wsp.kbtu.kz/RegistrationOnline («Отметка посещения») — чистый HTTP.

Сайт на Vaadin 7: HTML — пустой бутстрап, весь UI приходит JSON-ом (UIDL).
Порядок: GET страницы (JSESSIONID + appId) → POST ``?v-<ts>`` с
``v-browserDetails`` (дерево компонентов + csrf) → первый rpc досылает форму
логина (ComboBox логина, PasswordField, Panel с Enter-действием) → логин двумя
rpc → повторный bootstrap отдаёт состояние страницы. Логин — не email, а
``n_xxxxx`` (часть email до @), пароль тот же, что у uninet.
"""

from __future__ import annotations

import json
import re
import ssl
import time
from pathlib import Path

import certifi
import httpx

from packages.core.uninet import InvalidCredentials, UninetUnavailable

BASE = "https://wsp.kbtu.kz/RegistrationOnline"
# Заглушка пустой страницы: ru (казахского перевода нет, kk показывает ru) и en.
NO_DISCIPLINES = {"Нет доступных дисциплин", "No disciplines available"}
# Заголовок страницы и подписи кнопок — в уведомлении не нужны (там своя кнопка).
SKIP_TEXTS = {"Отметка посещения", "Attendance mark", "Отметиться"}
LOGIN_VIEW = "kz.kbtu.officeregistrar.widget.api.LoginView"
TIMEOUT = 20

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


def _component_ids(uidl: dict) -> dict[str, str]:
    """{имя класса Vaadin: id компонента} — чтобы не хардкодить id формы логина."""
    names = {v: k for k, v in uidl.get("typeMappings", {}).items()}
    return {names.get(int(t)): cid for cid, t in uidl.get("types", {}).items()}


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


async def registration_texts(email: str, password: str) -> list[str]:
    """Залогиниться и вернуть тексты страницы «Отметка посещения»."""
    # ponytail: логинимся на каждую проверку (5 запросов); держать сессию — если wsp начнёт ругаться.
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
            _, page = await _bootstrap(c, app.group(1))
    except (httpx.HTTPError, KeyError, ValueError, StopIteration) as exc:
        raise UninetUnavailable(f"wsp: {exc!r}") from exc
    if LOGIN_VIEW in page.get("typeMappings", {}):
        # Неверный пароль: после логина снова рисуется LoginView (от языка не зависит).
        raise InvalidCredentials()
    return texts(page)
