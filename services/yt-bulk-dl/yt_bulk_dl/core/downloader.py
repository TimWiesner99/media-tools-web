"""
YT Bulk Download core — adapted from yt_bulk_download.py for the web.

Progress uses structured dict events:
  {"type": "phase",        "phase": "download"|"done"}
  {"type": "videos_init",  "urls": [str, ...]}
  {"type": "video_info",   "url": str, "title": str, "channel": str}
  {"type": "video_start",  "url": str}
  {"type": "video_done",   "url": str, "success": bool, "filename": str,
                            "title": str, "channel": str, "upload_date": str}
"""

import csv
import io
import json
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import yt_dlp

_rename_lock = threading.Lock()


def sanitize_title(title: str, max_len: int) -> str:
    clean = re.sub(r"\s+", "_", title.strip())
    clean = re.sub(r"[^\w\-]", "", clean)
    if len(clean) > max_len:
        clean = clean[:max_len].rstrip("_")
    return clean


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffixes = "".join(path.suffixes)
    counter = 1
    while path.exists():
        path = path.parent / f"{stem}_{counter}{suffixes}"
        counter += 1
    return path


class EnsureH264PostProcessor(yt_dlp.postprocessor.PostProcessor):
    """Re-encode to H264+AAC if the downloaded streams use different codecs."""

    def run(self, info: dict):
        filepath = info.get("filepath", "")
        if not filepath or not Path(filepath).exists():
            return [], info

        video_codec, audio_codec = self._probe_codecs(filepath)
        needs_video = video_codec not in ("h264", "unknown")
        needs_audio = audio_codec not in ("aac", "unknown")

        if not needs_video and not needs_audio:
            return [], info

        old_path = Path(filepath)
        temp_path = old_path.with_suffix(".tmp.mp4")

        cmd = ["ffmpeg", "-i", str(old_path), "-y"]
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18"] if needs_video else ["-c:v", "copy"]
        cmd += ["-c:a", "aac", "-b:a", "192k"] if needs_audio else ["-c:a", "copy"]
        cmd += ["-movflags", "+faststart", str(temp_path)]

        try:
            subprocess.run(cmd, check=True, capture_output=True)
            old_path.unlink()
            temp_path.rename(old_path)
        except subprocess.CalledProcessError:
            if temp_path.exists():
                temp_path.unlink()

        return [], info

    @staticmethod
    def _probe_codecs(filepath: str) -> tuple[str, str]:
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", filepath],
                capture_output=True, text=True, check=True,
            )
            streams = json.loads(result.stdout).get("streams", [])
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            return ("unknown", "unknown")

        video_codec = audio_codec = "unknown"
        for s in streams:
            if s.get("codec_type") == "video" and video_codec == "unknown":
                video_codec = s.get("codec_name", "unknown")
            elif s.get("codec_type") == "audio" and audio_codec == "unknown":
                audio_codec = s.get("codec_name", "unknown")
        return video_codec, audio_codec


class RenamePostProcessor(yt_dlp.postprocessor.PostProcessor):
    def __init__(self, prefix: str | None, max_len: int):
        super().__init__()
        self.prefix = prefix
        self.max_len = max_len

    def run(self, info: dict):
        title = info.get("title", info.get("id", "unknown"))
        video_id = info.get("id", "unknown")
        clean_title = sanitize_title(title, self.max_len)
        new_base = f"{self.prefix}_{clean_title}" if self.prefix else clean_title

        old_path = Path(info["filepath"])
        new_path = old_path.parent / f"{new_base}{old_path.suffix}"
        if old_path.exists() and old_path != new_path:
            with _rename_lock:
                new_path = _unique_path(new_path)
                old_path.rename(new_path)
            info["filepath"] = str(new_path)

        for srt in old_path.parent.glob(f"{video_id}.*.*"):
            lang_ext = srt.name.removeprefix(f"{video_id}")
            new_srt = srt.parent / f"{new_base}{lang_ext}"
            if srt.exists() and srt != new_srt:
                with _rename_lock:
                    new_srt = _unique_path(new_srt)
                    srt.rename(new_srt)

        return [], info


