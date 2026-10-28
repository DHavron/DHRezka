"""
Сетевые утилиты Rezka: сборка сессий с браузерными заголовками/cookies и
детектор страниц антибот-защиты (Anubis и аналоги).

Вынесено в отдельный модуль, чтобы rezka.api и rezka.scraper использовали
одну и ту же логику без циклического импорта.

Дополнительно здесь живёт разбор Cookie-строки из браузера: очистка от
мусора («Cookie:», кавычки, переводы строк), проверка полноты и анализ
токена Anubis (`techaro.lol-anubis-auth-*`) — по его JWT видно, какой
домен выдал куки и не истёк ли он.
"""

from __future__ import annotations

import base64
import http.cookiejar
import json
import re
import time
from typing import Optional

import requests

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


class AntiBotError(RuntimeError):
    """Страница закрыта JS-challenge (Anubis и т.п.)."""


# --------------------------------------------------------------------------- #
# Разбор и валидация Cookie из браузера
# --------------------------------------------------------------------------- #
ANUBIS_COOKIE_RE = re.compile(r"(?:^|[;\s])([\w.-]*anubis[\w.-]*=[\w.%~+/-]+=*)",
                              re.IGNORECASE)
SESSION_COOKIE_RE = re.compile(r"(?:^|[;\s])(PHPSESSID|dle_user_token|_vc_id)=",
                               re.IGNORECASE)


def clean_cookie_string(raw: str) -> str:
    """Привести вставленный из браузера текст к чистой строке «a=b; c=d».

    Терпимо относится к типичным способам копирования:
      * «Cookie: name=value; ...» (вместе с именем заголовка);
      * значение в двойных кавычках;
      * переносы строк / табы внутри;
      * завершающая точка с запятой.
    """
    if not raw:
        return ""
    text = raw.strip()
    # снимаем обрамляющие кавычки, если скопировали вместе с ними
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
    # убираем имя заголовка в начале
    text = re.sub(r"^\s*cookie\s*:\s*", "", text, flags=re.IGNORECASE)
    # сворачиваем переводы строк/табы
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    # склеиваем разрывы вида «...; \n name2=value2»
    parts = [p.strip().rstrip(";").strip() for p in text.split(";")]
    parts = [p for p in parts if "=" in p]          # только пары name=value
    return "; ".join(parts)


def cookie_pairs(cookie_string: str) -> dict[str, str]:
    """Разобрать чистую Cookie-строку в словарь {имя: значение}."""
    out: dict[str, str] = {}
    for part in (cookie_string or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def decode_anubis_jwt(cookie_string: str) -> Optional[dict]:
    """Найти в куках Anubis-токен и декодировать его JWT payload.

    Возвращает None, если anubis-куки нет или токен не декодируется.
    Поля payload: action ('CHALLENGE'/'ACCESS'), exp, iat, nbf и т.д.
    """
    m = ANUBIS_COOKIE_RE.search(cookie_string or "")
    if not m:
        return None
    value = m.group(1).split("=", 1)[1].rstrip("=")
    # токен может быть закодирован как cookie value — уберём экранирование
    value = re.sub(r"%22|^\"|\"$", "", value)
    jwt = value.split(".")
    if len(jwt) < 2:
        return None
    try:
        seg = jwt[1] + "=" * (-len(jwt[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(seg))
    except Exception:  # noqa: BLE001
        return None
    return payload if isinstance(payload, dict) else None


def check_cookie(cookie_string: str, host: str = "") -> list[str]:
    """Вернуть список предупреждений о Cookie (пустой список = всё в порядке).

    Проверяется: наличие Anubis-токена, наличие сессионных куков PHPSESSID,
    срок действия токена (exp) и совпадение домена, которому он выдан.
    """
    warnings: list[str] = []
    pairs = cookie_pairs(cookie_string)
    if not pairs:
        return ["Cookie пустой — вставьте значение заголовка Cookie из браузера."]

    anubis_val = None
    anubis_name = None
    for k, v in pairs.items():
        if "anubis" in k.lower():
            anubis_name, anubis_val = k, v
            break

    if anubis_val is None:
        warnings.append("В куках нет токена защиты Anubis "
                        "(вида techaro.lol-anubis-auth-*=eyJ...).")
    else:
        payload = decode_anubis_jwt(cookie_string)
        if payload:
            exp = payload.get("exp")
            if isinstance(exp, (int, float)) and exp < time.time():
                warnings.append("Токен Anubis просрочен — откройте сайт в "
                                "браузере заново и скопируйте Cookie ещё раз.")
            issued_host = re.sub(r"^[\w.-]*?anubis-auth-", "",
                                 anubis_name or "", flags=re.IGNORECASE)
            issued_host = issued_host.strip(".")
            if host and issued_host and issued_host != host:
                warnings.append(
                    f"Cookie выданы для домена «{issued_host}», а приложение "
                    f"работает с «{host}». Скопируйте Cookie на том же зеркале, "
                    f"которое выбрано в настройках (или поменяйте хост на "
                    f"«{issued_host}»).")
            if payload.get("action") == "CHALLENGE":
                warnings.append(
                    "Это промежуточная кука проверки (action=CHALLENGE). Обычно "
                    "она работает, пока не истёк exp, но надёжнее скопировать "
                    "Cookie со страницы, где проверка уже завершена.")

    if not SESSION_COOKIE_RE.search(cookie_string or ""):
        warnings.append("Нет сессионных куков (PHPSESSID / dle_user_token) — "
                        "похоже, скопирована только часть заголовка Cookie.")
    return warnings


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
        sess.headers["Cookie"] = clean_cookie_string(cookie_string)
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
