#!/usr/bin/env python3
"""
Rezka GUI — приложение для просмотра фильмов rezka-ua.tv на Python + Tkinter.

Запуск:  python gui.py

Возможности:
  * Поиск, новинки, ТОП-100 фильмов и сериалов (парсинг сайта);
  * Карточка фильма: описание, жанры, рейтинги, список озвучек;
  * Прямые ссылки m3u8/mp4 через внутреннее API Rezka;
  * Воспроизведение (mpv / ffplay / vlc) или скачивание;
  * Окно «Настройки» (⚙), где можно указать заголовок Cookie, User-Agent,
    хост/зеркало, качество, плеер и папку загрузок;
  * Настройки сохраняются в ~/.rezka_gui/settings.json.

Cookie нужны, если сайт закрыт антибот-защитой (Anubis): откройте
rezka-ua.tv в браузере, пройдите проверку, затем DevTools (F12) →
Network → любой запрос к сайту → скопируйте значение заголовка `Cookie`
(или просто содержимое document.cookie) и вставьте в поле настроек.
"""

from __future__ import annotations

import json
import queue
import re
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse  # noqa: F401 (резерв)

# --- сторонние модули проекта ------------------------------------------------
try:
    from rezka.api import RezkaClient, MIRROR_HOSTS, parse_content_url
    from rezka.scraper import RezkaScraper, make_session
    from rezka.player import available_players, play_or_download
except ImportError:  # запуск из другого каталога
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from rezka.api import RezkaClient, MIRROR_HOSTS, parse_content_url
    from rezka.scraper import RezkaScraper, make_session
    from rezka.player import available_players, play_or_download

SETTINGS_DIR = Path.home() / ".rezka_gui"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "host": "rezka-ua.tv",          # основной домен или зеркало
    "cookie": "",                    # значение заголовка Cookie из браузера
    "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0 Safari/537.36"),
    "quality": "720",
    "player": "auto",                # auto | mpv | ffplay | vlc | download | print
    "download_dir": str(Path.home() / "Downloads"),
    "impersonate": True,             # TLS-имперсонация Chrome (curl_cffi)
}

QUALITIES = ["1080", "720", "480", "360"]


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    try:
        s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return s


