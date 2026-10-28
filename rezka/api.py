"""
Клиент REST API rezka-ua.tv.

Сайт rezka-ua.tv (онлайн-кинотеатр "Rezka") имеет внутреннее AJAX-API,
через которое веб-плеер получает данные фильмов/сериалов и ссылки на
видео-файлы (m3u8 / mp4).

API:
    GET https://api.rezka-ua.tv/v2/{film|serial}/data
    Параметры:
        id            - идентификатор фильма/сериала (из URL страницы)
        type          - тип контента (film / serial)
        transl        - название перевода
        linkType      - "basic" — список переводов; "all" — ссылки на видео
        quality       - "1080" | "720" | "480" | "360"
        translator    - код перевода (для сериалов)
        season/episode - только для сериалов

Примеры страниц:
    https://rezka-ua.tv/films/10409-after-earth.html
    https://rezka-ua.tv/serial/25438-sherlok.html
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import requests

try:  # curl_cffi даёт TLS-имперсонацию Chrome (помогает против антиботов)
    from curl_cffi.requests import Session as CSession  # type: ignore
except ImportError:  # pragma: no cover
    CSession = None  # type: ignore[assignment,misc]

from .net import (AntiBotError, looks_antibot, make_session,  # noqa: F401
                  raise_for_antibot)

BASE_SITE = "https://rezka-ua.tv"

# Зеркала Rezka: у каждого сайта своё API-поддомен (api.<host>).
# rezka-ua.tv может быть закрыт антибот-защитой (Anubis) — тогда можно
# использовать одно из зеркал, структура API у них идентична.
MIRROR_HOSTS = [
    "rezka-ua.tv",
    "rezka.pub",
    "rezka.art",
    "rezka.city",
    "hdrezka.pro",
]

API_URL = "https://api.{host}/v2/{kind}/data"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Referer": BASE_SITE + "/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-UA,ru;q=0.9,en;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
}

# Регулярка для извлечения id из URL страницы фильма/сериала
CONTENT_URL_RE = re.compile(
    r"/(?:films?|serials?|cartoons?|anime|documentary|series)/.*?(\d+)[-\w]*\.html",
    re.IGNORECASE,
)


@dataclass
class VideoLink:
    """Одна ссылка на видеопоток."""

    url: str
    quality: str = ""          # "1080", "720", ...
    kind: str = ""             # "m3u8", "mp4", ...
    direct_link: bool = False

    @property
    def is_hls(self) -> bool:
        return ".m3u8" in self.url or self.kind == "m3u8"


@dataclass
class Translator:
    """Вариант озвучки/перевода."""

    code: str                  # внутренний код, используется API
    title: str                 # читаемое название
    videos: list[VideoLink] = field(default_factory=list)


@dataclass
class Content:
    """Фильм или сериал."""

    content_id: int
    kind: str                  # "film" | "serial"
    name: str = ""
    original_name: str = ""
    year: str = ""
    description: str = ""
    genres: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    poster: str = ""
    rating_kinopoisk: str = ""
    rating_imdb: str = ""
    time: str = ""
    seasons: list[int] = field(default_factory=list)
    translators: list[Translator] = field(default_factory=list)


def parse_content_url(url: str) -> tuple[str, int]:
    """Разобрать URL страницы rezka → (kind, content_id).

    >>> parse_content_url("https://rezka-ua.tv/films/10409-after-earth.html")
    ('film', 10409)
    >>> parse_content_url("https://rezka-ua.tv/serial/25438-sherlok.html")
    ('serial', 25438)
    """
    m = CONTENT_URL_RE.search(url)
    if not m:
        raise ValueError(f"Не удалось разобрать URL: {url}")
    seg = url.lower()
    kind = "serial" if re.search(r"/(serial|series)", seg) else "film"
    return kind, int(m.group(1))


class RezkaClient:
    """Работа с API Rezka (api.<host>/v2/...).

    host по умолчанию — rezka-ua.tv. Если основной домен закрыт антиботом,
    можно передать другой хост (зеркало) или вызвать find_working_host().
    """

    def __init__(self, host: str = "rezka-ua.tv",
                 session: Optional[requests.Session] = None, timeout: int = 20):
        self.host = host
        sess = session or requests.Session()
        # Не перетирать пользовательские заголовки (Cookie/UA из GUI-настроек),
        # если сессия уже была настроена через scraper.make_session().
        for k, v in DEFAULT_HEADERS.items():
            if k not in sess.headers:
                sess.headers[k] = v
        if "Referer" not in sess.headers:
            sess.headers["Referer"] = f"https://{host}/"
        self.session = sess
        self.timeout = timeout

    # ------------------------------------------------------------------ #
    # Поиск рабочего зеркала
    # ------------------------------------------------------------------ #
    @staticmethod
    def _copy_session(session: requests.Session, host: str) -> requests.Session:
        """Скопировать сессию (curl_cffi / cloudscraper / requests), заменив Referer.

        Копия нужна, чтобы для каждого зеркала подставить свой Referer, но
        сохранить пользовательские заголовки (Cookie, User-Agent).
        """
        new: requests.Session
        if CSession is not None and isinstance(session, CSession):
            kw = {}
            impersonate = getattr(session, "impersonate", None)
            if impersonate:
                kw["impersonate"] = impersonate
            new = CSession(**kw)
        else:
            try:
                new = type(session)()  # cloudscraper и пр.
            except Exception:  # noqa: BLE001
                new = requests.Session()
        try:
            new.headers.update(dict(session.headers))
        except Exception:  # noqa: BLE001
            pass
        try:
            new.cookies.update(session.cookies)
        except Exception:  # noqa: BLE001
            pass
        new.headers["Referer"] = f"https://{host}/"
        return new

    @classmethod
    def find_working_host(cls, hosts: Optional[list[str]] = None,
                          probe_id: int = 10409, timeout: int = 10,
                          session: Optional[requests.Session] = None,
                          on_progress=None) -> Optional[str]:
        """Перебрать зеркала и вернуть первое, чей API отвечает валидным JSON.

        Ошибки отдельных зеркал (DNS, таймауты, антибот) не должны обрывать
        проверку остальных — для каждого хоста она изолирована.
        """
        base = session or make_session()
        for host in (hosts or MIRROR_HOSTS):
            if on_progress:
                try:
                    on_progress(host)
                except Exception:  # noqa: BLE001
                    pass
            try:
                sess = cls._copy_session(base, host)
                client = cls(host=host, timeout=timeout, session=sess)
                data = client._fetch("film", {
                    "id": probe_id, "type": "film",
                    "linkType": "basic", "quality": "720",
                })
                if isinstance(data, dict) and (data.get("items") or data.get("movieInfo")):
                    return host
            except Exception:  # noqa: BLE001
                # DNS не резолвится, зеркала нет, таймаут, JS-challenge — пробуем следующее
                continue
        return None

    # ------------------------------------------------------------------ #
    # Низкоуровневый запрос к API
    # ------------------------------------------------------------------ #
    def _fetch(self, kind: str, params: dict) -> dict:
        url = API_URL.format(host=self.host, kind=kind)
        # XHR-запрос (X-Requested-With) при закрытом доступе отдаёт голые
        # 401/403 без HTML — их нельзя ловить по тексту страницы, поэтому
        # такие статусы считаем антиботом сразу.
        xhr = "XMLHttpRequest" in self.session.headers.get(
            "X-Requested-With", "")
        resp = self.session.get(url, params=params, timeout=self.timeout)
        status = getattr(resp, "status_code", 200)
        text = ""
        try:
            text = resp.text
        except Exception:  # noqa: BLE001
            pass
        if status in (401, 403):
            hint = self._cookie_hint()
            raise AntiBotError(
                f"api.{self.host}: HTTP {status} — доступ закрыт "
                "(антибот Anubis / нужен рабочий Cookie)." + hint
            )
        if status in (429, 451, 503):
            raise AntiBotError(
                f"api.{self.host}: HTTP {status} — запросов слишком много "
                "или доступ ограничен. Повторите позже или смените зеркало."
            )
        if status >= 400 and not text.strip():
            raise AntiBotError(
                f"api.{self.host}: HTTP {status} без ответа — скорее всего "
                "API недоступен с текущими Cookie." + self._cookie_hint()
            )
        # JSON-ответы XHR иногда содержат сообщение об ошибке вместо данных
        data = None
        ctype = ""
        try:
            ctype = resp.headers.get("Content-Type", "")
        except Exception:  # noqa: BLE001
            pass
        try:
            data = resp.json()
        except ValueError:
            if looks_antibot(text):
                raise AntiBotError(
                    f"api.{self.host} вернул страницу антибот-проверки "
                    "вместо JSON. Нужен свежий Cookie того же зеркала, что "
                    "выбран в настройках." + self._cookie_hint()
                ) from None
            if xhr and status == 200 and "json" not in ctype.lower():
                raise AntiBotError(
                    f"api.{self.host}: ответ не является JSON (Content-Type: "
                    f"{ctype or 'нет'}). Похоже на блокировку запроса "
                    "(обновите Cookie или смените зеркало).")
            raise RuntimeError(
                f"Хост api.{self.host} вернул не JSON (возможно, включена "
                f"антибот-защита Anubis). Используйте другое зеркало — "
                f"RezkaClient.find_working_host()"
            ) from None
        if isinstance(data, dict) and data.get("error"):
            err = str(data["error"]).lower()
            if any(w in err for w in ("auth", "forbidden", "denied",
                                      "robot", "anubis")):
                raise AntiBotError(
                    f"api.{self.host}: отказ доступа ({data['error']}). "
                    "Обновите Cookie из браузера." + self._cookie_hint())
            raise RuntimeError(f"Ошибка API: {data['error']}")
        return data

    def _cookie_hint(self) -> str:
        """Подсказка про совпадение домена cookie и текущего хоста."""
        try:
            cookie = self.session.headers.get("Cookie", "") or ""
        except Exception:  # noqa: BLE001
            cookie = ""
        m = re.search(r"([\w.-]+)-anubis-auth-", cookie)
        if m and m.group(1) != self.host:
            return (f"\n\nВажно: ваши Cookie выданы для домена «{m.group(1)}», "
                    f"а запрос идёт к «{self.host}». Скопируйте Cookie на том "
                    f"же зеркале, которое выбрано в настройках, или смените "
                    f"хост на «{m.group(1)}».")
        if not cookie:
            return ("\n\nCookie не заданы: ⚙ Настройки → «Заголовок Cookie» → "
                    "вставьте значение из DevTools браузера.")
        return ""

    # ------------------------------------------------------------------ #
    # Список переводов (linkType=basic)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_translators(data: dict) -> list[Translator]:
        translators: list[Translator] = []
        items = data.get("items") or data.get("translators") or []
        for it in items:
            if not isinstance(it, dict):
                continue
            code = str(it.get("internal_description") or it.get("translator") or "").strip()
            title = str(it.get("description") or it.get("title") or code).strip()
            if code:
                translators.append(Translator(code=code, title=title))
        return translators

    def get_translators(self, content_id: int, kind: str = "film",
                        quality: str = "1080") -> list[Translator]:
        params = {"id": content_id, "type": kind, "linkType": "basic",
                  "quality": quality}
        data = self._fetch(kind, params)
        return self._parse_translators(data)

    # ------------------------------------------------------------------ #
    # Ссылки на видео выбранного перевода (linkType=all)
    # ------------------------------------------------------------------ #
    def get_videos(self, content_id: int, translator: str, kind: str = "film",
                   quality: str = "1080", season: Optional[int] = None,
                   episode: Optional[int] = None) -> list[VideoLink]:
        params = {
            "id": content_id,
            "type": kind,
            "linkType": "all",
            "quality": quality,
            "transl": translator,
            "translator": translator,
        }
        if kind == "serial":
            if season is not None:
                params["season"] = season
            if episode is not None:
                params["episode"] = episode
        data = self._fetch(kind, params)
        links: list[VideoLink] = []
        seen: set[str] = set()
        entries = (data.get("videoUrl") or data.get("links") or
                   data.get("items") or [])
        # videoUrl может быть списком строк или списком словарей
        for entry in entries:
            if isinstance(entry, str):
                url, q, k = entry, "", ""
            elif isinstance(entry, dict):
                url = entry.get("url") or entry.get("link") or entry.get("src") or ""
                q = str(entry.get("quality") or entry.get("label") or "")
                k = str(entry.get("type") or entry.get("kind") or "")
            else:
                continue
            if not url or url in seen:
                continue
            seen.add(url)
            if not k:
                k = "m3u8" if ".m3u8" in url else ("mp4" if ".mp4" in url else "")
            links.append(VideoLink(url=url, quality=q, kind=k, direct_link=True))
        return links

    # ------------------------------------------------------------------ #
    # Высокоуровневая загрузка карточки контента
    # ------------------------------------------------------------------ #
    def load_content(self, content_id: int, kind: str = "film",
                     quality: str = "1080") -> Content:
        """Загрузить карточку + список переводов. Видео подтягивается лениво."""
        params = {"id": content_id, "type": kind, "linkType": "basic",
                  "quality": quality}
        data = self._fetch(kind, params)
        info = data.get("movieInfo") or {}
        content = Content(
            content_id=content_id,
            kind=kind,
            name=info.get("name") or info.get("russianName") or "",
            original_name=info.get("originalName") or "",
            year=str(info.get("year") or ""),
            description=re.sub(r"<[^>]+>", "", info.get("description") or ""),
            genres=[g.get("title", "") if isinstance(g, dict) else str(g)
                    for g in (info.get("genres") or [])],
            countries=[c.get("title", "") if isinstance(c, dict) else str(c)
                       for c in (info.get("countries") or [])],
            poster=info.get("poster") or info.get("image") or "",
            rating_kinopoisk=str(info.get("kinopoisk") or ""),
            rating_imdb=str(info.get("imdb") or ""),
            time=str(info.get("time") or info.get("runtime") or ""),
        )
        content.translators = self._parse_translators(data)
        seasons = data.get("seasons") or []
        if seasons:
            content.seasons = [int(s.get("id", s) if isinstance(s, dict) else s)
                               for s in seasons]
        return content

    def load_serial_episodes(self, content_id: int, translator: str,
                             quality: str = "720") -> dict[int, list[int]]:
        """Для сериала получить карту сезон → номера серий."""
        params = {
            "id": content_id, "type": "serial", "linkType": "all",
            "quality": quality, "transl": translator, "translator": translator,
        }
        data = self._fetch("serial", params)
        result: dict[int, list[int]] = {}
        for entry in (data.get("videoUrl") or data.get("links") or []):
            if not isinstance(entry, dict):
                continue
            s = entry.get("season")
            e = entry.get("episode") or entry.get("series")
            if s is None:
                continue
            result.setdefault(int(s), []).append(int(e) if e else 0)
        return result


if __name__ == "__main__":  # быстрая самопроверка
    host = RezkaClient.find_working_host() or "rezka-ua.tv"
    print(f"Рабочий хост: {host}")
    client = RezkaClient(host=host)
    test_url = "https://rezka-ua.tv/films/10409-after-earth.html"
    kind, cid = parse_content_url(test_url)
    print(f"kind={kind}, id={cid}")
    try:
        card = client.load_content(cid, kind)
        print(f"{card.name} ({card.year})")
        for t in card.translators[:10]:
            print(f"  [{t.code}] {t.title}")
    except Exception as exc:  # noqa: BLE001
        print(f"Запрос не удался: {exc}")
