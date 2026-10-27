#!/usr/bin/env python3
"""
rezka-cli — приложение для поиска и просмотра фильмов/сериалов с rezka-ua.tv.

Примеры:
    # Поиск
    python app.py search "аватар"

    # ТОП-100 / новинки
    python app.py top --kind movies
    python app.py latest

    # Карточка фильма по URL или ID (переводы, качество)
    python app.py info https://rezka-ua.tv/films/10409-after-earth.html

    # Получить прямые ссылки на видео
    python app.py links 10409 --type film --transl "Гид" --quality 720

    # Смотреть (mpv/ffplay/vlc — автовыбор) или скачать
    python app.py watch 10409 --type film --quality 720
    python app.py watch 25438 --type serial --season 1 --episode 3 --player mpv
    python app.py watch 10409 --download

    # Автопоиск рабочего зеркала
    python app.py mirrors
"""

from __future__ import annotations

import argparse
import os
import sys

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
except ImportError:  # rich необязателен
    Console = None

from rezka.api import RezkaClient, parse_content_url, MIRROR_HOSTS
from rezka.scraper import RezkaScraper, make_session
from rezka.player import play_or_download, available_players

console = Console() if Console else None


def out(*args):
    if console:
        console.print(*args)
    else:
        print(*[str(a) for a in args])


# --------------------------------------------------------------------- #
# Команды
# --------------------------------------------------------------------- #
def cmd_search(sc: RezkaScraper, args) -> int:
    rows = sc.search(args.query, page=args.page)
    if not rows:
        out("[yellow]Ничего не найдено.[/]")
        return 1
    table = Table(title=f"Поиск: {args.query}") if console else None
    if table:
        table.add_column("ID", justify="right")
        table.add_column("Тип")
        table.add_column("Название")
        table.add_column("Год")
        table.add_column("URL", overflow="fold")
        for r in rows:
            table.add_row(str(r.content_id), r.kind, r.title, r.year, r.url)
        console.print(table)
    else:
        for r in rows:
            print(f"[{r.content_id}] {r.kind} | {r.title} ({r.year}) | {r.url}")
    return 0


def cmd_list(sc: RezkaScraper, args, rows) -> int:
    if not rows:
        out("[yellow]Пусто.[/]")
        return 1
    if console:
        table = Table()
        table.add_column("ID", justify="right")
        table.add_column("Тип")
        table.add_column("Название")
        table.add_column("Доп.")
        table.add_column("URL", overflow="fold")
        for r in rows:
            table.add_row(str(r.content_id), r.kind, r.title, r.extra or r.year, r.url)
        console.print(table)
    else:
        for r in rows:
            print(f"[{r.content_id}] {r.kind} | {r.title} | {r.extra}")
    return 0


def _resolve_target(args) -> tuple[str, int]:
    """Из аргументов команды получить (kind, content_id)."""
    target = args.target
    if str(target).startswith("http"):
        return parse_content_url(target)
    kind = getattr(args, "type", None) or "film"
    return kind, int(target)


def cmd_info(client: RezkaClient, args) -> int:
    kind, cid = _resolve_target(args)
    card = client.load_content(cid, kind, quality=args.quality)
    lines = [
        f"[bold]{card.name}[/bold] ({card.year}) — {card.kind} id={card.content_id}",
        f"Оригинал: {card.original_name}" if card.original_name else "",
        f"Страны: {', '.join(card.countries)}" if card.countries else "",
        f"Жанры: {', '.join(card.genres)}" if card.genres else "",
        f"КиноПоиск: {card.rating_kinopoisk}  IMDb: {card.rating_imdb}"
        if card.rating_kinopoisk or card.rating_imdb else "",
        f"Хронометраж: {card.time}" if card.time else "",
        card.description,
        "",
        "[bold]Переводы:[/bold]",
    ]
    for i, t in enumerate(card.translators, 1):
        lines.append(f"  {i}. [{t.code}] {t.title}")
    text = "\n".join(l for l in lines if l is not None)
    if console:
        console.print(Panel(text, title="Карточка"))
    else:
        print(text)
    return 0


def cmd_links(client: RezkaClient, args) -> int:
    kind, cid = _resolve_target(args)
    translator = args.transl
    if not translator:
        translators = client.get_translators(cid, kind, args.quality)
        if not translators:
            out("[red]Нет переводов.[/]")
            return 1
        translator = translators[0].code
        out(f"[dim]Берём первый перевод: {translator}[/]")
    links = client.get_videos(cid, translator, kind, args.quality,
                              args.season, args.episode)
    if not links:
        out("[red]Ссылки не получены (проверьте transl/quality/season/episode).[/]")
        return 1
    for i, l in enumerate(links, 1):
        out(f"{i}. [{l.kind or '?'}] {l.url}")
    return 0


