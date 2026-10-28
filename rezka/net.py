"""
Сетевые утилиты Rezka: сборка сессий с браузерными заголовками/cookies и
детектор страниц антибот-защиты (Anubis и аналоги).

Вынесено в отдельный модуль, чтобы rezka.api и rezka.scraper использовали
одну и ту же логику без циклического импорта.
"""

from __future__ import annotations

import http.cookiejar
import re
from typing import Optional

import requests

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


class AntiBotError(RuntimeError):
    """Страница закрыта JS-challenge (Anubis и т.п.)."""


def load_cookies(cookie_file: str) -> http.cookiejar.CookieJar:
    """Загрузить cookies в формате Netscape (browsercookie / EditThisCookie)."""
    jar = http.cookiejar.MozillaCookieJar(cookie_file)
    jar.load(cookie_file, ignore_discard=True, ignore_expires=True)
    return jar


def make_session(cookie_string: Optional[str] = None,
                 cookie_file: Optional[str] = None,
                 impersonate: bool = True,
                 user_agent: Optional[str] = None) -> requests.Session:
    """Собрать сессию с заголовками браузера и (опционально) cookies.

    cookie_string — значение заголовка Cookie, скопированное из DevTools
    после прохождения JS-challenge в браузере.

    Если установлен curl_cffi и impersonate=True, используется TLS-подпись
    Chrome; при наличии cloudscraper он применяется как запасной вариант.
    """
    sess: requests.Session
    if impersonate:
        try:
            from curl_cffi.requests import Session as CSession
            sess = CSession(impersonate="chrome")  # type: ignore[assignment]
        except Exception:  # noqa: BLE001
            try:
                import cloudscraper
                sess = cloudscraper.create_scraper()
            except Exception:  # noqa: BLE001
                sess = requests.Session()
    else:
        sess = requests.Session()

    base_headers = {
        "User-Agent": user_agent or DEFAULT_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ru-UA,ru;q=0.9,en;q=0.8",
    }
    for k, v in base_headers.items():
        try:
            sess.headers[k] = v
        except Exception:  # noqa: BLE001
            pass
    if cookie_string:
        sess.headers["Cookie"] = cookie_string.strip()
    if cookie_file:
        sess.cookies.update(load_cookies(cookie_file))
    return sess


# --------------------------------------------------------------------------- #
# Детекторы антибот-страниц
# --------------------------------------------------------------------------- #
_ANTIBOT_MARKS = (
    "anubis",
    "checking your browser",
    "just a moment",
    "attention required",
    "cf-browser-verification",
    "access denied",
    "making sure you are not a robot",
)


def looks_antibot(text: str) -> bool:
    """True, если ответ похож на страницу JS-challenge, а не на контент сайта."""
    if not text:
        return False
    head = text[:4000].lower()
    body = text.lower()
    for mark in _ANTIBOT_MARKS:
        if mark in head or mark in body[:20000]:
            return True
    # типичные признаки Anubis глубже в разметке
    if ("anubis" in body and ("challenge" in body or "proof-of-work" in body)):
        return True
    return False


def raise_for_antibot(resp: requests.Response) -> None:
    """Поднять AntiBotError, если ответ — страница защиты (в т.ч. HTTP 403)."""
    status = getattr(resp, "status_code", 200)
    text = ""
    try:
        text = resp.text
    except Exception:  # noqa: BLE001
        pass
    if status in (403, 429, 451, 503) and looks_antibot(text):
        raise AntiBotError(
            f"HTTP {status}: доступ закрыт антибот-защитой (Anubis)."
        )
    if looks_antibot(text) and "<html" in text[:2000].lower():
        # 200 OK, но внутри — challenge-страница
        raise AntiBotError("Сайт вернул страницу антибот-проверки вместо контента.")