def save_settings(s: dict) -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(
        json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


class SettingsDialog(tk.Toplevel):
    """Модальное окно настроек: Cookie, User-Agent, хост, качество, плеер."""

    def __init__(self, master: tk.Misc, settings: dict):
        super().__init__(master)
        self.title("Настройки")
        self.settings = dict(settings)
        self.result: dict | None = None
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        row = 0
        # --- Хост / зеркало ---
        ttk.Label(frm, text="Хост (сайт или зеркало):").grid(row=row, column=0, sticky="w", **pad)
        self.host_var = tk.StringVar(value=self.settings["host"])
        host_cb = ttk.Combobox(frm, textvariable=self.host_var,
                               values=MIRROR_HOSTS, width=32)
        host_cb.grid(row=row, column=1, sticky="ew", **pad)
        row += 1

        ttk.Button(frm, text="🔎 Найти рабочее зеркало",
                   command=self._find_mirror).grid(row=row, column=1, sticky="w", **pad)
        self.mirror_status = ttk.Label(frm, text="", foreground="gray")
        self.mirror_status.grid(row=row, column=0, sticky="w", **pad)
        row += 1

        ttk.Separator(frm).grid(row=row, column=0, columnspan=2, sticky="ew", pady=6)
        row += 1

        # --- Cookie ---
        ttk.Label(frm, text="Заголовок Cookie:").grid(row=row, column=0, sticky="nw", **pad)
        cookie_frm = ttk.Frame(frm)
        cookie_frm.grid(row=row, column=1, sticky="ew", **pad)
        row += 1

        self.cookie_txt = tk.Text(cookie_frm, width=48, height=5, wrap="word")
        self.cookie_txt.pack(fill="x")
        self.cookie_txt.insert("1.0", self.settings.get("cookie", ""))

        hint = ("Скопируйте из браузера: F12 → Network → запрос к сайту →\n"
                "Request Headers → Cookie. Нужен, если сайт закрыт Anubis.")
        ttk.Label(frm, text=hint, foreground="gray",
                  justify="left").grid(row=row, column=1, sticky="w", **pad)
        row += 1

        btns = ttk.Frame(frm)
        btns.grid(row=row, column=1, sticky="w", **pad)
        ttk.Button(btns, text="Из файла…", command=self._load_cookie_file)\
            .pack(side="left", padx=(0, 4))
        ttk.Button(btns, text="Очистить", command=lambda: self.cookie_txt.delete("1.0", "end"))\
            .pack(side="left")
        row += 1

        ttk.Separator(frm).grid(row=row, column=0, columnspan=2, sticky="ew", pady=6)
        row += 1

        # --- User-Agent ---
        ttk.Label(frm, text="User-Agent:").grid(row=row, column=0, sticky="w", **pad)
        self.ua_var = tk.StringVar(value=self.settings["user_agent"])
        ttk.Entry(frm, textvariable=self.ua_var, width=48)\
            .grid(row=row, column=1, sticky="ew", **pad)
        row += 1

        # --- Качество ---
        ttk.Label(frm, text="Качество по умолчанию:").grid(row=row, column=0, sticky="w", **pad)
        self.q_var = tk.StringVar(value=self.settings["quality"])
        ttk.Combobox(frm, textvariable=self.q_var, values=QUALITIES,
                     state="readonly", width=8).grid(row=row, column=1, sticky="w", **pad)
        row += 1

        # --- Плеер ---
        ttk.Label(frm, text="Плеер:").grid(row=row, column=0, sticky="w", **pad)
        found = available_players() or ["print"]
        players = ["auto"] + found + ["download", "print"]
        self.p_var = tk.StringVar(value=self.settings["player"])
        if self.p_var.get() not in players:
            players.append(self.p_var.get())
        ttk.Combobox(frm, textvariable=self.p_var, values=players,
                     state="readonly", width=12).grid(row=row, column=1, sticky="w", **pad)
        row += 1

        # --- Папка загрузок ---
        ttk.Label(frm, text="Папка загрузок:").grid(row=row, column=0, sticky="w", **pad)
        dl_frm = ttk.Frame(frm)
        dl_frm.grid(row=row, column=1, sticky="ew", **pad)
        self.dl_var = tk.StringVar(value=self.settings["download_dir"])
        ttk.Entry(dl_frm, textvariable=self.dl_var, width=36).pack(side="left")
        ttk.Button(dl_frm, text="…", width=3,
                   command=self._pick_dir).pack(side="left", padx=2)
        row += 1

        # --- TLS-имперсонация ---
        self.imp_var = tk.BooleanVar(value=bool(self.settings.get("impersonate", True)))
        ttk.Checkbutton(frm, text="TLS-имперсонация Chrome (curl_cffi)",
                        variable=self.imp_var).grid(row=row, column=1, sticky="w", **pad)
        row += 1

        # --- Кнопки OK/Cancel ---
        ok_frm = ttk.Frame(frm)
        ok_frm.grid(row=row, column=0, columnspan=2, pady=(10, 0))
        ttk.Button(ok_frm, text="Сохранить", command=self._ok).pack(side="left", padx=6)
        ttk.Button(ok_frm, text="Отмена", command=self.destroy).pack(side="left", padx=6)

        frm.columnconfigure(1, weight=1)

    # ------------------------------------------------------------------ #
    def _load_cookie_file(self):
        path = filedialog.askopenfilename(
            title="Файл с cookies (обычный текст или Netscape cookies.txt)",
            filetypes=[("Все файлы", "*.*")])
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            messagebox.showerror("Ошибка", f"Не удалось прочитать файл:\n{exc}")
            return
        if text.startswith("# HTTP Cookie File"):
            # Netscape-формат: соберём "name=value; ..."
            pairs = []
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    pairs.append(f"{parts[5]}={parts[6]}")
            text = "; ".join(pairs)
        self.cookie_txt.delete("1.0", "end")
        self.cookie_txt.insert("1.0", text)

    def _pick_dir(self):
        d = filedialog.askdirectory(title="Папка для загрузок")
        if d:
            self.dl_var.set(d)

    def _find_mirror(self):
        """Фоновый перебор зеркал с текущими cookie/UA."""
        self.mirror_status.config(text="Проверяю…", foreground="gray")
        cookie = self.cookie_txt.get("1.0", "end").strip()
        ua = self.ua_var.get().strip()
        imp = self.imp_var.get()

        def worker():
            sess = make_session(cookie_string=cookie or None, impersonate=imp)
            if ua:
                sess.headers["User-Agent"] = ua
            host = RezkaClient.find_working_host(session=sess, timeout=8)
            self.after(0, lambda: self._mirror_done(host))

        threading.Thread(target=worker, daemon=True).start()

    def _mirror_done(self, host):
        if host:
            self.host_var.set(host)
            self.mirror_status.config(text=f"Рабочее зеркало: {host}",
                                      foreground="green")
        else:
            self.mirror_status.config(text="Ни одно зеркало не отвечает",
                                      foreground="red")

    def _ok(self):
        self.result = {
            "host": self.host_var.get().strip() or DEFAULT_SETTINGS["host"],
            "cookie": self.cookie_txt.get("1.0", "end").strip(),
            "user_agent": self.ua_var.get().strip(),
            "quality": self.q_var.get(),
            "player": self.p_var.get(),
            "download_dir": self.dl_var.get().strip() or DEFAULT_SETTINGS["download_dir"],
            "impersonate": self.imp_var.get(),
        }
        self.destroy()


class RezkaGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Rezka Player")
        self.geometry("1000x640")
        self.minsize(800, 520)

        self.settings = load_settings()
        self._q: queue.Queue = queue.Queue()
        self._busy = False
        self._items: list = []          # текущий список SearchResult
        self._content = None            # открытая карточка (Content)

        self._build_style()
        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self._build_statusbar()

        self.after(100, self._poll_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.refresh_latest()

    # ============================ UI-каркас ============================= #
    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=24)
        style.configure("Big.TButton", font=("", 10))

    def _build_menu(self):
        menubar = tk.Menu(self)
        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label="Настройки…  (Cookie, плеер…)",
                           accelerator="Ctrl+P", command=self.open_settings)
        m_file.add_separator()
        m_file.add_command(label="Выход", accelerator="Ctrl+Q",
                           command=self._on_close)
        menubar.add_cascade(label="Файл", menu=m_file)

        m_nav = tk.Menu(menubar, tearoff=0)
        m_nav.add_command(label="Новинки", command=self.refresh_latest)
        m_nav.add_command(label="ТОП-100 фильмов", command=self.refresh_top_films)
        m_nav.add_command(label="ТОП-100 сериалов", command=self.refresh_top_serials)
        menubar.add_cascade(label="Разделы", menu=m_nav)

        m_help = tk.Menu(menubar, tearoff=0)
        m_help.add_command(label="Справка / как получить Cookie",
                           command=self.show_help)
        m_help.add_command(label="О программе", command=self.show_about)
        menubar.add_cascade(label="Справка", menu=m_help)
        self.config(menu=menubar)
        self.bind_all("<Control-p>", lambda e: self.open_settings())
        self.bind_all("<Control-q>", lambda e: self._on_close())

    def _build_toolbar(self):
        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(fill="x")

        ttk.Label(bar, text="Поиск:").pack(side="left")
        self.search_var = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=self.search_var, width=38)
        ent.pack(side="left", padx=(4, 6))
        ent.bind("<Return>", lambda e: self.do_search())
        ttk.Button(bar, text="🔍", width=4,
                   command=self.do_search).pack(side="left", padx=(0, 10))

        for text, cmd in (("Новинки", self.refresh_latest),
                          ("ТОП фильмы", self.refresh_top_films),
                          ("ТОП сериалы", self.refresh_top_serials)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=2)

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text="⚙ Настройки",
                   command=self.open_settings).pack(side="right")

    def _build_body(self):
        paned = ttk.Panedwindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        # левая часть — список результатов
        left = ttk.Frame(paned)
        cols = ("title", "year", "kind")
        self.tree = ttk.Treeview(left, columns=cols, show="tree headings",
                                 selectmode="browse")
        self.tree.heading("#0", text="Просмотренное")
        self.tree.heading("title", text="Название")
        self.tree.heading("year", text="Год")
        self.tree.heading("kind", text="Тип")
        self.tree.column("#0", width=0, stretch=False)
        self.tree.column("title", width=380, anchor="w")
        self.tree.column("year", width=60, anchor="center")
        self.tree.column("kind", width=90, anchor="center")

        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self.on_open_item())
        self.tree.bind("<Return>", lambda e: self.on_open_item())

        # правая часть — карточка + действия
        right = ttk.Frame(paned, width=360)
        nb = ttk.Notebook(right)
        nb.pack(fill="both", expand=True)

        tab_info = ttk.Frame(nb)
        self.info_txt = tk.Text(tab_info, wrap="word", state="disabled",
                                font=("", 10))
        info_sb = ttk.Scrollbar(tab_info, command=self.info_txt.yview)
        self.info_txt.configure(yscrollcommand=info_sb.set)
        self.info_txt.pack(side="left", fill="both", expand=True)
        info_sb.pack(side="right", fill="y")
        nb.add(tab_info, text="Информация")

        tab_links = ttk.Frame(nb)
        self.links_txt = tk.Text(tab_links, wrap="char", state="disabled",
                                 font=("TkFixedFont", 9))
        links_sb = ttk.Scrollbar(tab_links, command=self.links_txt.yview)
        self.links_txt.configure(yscrollcommand=links_sb.set)
        self.links_txt.pack(side="left", fill="both", expand=True)
        links_sb.pack(side="right", fill="y")
        nb.add(tab_links, text="Ссылки / озвучки")

        # панель действий
        act = ttk.Frame(right, padding=(0, 8))
        act.pack(fill="x")
        self.btn_play = ttk.Button(act, text="▶ Смотреть", style="Big.TButton",
                                   command=self.watch_selected)
        self.btn_play.pack(side="left", padx=(0, 6))
        self.btn_download = ttk.Button(act, text="⬇ Скачать",
                                       command=self.download_selected)
        self.btn_download.pack(side="left", padx=(0, 6))
        self.btn_copy = ttk.Button(act, text="📋 Скопировать ссылку",
                                   command=self.copy_link)
        self.btn_copy.pack(side="left")

        # выбор озвучки/качества/серии
        opts = ttk.Frame(right)
        opts.pack(fill="x", pady=(6, 0))
        ttk.Label(opts, text="Озвучка:").grid(row=0, column=0, sticky="w")
        self.transl_var = tk.StringVar()
        self.transl_cb = ttk.Combobox(opts, textvariable=self.transl_var,
                                      state="readonly", width=28)
        self.transl_cb.grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Label(opts, text="Качество:").grid(row=0, column=2, sticky="w")
        self.quality_var = tk.StringVar(value=self.settings["quality"])
        q_cb = ttk.Combobox(opts, textvariable=self.quality_var,
                            values=QUALITIES, state="readonly", width=7)
        q_cb.grid(row=0, column=3, padx=4)
        opts.columnconfigure(1, weight=1)

        ser_frm = ttk.Frame(opts)
        ser_frm.grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.serial_controls: list[tk.Widget] = []
        ttk.Label(ser_frm, text="Сезон:").pack(side="left")
        self.season_var = tk.StringVar()
        self.season_cb = ttk.Combobox(ser_frm, textvariable=self.season_var,
                                      state="readonly", width=6)
        self.season_cb.pack(side="left", padx=(2, 10))
        ttk.Label(ser_frm, text="Серия:").pack(side="left")
        self.episode_var = tk.StringVar()
        self.episode_cb = ttk.Combobox(ser_frm, textvariable=self.episode_var,
                                       state="readonly", width=6)
        self.episode_cb.pack(side="left", padx=2)
        self.season_cb.bind("<<ComboboxSelected>>", lambda e: self._update_episodes())

        paned.add(left, weight=3)
        paned.add(right, weight=2)

    def _build_statusbar(self):
        sb = ttk.Frame(self, padding=(8, 2))
        sb.pack(fill="x")
        self.status_var = tk.StringVar(value="Готово")
        ttk.Label(sb, textvariable=self.status_var, foreground="gray").pack(side="left")
        self.host_var_lbl = ttk.Label(
            sb, textvariable=tk.StringVar(value=f"Хост: {self.settings['host']}"),
            foreground="gray")
        self.host_var_lbl.pack(side="right")
        self._host_text = tk.StringVar(value=f"Хост: {self.settings['host']}")
        self.host_var_lbl.configure(textvariable=self._host_text)

    # ======================== работа с сетью (фоновая) ================== #
    def _make_session(self):
        s = self.settings
        sess = make_session(cookie_string=s.get("cookie") or None,
                            impersonate=s.get("impersonate", True))
        if s.get("user_agent"):
            sess.headers["User-Agent"] = s["user_agent"]
        return sess

    def _run_async(self, fn, status: str, on_done):
        """fn() выполняется в рабочем потоке; результат → on_done через очередь."""
        if self._busy:
            messagebox.showinfo("Занято", "Дождитесь завершения текущего запроса.")
            return
        self._busy = True
        self._set_busy(True)
        self.status_var.set(status)

        def worker():
            try:
                res = fn()
                self._q.put(("ok", res, on_done))
            except Exception as exc:  # noqa: BLE001
                self._q.put(("err", exc, on_done))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_queue(self):
        try:
            while True:
                kind, payload, cb = self._q.get_nowait()
                self._busy = False
                self._set_busy(False)
                if kind == "ok":
                    cb(payload)
                    self.status_var.set("Готово")
                else:
                    cb(None)
                    msg = str(payload)
                    self.status_var.set(f"Ошибка: {msg[:100]}")
                    if "Anubis" in msg or "не JSON" in msg:
                        msg += ("\n\nСкорее всего сайт закрыт антибот-защитой."
                               " Откройте «⚙ Настройки» и укажите заголовок Cookie"
                               " из браузера (или найдите рабочее зеркало).")
                    messagebox.showerror("Ошибка", msg)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _set_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        for b in (self.btn_play, self.btn_download, self.btn_copy):
            b.state(["!disabled"] if not busy else ["disabled"])

    # ========================= действия списка ========================== #
    def _fill_tree(self, items, heading_note=""):
        self.tree.delete(*self.tree.get_children())
        self._items = items
        for it in items:
            self.tree.insert("", "end", values=(it.title, it.year, it.kind),
                             text=heading_note or "▸")
        self.status_var.set(f"Найдено: {len(items)} {heading_note}".strip())

    def do_search(self):
        query = self.search_var.get().strip()
        if not query:
            return
        scraper = RezkaScraper(base_url=f"https://{self.settings['host']}",
                               session=self._make_session())
        self._run_async(lambda: scraper.search(query),
                        f"Ищу «{query}»…", self._fill_tree)

    def refresh_latest(self):
        scraper = RezkaScraper(base_url=f"https://{self.settings['host']}",
                               session=self._make_session())
        self._run_async(scraper.latest_updates,
                        "Загружаю новинки…",
                        lambda items: self._fill_tree(items, "новинки"))

    def refresh_top_films(self):
        scraper = RezkaScraper(base_url=f"https://{self.settings['host']}",
                               session=self._make_session())
        self._run_async(scraper.top_movies,
                        "Загружаю ТОП-100 фильмов…",
                        lambda items: self._fill_tree(items, "ТОП фильмы"))

    def refresh_top_serials(self):
        scraper = RezkaScraper(base_url=f"https://{self.settings['host']}",
                               session=self._make_session())
        self._run_async(scraper.top_serials,
                        "Загружаю ТОП-100 сериалов…",
                        lambda items: self._fill_tree(items, "ТОП сериалы"))

    # ========================== карточка фильма ========================= #
    def on_open_item(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        if idx >= len(self._items):
            return
        item = self._items[idx]
        client = RezkaClient(host=self.settings["host"],
                             session=self._make_session())

        def task():
            return client.load_content(item.content_id, item.kind,
                                       quality=self.quality_var.get())

        self._run_async(task, f"Загружаю «{item.title}»…", self._show_content)

    def _show_content(self, content):
        if content is None:
            return
        self._content = content
        lines = [
            f"{content.name}" + (f" ({content.original_name})" if content.original_name else ""),
            "",
            f"Год: {content.year}   Тип: {'сериал' if content.kind == 'serial' else 'фильм'}"
            + (f"   Длительность: {content.time}" if content.time else ""),
        ]
        if content.rating_kinopoisk:
            lines.append(f"Кинопоиск: {content.rating_kinopoisk}", )
        if content.rating_imdb:
            lines[-1] += f"   IMDb: {content.rating_imdb}"
        if content.genres:
            lines.append("Жанры: " + ", ".join(g for g in content.genres if g))
        if content.countries:
            lines.append("Страны: " + ", ".join(c for c in content.countries if c))
        lines += ["", content.description or "(описание отсутствует)"]
        self._set_text(self.info_txt, "\n".join(lines))

        titles = [t.title for t in content.translators] or ["(нет озвучек)"]
        self.transl_cb["values"] = titles
        self.transl_var.set(titles[0])

        if content.kind == "serial" and content.seasons:
            vals = [str(s) for s in content.seasons]
            self.season_cb["values"] = vals
            self.season_var.set(vals[0])
            self._update_episodes()
        else:
            self.season_cb["values"] = []
            self.season_var.set("")
            self.episode_cb["values"] = []
            self.episode_var.set("")

        self._set_text(self.links_txt,
                       "Двойной клик по строке ниже или кнопка «▶ Смотреть».\n"
                       "Список ссылок будет загружен при запуске воспроизведения.")
        self.title(f"Rezka Player — {content.name}")

    @staticmethod
    def _set_text(widget: tk.Text, text: str):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _selected_translator(self):
        c = self._content
        if not c or not c.translators:
            return None
        title = self.transl_var.get()
        for t in c.translators:
            if t.title == title:
                return t
        return c.translators[0]

    def _update_episodes(self):
        """Для сериала загрузить номера серий выбранного сезона (через API)."""
        self.episode_cb["values"] = []
        self.episode_var.set("")
        c = self._content
        if not c or c.kind != "serial" or not self.season_var.get():
            return
        transl = self._selected_translator()
        if not transl:
            return
        season = int(self.season_var.get())
        client = RezkaClient(host=self.settings["host"],
                             session=self._make_session())

        def task():
            data = client._fetch("serial", {
                "id": c.content_id, "type": "serial", "linkType": "all",
                "quality": self.quality_var.get(),
                "transl": transl.code, "translator": transl.code,
                "season": season,
            })
            eps = set()
            for entry in (data.get("videoUrl") or data.get("links") or []):
                if isinstance(entry, dict):
                    e = entry.get("episode") or entry.get("series")
                    if e is not None:
                        eps.add(int(e))
            return sorted(eps)

        def done(vals):
            if vals:
                self.episode_cb["values"] = [str(v) for v in vals]
                self.episode_var.set(str(vals[0]))

        self._run_async(task, f"Серия {season}: загружаю список серий…", done)

    # ====================== воспроизведение / загрузка ================== #
    def _fetch_best_link(self):
        """В рабочем потоке: получить ссылки выбранной озвучки и выбрать лучшее."""
        c = self._content
        transl = self._selected_translator()
        if not c or not transl:
            raise RuntimeError("Сначала откройте фильм (двойной клик по списку).")
        client = RezkaClient(host=self.settings["host"],
                             session=self._make_session())
        quality = self.quality_var.get()
        kwargs = {}
        if c.kind == "serial":
            if self.season_var.get():
                kwargs["season"] = int(self.season_var.get())
            if self.episode_var.get():
                kwargs["episode"] = int(self.episode_var.get())
        links = client.get_videos(c.content_id, transl.code, c.kind,
                                  quality=quality, **kwargs)
        if not links:
            raise RuntimeError("API не вернул ни одной ссылки для этой озвучки.")
        # предпочесть запрошенное качество, иначе любое
        best = next((l for l in links if l.quality == quality), links[0])
        report = "\n".join(f"[{l.quality or '?'}] {l.kind or ''}: {l.url}"
                           for l in links)
        return best, report, client.host

    def watch_selected(self, mode: str | None = None):
        if not self._content:
            messagebox.showinfo("Фильм не выбран",
                                "Двойным кликом выберите фильм в списке слева.")
            return
        player = mode or self.settings["player"]

        def task():
            return self._fetch_best_link()

        def done(res):
            if not res:
                return
            link, report, host = res
            self._set_text(self.links_txt, report)
            referer = f"https://{host}/"
            self.status_var.set(f"Запускаю {player}: {link.quality}…")
            try:
                play_or_download(link, referer=referer, mode=player,
                                 title=self._content.name)
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("Плеер", f"Не удалось запустить плеер:\n{exc}\n\n"
                                     "Ссылка скопирована во вкладку «Ссылки».")

        self._run_async(task, "Получаю ссылки на видео…", done)

    def download_selected(self):
        """Скачать выбранное видео в папку из настроек."""
        if not self._content:
            messagebox.showinfo("Фильм не выбран",
                                "Двойным кликом выберите фильм в списке слева.")
            return

        def task():
            return self._fetch_best_link()

        def done(res):
            if not res:
                return
            link, report, host = res
            self._set_text(self.links_txt, report)
            referer = f"https://{host}/"
            out_dir = self.settings.get("download_dir") or "."
            self.status_var.set(f"Скачиваю {link.quality} в {out_dir}…")
            try:
                if link.is_hls:
                    from rezka.player import download_hls_with_ffmpeg
                    name = re.sub(r"[^\w\-. ]+", "_", self._content.name) + ".mp4"
                    download_hls_with_ffmpeg(link, referer,
                        out_file=str(Path(out_dir) / name))
                else:
                    from rezka.player import download as dl
                    dl(link, referer, out_dir=out_dir)
                self.status_var.set("Загрузка завершена")
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("Загрузка", f"Не удалось скачать:\n{exc}")

        self._run_async(task, "Получаю ссылки на видео…", done)

    def copy_link(self):
        txt = self.links_txt.get("1.0", "end").strip()
        first = ""
        for line in txt.splitlines():
            if line.startswith("["):
                first = line.split(": ", 1)[-1]
                break
        if not first:
            messagebox.showinfo("Нет ссылки", "Сначала получите ссылки (▶ Смотреть).")
            return
        self.clipboard_clear()
        self.clipboard_append(first)
        self.status_var.set("Ссылка скопирована в буфер обмена")

    # ============================== настройки ============================= #
    def open_settings(self):
        dlg = SettingsDialog(self, self.settings)
        self.wait_window(dlg)
        if dlg.result:
            self.settings = dlg.result
            save_settings(self.settings)
            self.quality_var.set(self.settings["quality"])
            self._host_text.set(f"Хост: {self.settings['host']}")
            self.status_var.set("Настройки сохранены")

    def show_help(self):
        HelpWindow(self)

    def show_about(self):
        messagebox.showinfo(
            "О программе",
            "Rezka Player (GUI)\n\n"
            "Парсер и плеер для rezka-ua.tv на Python + Tkinter.\n"
            "Данные: https://rezka-ua.tv (внутреннее AJAX-API).\n\n"
            "Используйте только контент, на который у вас есть права.")

    def _on_close(self):
        save_settings(self.settings)
        self.destroy()


class HelpWindow(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Справка")
        self.geometry("640x480")
        txt = tk.Text(self, wrap="word", padx=12, pady=10)
        sb = ttk.Scrollbar(self, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        txt.insert("1.0", HELP_TEXT)
        txt.configure(state="disabled")


HELP_TEXT = """\
Как пользоваться программой
===========================

1. Выберите раздел (Новинки / ТОП) или введите запрос в строку поиска.
2. Двойной клик по строке — загрузится карточка фильма и список озвучек.
3. Нажмите «▶ Смотреть» — ссылка m3u8/mp4 откроется в mpv/ffplay/vlc
   (автовыбор). «⬇ Скачать» — сохранит видео в папку загрузок.
4. Если плеера нет — ссылка попадёт во вкладку «Ссылки / озвучки»,
   её можно скопировать кнопкой «📋» и открыть в любом плеере
   (например, VLC: Медиа → Открыть URL → «Показать дополнительные
   параметры» → заголовок Referer: https://rezka-ua.tv/).

Как получить Cookie (если сайт закрыт защитой Anubis)
======================================================
1. Откройте https://rezka-ua.tv в обычном браузере и дождитесь,
   пока пройдёт автоматическая проверка («Checking your browser»).
2. Нажмите F12 → вкладка Network (Сеть) → обновите страницу (F5).
3. Кликните на первый запрос к домену rezka-ua.tv.
4. В Request Headers найдите строку Cookie и скопируйте её значение
   целиком (вида «anubis-xxx=...; PHPSESSID=...; ...»).
5. В программе: ⚙ Настройки → вставьте в поле «Заголовок Cookie» →
   Сохранить. Настройки лежат в ~/.rezka_gui/settings.json.

Альтернатива: кнопка «🔎 Найти рабочее зеркало» в окне настроек
переберёт известные зеркала (rezka.pub, rezka.art, ...) и подставит
первое доступное.

Примечание
----------
Программа предназначена для изучения веб-парсинга. Используйте только
контент, распространение и просмотр которого законны в вашем регионе.
"""


def main():
    app = RezkaGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