def cmd_watch(client: RezkaClient, args) -> int:
    kind, cid = _resolve_target(args)
    card = client.load_content(cid, kind, quality=args.quality)
    translator = args.transl
    if not translator:
        if card.translators:
            translator = card.translators[0].code
            out(f"[dim]Перевод: {card.translators[0].title} ({translator})[/]")
        else:
            translator = "Гид"
    links = client.get_videos(cid, translator, kind, args.quality,
                              args.season, args.episode)
    if not links:
        out("[red]Видео не найдено. Попробуйте другое качество/перевод/сезон/серию.[/]")
        return 1
    # Предпочитаем m3u8 максимально доступного качества, либо первую ссылку
    link = next((l for l in links if l.is_hls), links[0])
    mode = "download" if args.download else (args.player or "auto")
    referer = f"https://{client.host}/"
    title = f"{card.name} {f'S{args.season}E{args.episode}' if args.season else ''}".strip()
    out(f"[bold green]▶ {title}[/bold green]  ({link.kind}, {link.quality or args.quality})")
    play_or_download(link, referer=referer, mode=mode, title=title)
    return 0


def cmd_mirrors(_client, _args) -> int:
    out("Проверка зеркал...")
    host = RezkaClient.find_working_host()
    if host:
        out(f"[green]Рабочее зеркало: {host}[/green]")
    else:
        out("[red]Ни одно зеркало не отвечает без антибот-challenge.[/]")
        out("Варианты:")
        out("  1. Откройте сайт в браузере, скопируйте Cookie и запустите с REZKA_COOKIE='...'")
        out("  2. Используйте обход Anubis: pip install curl_cffi / cloudscraper и передайте session")
    for h in MIRROR_HOSTS:
        out(f"  - https://{h}  (api: https://api.{h})")
    return 0


# --------------------------------------------------------------------- #
# Парсер аргументов
# --------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rezka-cli",
        description="Парсер и плеер для rezka-ua.tv",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--host", default=os.environ.get("REZKA_HOST", "rezka-ua.tv"),
                   help="Зеркало сайта (по умолчанию rezka-ua.tv)")
    p.add_argument("--cookie", default=os.environ.get("REZKA_COOKIE"),
                   help="Строка Cookie из браузера (для обхода Anubis)")
    p.add_argument("--cookies", dest="cookies_file",
                   default=os.environ.get("REZKA_COOKIES_FILE"),
                   help="Файл cookies в формате Netscape")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("search", help="Поиск по названию")
    sp.add_argument("query")
    sp.add_argument("--page", type=int, default=1)

    sp = sub.add_parser("latest", help="Обновления/новинки")
    sp.add_argument("--page", type=int, default=1)

    sp = sub.add_parser("top", help="ТОП-100")
    sp.add_argument("--kind", choices=["movies", "serials"], default="movies")
    sp.add_argument("--page", type=int, default=1)

    sp = sub.add_parser("catalog", help="Каталог по жанру (action, comedy, drama...)")
    sp.add_argument("genre")
    sp.add_argument("--page", type=int, default=1)

    sp = sub.add_parser("info", help="Карточка фильма/сериала + переводы")
    sp.add_argument("target", help="URL страницы или ID")
    sp.add_argument("--type", choices=["film", "serial"])
    sp.add_argument("--quality", default="720")

    sp = sub.add_parser("links", help="Прямые видео-ссылки")
    sp.add_argument("target")
    sp.add_argument("--type", choices=["film", "serial"])
    sp.add_argument("--transl", help="Код перевода (см. info)")
    sp.add_argument("--quality", default="720")
    sp.add_argument("--season", type=int)
    sp.add_argument("--episode", type=int)

    sp = sub.add_parser("watch", help="Смотреть или скачать")
    sp.add_argument("target")
    sp.add_argument("--type", choices=["film", "serial"])
    sp.add_argument("--transl")
    sp.add_argument("--quality", default="720")
    sp.add_argument("--season", type=int)
    sp.add_argument("--episode", type=int)
    sp.add_argument("--player", choices=["auto", "mpv", "ffplay", "vlc", "print"],
                    default="auto")
    sp.add_argument("--download", action="store_true", help="Скачать вместо просмотра")

    sub.add_parser("mirrors", help="Проверить рабочие зеркала")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    session = make_session(cookie_string=args.cookie, cookie_file=args.cookies_file)
    client = RezkaClient(host=args.host, session=session)
    scraper = RezkaScraper(base_url=f"https://{args.host}", session=session)

    try:
        if args.cmd == "search":
            return cmd_search(scraper, args)
        if args.cmd == "latest":
            return cmd_list(scraper, args, scraper.latest_updates(args.page))
        if args.cmd == "top":
            rows = (scraper.top_movies(args.page) if args.kind == "movies"
                    else scraper.top_serials(args.page))
            return cmd_list(scraper, args, rows)
        if args.cmd == "catalog":
            return cmd_list(scraper, args, scraper.catalog(args.genre, args.page))
        if args.cmd == "info":
            return cmd_info(client, args)
        if args.cmd == "links":
            return cmd_links(client, args)
        if args.cmd == "watch":
            return cmd_watch(client, args)
        if args.cmd == "mirrors":
            return cmd_mirrors(client, args)
        return 2
    except Exception as exc:  # noqa: BLE001
        out(f"[red]Ошибка:[/] {exc}")
        out("[dim]Подсказка: если сайт закрыт антибот-защитой Anubis или DNS "
            "не резолвит api-поддомен — укажите зеркало (--host rezka.pub), "
            "либо передайте cookies из браузера (--cookie / --cookies). "
            "Список доступных зеркал: python app.py mirrors[/]")
        return 1


if __name__ == "__main__":
    players = available_players()
    if len(sys.argv) == 1:
        build_parser().print_help()
    sys.exit(main())
