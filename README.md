# rezka-cli — парсер и плеер для rezka-ua.tv

Приложение на Python для поиска, просмотра информации и **воспроизведения**
фильмов/сериалов с онлайн-кинотеатра [rezka-ua.tv](https://rezka-ua.tv).

## Структура

```
app.py               # CLI-приложение (поиск / инфо / ссылки / просмотр)
rezka/api.py         # клиент AJAX-API rezka (карточки, переводы, видео-ссылки)
rezka/scraper.py     # HTML-парсер (поиск, топ-100, новинки, каталог)
rezka/player.py      # воспроизведение/скачивание через mpv / ffplay / vlc / ffmpeg
tests/test_parser.py # офлайн-тесты разбора HTML/JSON
```

## Установка

```bash
pip install -r requirements.txt
# рекомендуемый внешний плеер (лучше всех играет HLS .m3u8):
#   apt install mpv   |   brew install mpv
```

## Использование

```bash
python app.py search "аватар"                 # поиск по названию
python app.py latest                          # обновления
python app.py top --kind serials              # ТОП-100 сериалов
python app.py catalog drama                   # каталог по жанру

# карточка + список переводов (target = URL страницы или ID):
python app.py info https://rezka-ua.tv/films/10409-after-earth.html

# прямые ссылки на видео (m3u8/mp4) выбранного перевода:
python app.py links 10409 --transl "Гид" --quality 720

# смотреть (автовыбор плеера mpv/ffplay/vlc):
python app.py watch 10409 --quality 720
python app.py watch 25438 --type serial --season 1 --episode 3 --player mpv

# скачать вместо просмотра:
python app.py watch 10409 --download

# проверить доступные зеркала:
python app.py mirrors
```

Переменные окружения / флаги: `--host` (`REZKA_HOST`) — зеркало сайта,
`--cookie` (`REZKA_COOKIE`) — cookie-строка из браузера,
`--cookies file` (`REZKA_COOKIES_FILE`) — файл cookies в формате Netscape.

## Как это работает

1. **API**: веб-плеер Rezka получает данные через
   `https://api.<хост>/v2/{film|serial}/data`:
   * `linkType=basic` → информация о фильме и список озвучек;
   * `linkType=all`   → прямые ссылки `.m3u8`/`.mp4` для выбранного
     перевода/качества (для сериалов + `season`/`episode`).
2. **Просмотр**: ссылки защищены по `Referer`, поэтому `player.py` запускает
   mpv с `--http-header-fields=Referer: https://<хост>/` (аналогично для
   ffplay/ffmpeg/VLC) либо скачивает mp4 поблочно через requests.
3. **HTML-парсер** (`scraper.py`) разбирает карточки
   `div.b-content__inline_item` на страницах поиска/топа.

## Антибот-защита (Anubis)

С 2024–2025 гг. rezka-ua.tv (и часть зеркал) закрывает HTTP-запросы от ботов
JS-challenge защитой **Anubis**. Приложение это учитывает:

* пытается использовать TLS-имперсонацию Chrome (`curl_cffi`) / `cloudscraper`;
* команда `mirrors` автоматически перебирает список зеркал
  (`rezka.pub`, `rezka.art`, `rezka.city`, `hdrezka.pro` …) ищет то, чей API
  отвечает без challenge;
* если ничего не проходит — откройте сайт в обычном браузере, скопируйте
  заголовок `Cookie` (DevTools → Network) и запустите:
  `REZKA_COOKIE="..." python app.py search "фильм"`.

Для гарантированного обхода JS-challenge можно также подключить Playwright
(headless-браузер passing cookies в `requests.Session`) — интерфейс `make_session()`
принимает любую совместимую сессию.

## Тесты

```bash
python -m unittest discover -s tests -v
```

## Дисклеймер

Проект создан в образовательных целях. Используйте только контент, права на
просмотр которого вы имеете, и соблюдайте законодательство своей страны.
