"""YouTube downloader — adapted from Download-Simply-Videos-From-YouTube/download.py.

Changes from original:
- Removed interactive __main__ block
- download_youtube_content accepts an optional progress_fn for web progress reporting
- Removed sys import (not needed without __main__)
"""

from yt_dlp import YoutubeDL
import os
import re
import time
from typing import Optional, List, Callable
from urllib.parse import urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

MAX_RETRIES = 3
RETRY_DELAY = 2
MAX_CONCURRENT_WORKERS = 5
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
            video_info = ydl.extract_info(url, download=False)
            if video_info is None:
                parsed_url = urlparse(url)
                query_params = parse_qs(parsed_url.query)
                if "/@" in url or "/channel/" in url or "/c/" in url or "/user/" in url:
                    return "channel", {}
                elif "list" in query_params:
                    return "playlist", {}
                else:
                    return "video", {}
            content_type = video_info.get("_type", "video")
            if content_type == "playlist":
                if video_info.get("uploader_id") and (
                    "/@" in url or "/channel/" in url or "/c/" in url or "/user/" in url
                ):
                    return "channel", video_info
                else:
                    return "playlist", video_info
            return content_type, video_info
    except Exception:
        parsed_url = urlparse(url)
        query_params = parse_qs(parsed_url.query)
        if "/@" in url or "/channel/" in url or "/c/" in url or "/user/" in url:
            return "channel", {}
        elif "list" in query_params:
            return "playlist", {}
        else:
            return "video", {}


def download_single_video(
    url: str,
    output_path: str,
    thread_id: int = 0,
    audio_only: bool = False,
    progress_fn: Callable[[str], None] = print,
) -> dict:
    if audio_only:
        format_selector = "bestaudio/best"
        postprocessors = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
    else:
        format_selector = "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
        postprocessors = [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}]

    downloader_options = {
        "format": format_selector,
        "ignoreerrors": True,
        "no_warnings": False,
        "noplaylist": False,
        "extract_flat": False,
        "postprocessors": postprocessors,
        "keepvideo": False,
        "clean_infojson": True,
        "retries": MAX_RETRIES,
        "fragment_retries": MAX_RETRIES,
        "compat_opts": ["no-youtube-unavailable-videos"],
        "youtube_include_dash_manifest": False,
        "nocheckcertificate": True,
        "quiet": True,
        "no_warnings": True,
    }

    if not audio_only:
        downloader_options["merge_output_format"] = "mp4"

    content_type, _ = get_url_info(url)
    downloader_options["outtmpl"] = os.path.join(output_path, "%(title)s.%(ext)s")

    last_exception = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with YoutubeDL(downloader_options) as ydl:
                download_result = ydl.extract_info(url, download=True)
                if download_result is None:
                    return {"url": url, "success": False, "count": 0, "title": "", "message": "Failed to extract video information."}

                if download_result.get("_type") == "playlist":
                    title = download_result.get("title", "Unknown Playlist")
                    video_count = len(download_result.get("entries", []))
                    return {"url": url, "success": video_count > 0, "count": video_count, "title": title, "message": f"Downloaded {video_count} tracks"}
                else:
                    title = download_result.get("title", "Unknown")
                    progress_fn(f"Downloaded: {title}")
                    return {"url": url, "success": True, "count": 1, "title": title, "message": f"Downloaded: {title}"}
        except Exception as error:
            last_exception = error
            if attempt < MAX_RETRIES:
                retry_delay = RETRY_DELAY * (2 ** (attempt - 1))
                time.sleep(retry_delay)
            else:
                return {"url": url, "success": False, "count": 0, "title": "", "message": f"Failed after {MAX_RETRIES} attempts: {str(last_exception)}"}

    return {"url": url, "success": False, "count": 0, "title": "", "message": str(last_exception)}


def download_youtube_content(
    urls: List[str],
    output_path: Optional[str] = None,
    max_workers: int = DEFAULT_CONCURRENT_WORKERS,
    audio_only: bool = False,
    progress_fn: Callable[[str], None] = print,
) -> List[dict]:
    if output_path is None:
        output_path = os.path.join(os.getcwd(), "downloads")
    os.makedirs(output_path, exist_ok=True)

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {
            executor.submit(download_single_video, url, output_path, i + 1, audio_only, progress_fn): url
            for i, url in enumerate(urls)
        }
        for future in as_completed(future_to_url):
            result = future.result()
            results.append(result)

    return results
