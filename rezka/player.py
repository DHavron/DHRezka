"""
Воспроизведение видео-ссылок Rezka.

Rezka отдаёт ссылки двух типов:
  * .m3u8 — HLS-плейлист (требует Referer заголовка);
  * .mp4 — прямой файл.

Способы просмотра (в порядке автовыбора):
  1. mpv   — лучшая поддержка HLS из коробки (--http-header-fields).
  2. ffplay / ffmpeg — запасной вариант.
  3. VLC   — через интерфейс или cvlc.
  4. Скачивание .mp4 requests-ом с правильным Referer.
  5. Печать ссылки — чтобы открыть в любом плеере вручную.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import requests

from .api import VideoLink


def available_players() -> list[str]:
    return [p for p in ("mpv", "ffplay", "vlc", "cvlc") if shutil.which(p)]


def play_mpv(url: str, referer: str, title: str = "") -> int:
    """Проиграть через mpv с подстановкой Referer (нужен для m3u8)."""
    cmd = [
        "mpv",
        "--no-terminal",
        f"--http-header-fields=Referer: {referer}",
        f"--force-window={title or 'yes'}",
        url,
    ]
    print(">>", " ".join(cmd))
    return subprocess.call(cmd)


def play_ffplay(url: str, referer: str) -> int:
    cmd = ["ffplay", "-autoexit", "-loglevel", "warning",
           "-headers", f"Referer: {referer}\r\n", url]
    print(">>", " ".join(cmd))
    return subprocess.call(cmd)


def play_vlc(url: str, referer: str) -> int:
    exe = shutil.which("vlc") or shutil.which("cvlc") or "vlc"
    cmd = [exe, f"http-user-agent=Mozilla/5.0", "--intf", "rc"]
    # VLC принимает http-referrer
    cmd += [f"--http-referrer={referer}", url]
    print(">>", " ".join(cmd))
    return subprocess.call(cmd)


def download(link: VideoLink, referer: str, out_dir: str = "downloads",
             filename: Optional[str] = None) -> Path:
    """Скачать .mp4 поблочно. Для m3u8 лучше использовать ffmpeg/youtube-dl."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    name = filename or link.url.split("?")[0].rsplit("/", 1)[-1] or "video.mp4"
    dest = out / name
    headers = {"Referer": referer, "User-Agent": "Mozilla/5.0"}
    with requests.get(link.url, headers=headers, stream=True, timeout=30) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 16):
                fh.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    sys.stdout.write(f"\r{pct}% ({done}/{total})")
                    sys.stdout.flush()
    print(f"\nСохранено: {dest}")
    return dest


def download_hls_with_ffmpeg(link: VideoLink, referer: str,
                             out_file: str = "video.mp4") -> int:
    """m3u8 → mp4 через ffmpeg (если установлен)."""
    if not shutil.which("ffmpeg"):
        raise FileNotFoundError("ffmpeg не найден в PATH")
    cmd = [
        "ffmpeg", "-y",
        "-headers", f"Referer: {referer}\r\n",
        "-i", link.url,
        "-c", "copy", "-bsf:a", "aac_adtstoasc",
        out_file,
    ]
    print(">>", " ".join(cmd))
    return subprocess.call(cmd)


def play_or_download(link: VideoLink, referer: str, mode: str = "auto",
                     title: str = "") -> None:
    """Играть/скачать ссылку согласно выбранному режиму.

    mode: auto | mpv | ffplay | vlc | download | print
    """
    if mode == "auto":
        players = available_players()
        mode = players[0] if players else "print"
        if mode == "ffplay" and link.is_hls:
            pass  # ffplay умеет hls
        print(f"Автовыбор плеера: {mode}")

    if mode in ("mpv",):
        code = play_mpv(link.url, referer, title)
    elif mode in ("ffplay", "ffmpeg"):
        code = play_ffplay(link.url, referer)
    elif mode in ("vlc", "cvlc"):
        code = play_vlc(link.url, referer)
    elif mode == "download":
        if link.is_hls and shutil.which("ffmpeg"):
            code = download_hls_with_ffmpeg(link, referer)
        else:
            download(link, referer)
            code = 0
    else:  # print
        code = 0

    if mode == "print" or code != 0:
        print("\nСсылка для ручного воспроизведения (нужен Referer):")
        print(f"  URL:     {link.url}")
        print(f"  Referer: {referer}")