def download_one(
    url: str,
    opts: dict,
    prefix: str | None,
    max_len: int,
    on_event: Callable[[dict], None],
    global_semaphore=None,
) -> dict:
    """Download a single video, firing progress events via on_event."""
    _info_fired = [False]

    def progress_hook(d: dict) -> None:
        if d["status"] == "downloading" and not _info_fired[0]:
            _info_fired[0] = True
            idict = d.get("info_dict", {})
            on_event({"type": "video_info", "url": url,
                      "title": idict.get("title", url),
                      "channel": idict.get("channel", idict.get("uploader", ""))})
            on_event({"type": "video_start", "url": url})

    class DonePostProcessor(yt_dlp.postprocessor.PostProcessor):
        def __init__(self):
            super().__init__()
            self.metadata: dict | None = None

        def run(self, info: dict):
            upload_date = info.get("upload_date", "")
            if len(upload_date) == 8:
                upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
            self.metadata = {
                "filename": Path(info.get("filepath", "")).name,
                "title": info.get("title", ""),
                "channel": info.get("channel", info.get("uploader", "")),
                "upload_date": upload_date,
                "url": info.get("original_url", info.get("webpage_url", url)),
            }
            on_event({"type": "video_done", "url": url, "success": True,
                      "filename": self.metadata["filename"],
                      "title": self.metadata["title"],
                      "channel": self.metadata["channel"]})
            return [], info

    opts_with_hook = {**opts, "progress_hooks": [progress_hook]}
    done_pp = DonePostProcessor()

    try:
        ctx = global_semaphore if global_semaphore is not None else _NullContext()
        with ctx:
            with yt_dlp.YoutubeDL(opts_with_hook) as ydl:
                ydl.add_post_processor(EnsureH264PostProcessor(), when="post_process")
                ydl.add_post_processor(RenamePostProcessor(prefix, max_len), when="post_process")
                ydl.add_post_processor(done_pp, when="post_process")
                ydl.download([url])
    except Exception:
        pass

    if not _info_fired[0]:
        on_event({"type": "video_info", "url": url, "title": url, "channel": ""})

    if done_pp.metadata is None:
        on_event({"type": "video_done", "url": url, "success": False, "filename": "", "title": "", "channel": ""})
        return {"url": url, "filename": "", "title": "", "channel": "", "upload_date": ""}

    return done_pp.metadata


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def build_opts(download_dir: Path) -> dict:
    return {
        "format": (
            "bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]/"
            "bestvideo[vcodec^=avc1]+bestaudio/"
            "bestvideo+bestaudio/best"
        ),
        "merge_output_format": "mp4",
        "outtmpl": str(download_dir / "%(id)s.%(ext)s"),
        "ignoreerrors": True,
        "retries": 5,
        "fragment_retries": 5,
        "continuedl": True,
        "noplaylist": True,
        "writesubtitles": True,
        "writeautomaticsub": False,
        "subtitleslangs": ["all"],
        "subtitlesformat": "srt",
        "writethumbnail": False,
        "quiet": True,
        "no_warnings": True,
    }


def download_all(
    urls: list[str],
    download_dir: Path,
    prefix: str | None,
    max_len: int,
    max_workers: int,
    on_event: Callable[[dict], None],
    global_semaphore=None,
) -> list[dict]:
    """Download all URLs in parallel, returning metadata rows in input order."""
    opts = build_opts(download_dir)

    def _one(url: str) -> dict:
        return download_one(url, opts, prefix, max_len, on_event, global_semaphore)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        rows = list(executor.map(_one, urls))

    return rows


def write_metadata_csv(rows: list[dict], output_path: Path) -> None:
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["filename", "youtube_title", "channel", "upload_date", "youtube_url"])
        for row in rows:
            writer.writerow([row.get("filename", ""), row.get("title", ""),
                             row.get("channel", ""), row.get("upload_date", ""),
                             row.get("url", "")])


def parse_urls(text: str) -> list[str]:
    """Extract YouTube URLs from text input (one per line, # comments ignored)."""
    urls = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            urls.append(stripped)
    return urls
