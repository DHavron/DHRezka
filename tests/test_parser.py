"""Офлайновые тесты парсера (без сети): разбор HTML и JSON, URL-парсер."""

import unittest

from rezka.api import RezkaClient, parse_content_url
from rezka.scraper import RezkaScraper


SAMPLE_PAGE = """
<html><body>
<div class="b-content__inline_item">
  <div class="b-content__inline_item-cover">
    <a href="/films/10409-after-earth.html"><img data-src="//rezka-ua.tv/poster/10409.jpg"></a>
  </div>
  <div class="b-content__inline_item-info">
    <a href="/films/10409-after-earth.html" class="b-content__inline_item-link">
      После нас Земля<ins>После нас Земля</ins>
      <span>2013 | приключения</span>
    </a>
  </div>
</div>
<div class="b-content__inline_item">
  <div class="b-content__inline_item-info">
    <a href="/serial/25438-sherlok.html" class="b-content__inline_item-link">
      Шерлок<ins>Шерлок</ins>
      <span>сериалы / 4 сезон 3 серия | 2010</span>
    </a>
  </div>
</div>
</body></html>
"""

SAMPLE_BASIC_JSON = {
    "movieInfo": {
        "name": "После нас Земля",
        "originalName": "After Earth",
        "year": "2013",
        "description": "<p>Крушение на неизвестной планете.</p>",
        "genres": [{"title": "фантастика"}, {"title": "приключения"}],
        "countries": [{"title": "США"}],
        "kinopoisk": "6.2",
        "imdb": "4.8",
        "time": "103 мин.",
    },
    "items": [
        {"internal_description": "Гид", "description": "Дублированный"},
        {"internal_description": "NewStudio", "description": "OneMedia"},
    ],
}

SAMPLE_ALL_JSON = {
    "videoUrl": [
        "https://example.com/hls/720p/index.m3u8",
        "https://example.com/mp4/720.mp4",
    ]
}


class UrlParseTests(unittest.TestCase):
    def test_film(self):
        self.assertEqual(parse_content_url(
            "https://rezka-ua.tv/films/10409-after-earth.html"), ("film", 10409))

    def test_serial(self):
        self.assertEqual(parse_content_url(
            "https://rezka-ua.tv/serial/25438-sherlok.html"), ("serial", 25438))

    def test_bad(self):
        with self.assertRaises(ValueError):
            parse_content_url("https://rezka-ua.tv/")


class HtmlParseTests(unittest.TestCase):
    def setUp(self):
        from bs4 import BeautifulSoup
        self.soup = BeautifulSoup(SAMPLE_PAGE, "lxml")

    def test_items(self):
        rows = RezkaScraper._parse_items(self.soup)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].content_id, 10409)
        self.assertEqual(rows[0].kind, "film")
        self.assertEqual(rows[0].title, "После нас Земля")
        self.assertEqual(rows[0].year, "2013")
        self.assertIn("10409.jpg", rows[0].poster)
        self.assertTrue(rows[0].url.startswith("https://rezka-ua.tv/films/"))
        self.assertEqual(rows[1].kind, "serial")
        self.assertEqual(rows[1].content_id, 25438)


class ApiParseTests(unittest.TestCase):
    def test_translators(self):
        ts = RezkaClient._parse_translators(SAMPLE_BASIC_JSON)
        self.assertEqual([t.code for t in ts], ["Гид", "NewStudio"])

    def test_videos(self):
        client = RezkaClient.__new__(RezkaClient)
        client._fetch = lambda kind, params: SAMPLE_ALL_JSON  # type: ignore
        links = client.get_videos(10409, "Гид")
        self.assertEqual(len(links), 2)
        self.assertTrue(links[0].is_hls)
        self.assertFalse(links[1].is_hls)

    def test_load_content(self):
        client = RezkaClient.__new__(RezkaClient)
        client._fetch = lambda kind, params: SAMPLE_BASIC_JSON  # type: ignore
        card = client.load_content(10409, "film")
        self.assertEqual(card.name, "После нас Земля")
        self.assertEqual(card.year, "2013")
        self.assertEqual(card.genres, ["фантастика", "приключения"])
        self.assertIn("Крушение", card.description)
        self.assertNotIn("<p>", card.description)
        self.assertEqual(len(card.translators), 2)


if __name__ == "__main__":
    unittest.main()
