"""YouTube downloader — adapted from Download-Simply-Videos-From-YouTube/download.py.

Web-specific changes:
- on_track_start / on_track_done callbacks for per-track UI updates
- global_semaphore: a threading.Semaphore acquired per download to enforce
  the server-wide concurrent download cap
- Quiet yt-dlp output (logs go to server stdout, not shown to the user)
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from typing import Callable, List, Optional
from urllib.parse import parse_qs, urlparse

from yt_dlp import YoutubeDL

MAX_RETRIES = 3
RETRY_DELAY = 2
DEFAULT_CONCURRENT_WORKERS = 3


@lru_cache(maxsize=128)
def get_url_info(url: str):
    try:
        ydl_opts = {
            "quiet": True,
            "extract_flat": True,
            "no_warnings": True,
            "skip_download": True,
            "playlist_items": "1",
        }
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                parsed = urlparse(url)
                qp = parse_qs(parsed.query)
                if any(x in url for x in ("/@", "/channel/", "/c/", "/user/")):
                    return "channel", {}
                elif "list" in qp:
                    return "playlist", {}
                return "video", {}
            ct = info.get("_type", "video")
            if ct == "playlist" and info.get("uploader_id") and any(
                x in url for x in ("/@", "/channel/", "/c/", "/user/")
            ):
                return "channel", info
            return ct, info
    except Exception:
        parsed = urlparse(url)
        qp = parse_qs(parsed.query)
        if any(x in url for x in ("/@", "/channel/", "/c/", "/user/")):
            return "channel", {}
        elif "list" in qp:
            return "playlist", {}
        return "video", {}


def download_single_video(
    url: str,
    output_path: str,
    thread_id: int = 0,
    audio_only: bool = False,
    track_name: str | None = None,
    on_start: Callable[[str], None] | None = None,
    on_done: Callable[[str, bool], None] | None = None,
    global_semaphore=None,
) -> dict:
    if on_start and track_name:
        on_start(track_name)

    postprocessors = (
        [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
        if audio_only
        else [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}]
    )

    opts = {
        "format": "bestaudio/best" if audio_only else "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "ignoreerrors": True,
        "no_warnings": True,
        "quiet": True,
        "noplaylist": False,
        "extract_flat": False,
        "postprocessors": postprocessors,
        "keepvideo": False,
        "clean_infojson": True,
        "retries": MAX_RETRIES,
        "fragment_retries": MAX_RETRIES,
        "compat_opts": ["no-youtube-unavailable-videos"],
        "nocheckcertificate": True,
        "outtmpl": os.path.join(output_path, "%(title)s.%(ext)s"),
    }
    if not audio_only:
        opts["merge_output_format"] = "mp4"

    last_exc = None
    success = False
    title = ""

    def _do_download():
        nonlocal last_exc, success, title
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                if info is None:
                    return {"url": url, "success": False, "count": 0, "title": "", "message": "No info returned."}
                if info.get("_type") == "playlist":
                    t = info.get("title", "Unknown")
                    n = len(info.get("entries", []))
                    success = n > 0
                    title = t
                    return {"url": url, "success": success, "count": n, "title": t, "message": f"Downloaded {n} items"}
                else:
                    title = info.get("title", "Unknown")
                    success = True
                    return {"url": url, "success": True, "count": 1, "title": title, "message": f"Done: {title}"}
            except Exception as e:
                last_exc = e
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY * (2 ** (attempt - 1)))
        return {"url": url, "success": False, "count": 0, "title": "", "message": str(last_exc)}

    if global_semaphore is not None:
        with global_semaphore:
            result = _do_download()
    else:
        result = _do_download()

    if on_done and track_name:
        on_done(track_name, result.get("success", False))

    return result


def download_youtube_content(
    urls: List[str],
    output_path: Optional[str] = None,
    max_workers: int = DEFAULT_CONCURRENT_WORKERS,
    audio_only: bool = False,
    on_track_start: Callable[[str], None] | None = None,
    on_track_done: Callable[[str, bool], None] | None = None,
    url_to_name: dict | None = None,
    global_semaphore=None,
) -> List[dict]:
    if output_path is None:
        output_path = os.path.join(os.getcwd(), "downloads")
    os.makedirs(output_path, exist_ok=True)

    name_map = url_to_name or {}

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                download_single_video,
                url,
                output_path,
                i + 1,
                audio_only,
                name_map.get(url),
                on_track_start,
                on_track_done,
                global_semaphore,
            ): url
            for i, url in enumerate(urls)
        }
        for future in as_completed(futures):
            results.append(future.result())

    return results
