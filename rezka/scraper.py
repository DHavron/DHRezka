"""
HTML-парсер страниц rezka-ua.tv: поиск, каталог, новинки.

Страницы сайта защищены антибот-защитой Anubis (JS-challenge), поэтому
обычный requests может не пройти. Варианты:

1. Использовать рабочее зеркало без challenge (см. MIRROR_HOSTS).
2. Пробросить cookies из настоящего браузера (строка cookie или файл
   Netscape cookies.txt) — см. rezka.net.make_session().
3. Запускать браузер через Selenium/Playwright и подставлять session.

Класс RezkaScraper работает с любым requests.Session — можно передать
сессию, полученную из браузера.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .api import BASE_SITE
from .net import (AntiBotError, load_cookies, make_session,  # noqa: F401
                  raise_for_antibot)


@dataclass
class SearchResult:
    """Карточка фильма/сериала из выдачи."""

    title: str
    url: str
    content_id: int
    kind: str                  # "film" | "serial"
    year: str = ""
    original_title: str = ""
    poster: str = ""
    extra: str = ""            # "сериалы / 2 сезон 8 серия" и т.п.


class RezkaScraper:
    """Парсер HTML-страниц rezka-ua.tv (и зеркал)."""

    def __init__(self, base_url: str = BASE_SITE,
                 session: Optional[requests.Session] = None, timeout: int = 20):
        self.base_url = base_url.rstrip("/")
        self.session = session or make_session()
        self.timeout = timeout

    # ------------------------------------------------------------------ #
    def _get_soup(self, path: str) -> BeautifulSoup:
        resp = self.session.get(self.base_url + path, timeout=self.timeout)
        # Антибот может отвечать и HTTP 403, и «вежливым» 200 с challenge-страницей,
        # поэтому сначала проверяем содержимое, потом поднимаем HTTP-ошибку.
        try:
            raise_for_antibot(resp)
        except AntiBotError as exc:
            hint = ""
            cookie = ""
            try:
                cookie = self.session.headers.get("Cookie", "") or ""
            except Exception:  # noqa: BLE001
                pass
            m = re.search(r"([\w.-]+)-anubis-auth-", cookie)
            cur_host = re.sub(r"^https?://", "", self.base_url).split("/")[0]
            if m and m.group(1) != cur_host:
                hint = (f" Ваши Cookie выданы для домена «{m.group(1)}», а запрос "
                        f"идёт к «{cur_host}» — откройте в ⚙ Настройках то же "
                        f"зеркало, где копировали Cookie.")
            elif not cookie:
                hint = " Cookie не заданы — вставьте их в ⚙ Настройках."
            raise AntiBotError(
                f"{exc}.{hint} Обновите Cookie (F12 → Network → заголовок "
                "Cookie целиком) или используйте зеркало без проверки."
            ) from None
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        return soup

    # ------------------------------------------------------------------ #
    # Разбор карточек b-code (<div class="b-content__inline_item">)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_items(soup: BeautifulSoup) -> list[SearchResult]:
        results: list[SearchResult] = []
        for item in soup.select("div.b-content__inline_item"):
            link = item.select_one("a.b-content__inline_item-link")
            if not link:
                continue
            title_el = link.select_one("ins")
            extra_el = link.select_one("span")
            href = link.get("href", "")
            m = re.search(r"(\d+)[-\w]*\.html", href)
            if not m:
                continue
            kind = "serial" if re.search(r"/(serial|series)", href.lower()) else "film"
            poster_el = item.select_one("img")
            poster = ""
            if poster_el:
                poster = poster_el.get("data-src") or poster_el.get("src") or ""
                if poster.startswith("//"):
                    poster = "https:" + poster
            year = ""
            if extra_el:
                ym = re.search(r"\b(19|20)\d{2}\b", extra_el.get_text())
                if ym:
                    year = ym.group(0)
            orig_el = link.find(string=True, recursive=False)
            results.append(SearchResult(
                title=title_el.get_text(strip=True) if title_el else "",
                url=href if href.startswith("http") else BASE_SITE + href,
                content_id=int(m.group(1)),
                kind=kind,
                year=year,
                original_title=(orig_el or "").strip() if isinstance(orig_el, str) else "",
                poster=poster,
                extra=extra_el.get_text(strip=True) if extra_el else "",
            ))
        return results

    # ------------------------------------------------------------------ #
    # Публичные методы
    # ------------------------------------------------------------------ #
    def search(self, query: str, page: int = 1) -> list[SearchResult]:
        """Поиск по названию: /search/?qs=запрос&sort_by=date&sort_order=desc&page=N"""
        path = f"/search/?qs={requests.utils.quote(query)}&sort_by=date&sort_order=desc"
        if page > 1:
            path += f"&page={page}"
        return self._parse_items(self._get_soup(path))

    def latest_updates(self, page: int = 1) -> list[SearchResult]:
        """Обновления контента: /latest-updates/N/"""
        path = f"/latest-updates/{page}/" if page > 1 else "/latest-updates/"
        return self._parse_items(self._get_soup(path))

    def catalog(self, genre: str = "action", page: int = 1,
                ordering: str = "rating") -> list[SearchResult]:
        """Каталог: /films/{genre}/order:{ordering}/page/{N}/  (жанры в URL на англ.)"""
        path = f"/films/{genre}/order:{ordering}/page/{page}/"
        return self._parse_items(self._get_soup(path))

    def top_movies(self, page: int = 1) -> list[SearchResult]:
        """ТОП фильмов: /top100/movies/page/{N}/"""
        return self._parse_items(self._get_soup(f"/top100/movies/page/{page}/"))

    def top_serials(self, page: int = 1) -> list[SearchResult]:
        """ТОП сериалов: /top100/serials/page/{N}/"""
        return self._parse_items(self._get_soup(f"/top100/serials/page/{page}/"))


if __name__ == "__main__":
    sc = RezkaScraper()
    try:
        for r in sc.search("терра")[:10]:
            print(f"[{r.content_id}] {r.title} ({r.year}) {r.kind} -> {r.url}")
    except Exception as exc:  # noqa: BLE001
        print("Не удалось получить страницу:", exc)
