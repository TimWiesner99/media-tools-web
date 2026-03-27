"""
green-to-red pipeline — web-adapted version of green_to_red.py.

Progress reporting uses structured dict events (not strings):
  {"type": "phase",        "phase": "spotify"|"tracks"|"done"}
  {"type": "spotify_done", "content_name": str, "track_count": int}
  {"type": "tracks_init",  "names": [str, ...]}
  {"type": "yt_result",    "name": str, "found": bool}
  {"type": "dl_start",     "name": str}
  {"type": "dl_done",      "name": str, "success": bool}
  {"type": "mb_start"}                          ← MusicBrainz lookup started (runs parallel to downloads)
  {"type": "mb_done"}                           ← CSV ready
  {"type": "note",         "msg": str}          ← internal progress, ignored by UI
"""

import csv
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

try:
    import musicbrainzngs
    HAS_MUSICBRAINZ = True
except ImportError:
    HAS_MUSICBRAINZ = False

from green_to_red.core.youtube_search import get_youtube_link
from green_to_red.core.downloader import download_youtube_content

MAX_FILENAME_BYTES = 200

MUSICBRAINZ_USERAGENT = (
    "green-to-red-music",
    "1.0",
    "green-to-red-music@users.noreply.github.com",
)


class PipelineError(Exception):
    pass


@dataclass
class PipelineResult:
    content_name: str
    track_count: int
    downloaded_count: int
    not_found: list[str]
    csv_path: Path
    mp3_dir: Path


def _note(cb: Callable[[dict], None], msg: str) -> None:
    cb({"type": "note", "msg": msg})


def _truncate_path_component(name, max_bytes=MAX_FILENAME_BYTES):
    if len(name.encode("utf-8")) <= max_bytes:
        return name
    while len(name.encode("utf-8")) > max_bytes:
        name = name[:-1]
    return name.rstrip()


def _track_display_name(track: dict) -> str:
    t = track["track"]
    artist = t["artists"][0]["name"] if t["artists"] else "Unknown"
    return f"{artist} - {t['name']}"


def detect_spotify_type(url):
    url = url.strip()
    m = re.search(r"open\.spotify\.com/(?:embed/)?(playlist|album|track)/", url)
    if m:
        return m.group(1)
    m = re.match(r"spotify:(playlist|album|track):", url)
    if m:
        return m.group(1)
    return "playlist"


def _normalize_track(track_data):
    track_id = (
        track_data.get("uri", "").split(":")[-1]
        if track_data.get("uri")
        else track_data.get("id", "")
    )
    return {
        "track": {
            "name": track_data["name"],
            "artists": track_data.get("artists", []),
            "duration_ms": track_data.get("duration_ms", 0),
            "id": track_id,
            "uri": track_data.get("uri", ""),
        }
    }


def fetch_spotify_content(client, url, url_type, cb):
    if url_type == "track":
        _note(cb, "Fetching track from Spotify...")
        track_data = client.get_track_info(url)
        name = track_data["name"]
        artists = track_data.get("artists", [])
        artist_str = artists[0]["name"] if artists else "Unknown"
        display_name = f"{artist_str} - {name}"
        return [_normalize_track(track_data)], display_name

    elif url_type == "album":
        _note(cb, "Fetching album from Spotify...")
        album = client.get_album_info(url)
        album_name = album["name"]
        album_artists = album.get("artists", [])
        total_tracks = album.get("total_tracks", len(album.get("tracks", [])))
        raw_tracks = album.get("tracks", [])
        if total_tracks > len(raw_tracks):
            _note(cb, f"Note: Only {len(raw_tracks)} of {total_tracks} tracks available via scraping.")
        tracks = []
        for t in raw_tracks:
            if not t.get("artists"):
                t["artists"] = album_artists
            tracks.append(_normalize_track(t))
        return tracks, album_name

    else:  # playlist
        _note(cb, "Fetching playlist from Spotify...")
        playlist = client.get_playlist_info(url)
        playlist_name = playlist["name"]
        total_tracks = playlist.get("track_count", len(playlist["tracks"]))
        if total_tracks > len(playlist["tracks"]):
            _note(cb, f"Note: Only the first {len(playlist['tracks'])} tracks are available via scraping.")
        tracks = [_normalize_track(t) for t in playlist["tracks"]]
        return tracks, playlist_name


def _lookup_artist_formats(tracks, str_cb):
    unique_names = sorted(
        {a["name"] for t in tracks if t.get("track") for a in t["track"]["artists"]}
    )
    cache = {}
    str_cb(f"Looking up {len(unique_names)} artists on MusicBrainz...")
    for name in unique_names:
        try:
            result = musicbrainzngs.search_artists(artist=name, limit=1)
            artists = result.get("artist-list", [])
            if artists and artists[0].get("type") == "Person":
                cache[name] = artists[0].get("sort-name", name)
            else:
                cache[name] = name
        except Exception:
            cache[name] = name
        time.sleep(1)
    return cache


def _lookup_track_metadata(tracks, str_cb):
    unique_tracks = []
    seen = set()
    for t in tracks:
        if not t.get("track"):
            continue
        artist = t["track"]["artists"][0]["name"] if t["track"]["artists"] else ""
        title = t["track"]["name"]
        key = (artist, title)
        if key not in seen:
            unique_tracks.append(key)
            seen.add(key)

    n = len(unique_tracks)
    str_cb(f"Looking up metadata for {n} tracks on MusicBrainz (~{n * 3}s)...")

    cache = {}
    release_ids = {}

    for i, (artist, title) in enumerate(unique_tracks):
        entry = {"composers": [], "isrc": "", "album": "", "release_date": "", "label": ""}
        try:
            result = musicbrainzngs.search_recordings(recording=title, artist=artist, limit=1)
            time.sleep(1)
            rec_list = result.get("recording-list", [])
            if not rec_list:
                cache[(artist, title)] = entry
                continue
            rec = rec_list[0]
            rec_id = rec["id"]
            releases = rec.get("release-list", [])
            if releases:
                release = releases[0]
                entry["album"] = release.get("title", "")
                entry["release_date"] = release.get("date", "")
                release_id = release.get("id", "")
                if release_id:
                    release_ids.setdefault(release_id, set()).add((artist, title))
            rec_detail = musicbrainzngs.get_recording_by_id(rec_id, includes=["work-rels", "isrcs"])
            time.sleep(1)
            recording = rec_detail.get("recording", {})
            isrc_list = recording.get("isrc-list", [])
            if isrc_list:
                entry["isrc"] = isrc_list[0]
            work_rels = recording.get("work-relation-list", [])
            perf = [r for r in work_rels if r.get("type") == "performance"]
            if perf:
                work_id = perf[0]["work"]["id"]
                work = musicbrainzngs.get_work_by_id(work_id, includes=["artist-rels"])
                time.sleep(1)
                for rel in work.get("work", {}).get("artist-relation-list", []):
                    if rel.get("type") in ("composer", "writer"):
                        entry["composers"].append(rel["artist"].get("sort-name", rel["artist"]["name"]))
        except Exception:
            pass
        cache[(artist, title)] = entry
        if (i + 1) % 10 == 0:
            str_cb(f"MusicBrainz: {i + 1}/{n} tracks done")

    if release_ids:
        str_cb(f"Fetching labels for {len(release_ids)} albums...")
        for release_id in release_ids:
            try:
                rel = musicbrainzngs.get_release_by_id(release_id, includes=["labels"])
                time.sleep(1)
                label_info = rel.get("release", {}).get("label-info-list", [])
                if label_info:
                    label_name = label_info[0].get("label", {}).get("name", "")
                    for key in release_ids[release_id]:
                        if key in cache:
                            cache[key]["label"] = label_name
            except Exception:
                pass

    return cache


def generate_song_info(tracks, video_ids, content_name, output_dir, cb):
    note_cb = lambda msg: _note(cb, msg)  # noqa: E731
    cb({"type": "mb_start"})

    artist_fmt = {}
    metadata_map = {}
    if HAS_MUSICBRAINZ:
        musicbrainzngs.set_useragent(*MUSICBRAINZ_USERAGENT)
        artist_fmt = _lookup_artist_formats(tracks, note_cb)
        metadata_map = _lookup_track_metadata(tracks, note_cb)

    fieldnames = [
        "Title", "Artist", "Composer", "Composer 2", "Label",
        "Publication Year", "Album", "Duration", "ISRC", "UPC",
        "Spotify URL", "YouTube URL",
    ]

    rows = []
    for track, video_id in zip(tracks, video_ids):
        t = track.get("track")
        if not t:
            continue
        first_artist = t["artists"][0]["name"] if t["artists"] else ""
        meta = metadata_map.get((first_artist, t["name"]), {})
        parts = [artist_fmt.get(a["name"], a["name"]) for a in t["artists"]]
        artist_str = " & ".join(parts)
        composers = meta.get("composers", [])
        dur_ms = t.get("duration_ms", 0)
        mins, secs = divmod(dur_ms // 1000, 60)
        track_id = t.get("id", "")
        spotify_url = f"https://open.spotify.com/track/{track_id}" if track_id else ""
        youtube_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
        rows.append({
            "Title": t["name"],
            "Artist": artist_str,
            "Composer": composers[0] if composers else "",
            "Composer 2": composers[1] if len(composers) > 1 else "",
            "Label": meta.get("label", ""),
            "Publication Year": meta.get("release_date", "")[:4],
            "Album": meta.get("album", ""),
            "Duration": f"{mins}:{secs:02d}",
            "ISRC": meta.get("isrc", ""),
            "UPC": "",
            "Spotify URL": spotify_url,
            "YouTube URL": youtube_url,
        })

    os.makedirs(output_dir, exist_ok=True)
    csv_path = Path(output_dir) / f"_{content_name} - Song Information.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    with_composers = sum(1 for r in rows if r["Composer"])
    note_cb(f"CSV saved: {len(rows)} tracks, {with_composers} with composer data.")
    cb({"type": "mb_done"})
    return csv_path


def rename_downloaded_files(tracks, video_ids, download_results, output_dir):
    from yt_dlp.utils import sanitize_filename

    url_to_track = {}
    for track, video_id in zip(tracks, video_ids):
        if not video_id or not track.get("track"):
            continue
        t = track["track"]
        artist = t["artists"][0]["name"] if t["artists"] else "Unknown"
        title = t["name"]
        url = f"https://www.youtube.com/watch?v={video_id}"
        url_to_track[url] = (artist, title)

    for result in download_results:
        if not result.get("success") or not result.get("title"):
            continue
        url = result["url"]
        if url not in url_to_track:
            continue
        artist, title = url_to_track[url]
        old_name = sanitize_filename(result["title"], restricted=False) + ".mp3"
        base = sanitize_filename(f"{artist} - {title}", restricted=False)
        new_name = _truncate_path_component(base) + ".mp3"
        if old_name == new_name:
            continue
        old_path = Path(output_dir) / old_name
        new_path = Path(output_dir) / new_name
        if old_path.exists() and not new_path.exists():
            old_path.rename(new_path)


def run_pipeline(
    spotify_url: str,
    job_dir: Path,
    progress_callback: Callable[[dict], None],
    workers: int = 5,
    global_semaphore=None,
) -> PipelineResult:
    """
    Run the full green-to-red pipeline.
    progress_callback receives structured dict events (see module docstring).
    Raises PipelineError on fatal errors.
    """
    from spotify_scraper import SpotifyClient
    from yt_dlp.utils import sanitize_filename

    cb = progress_callback
    note = lambda msg: cb({"type": "note", "msg": msg})  # noqa: E731

    # ── 1. Spotify ──────────────────────────────────────────────────────────
    cb({"type": "phase", "phase": "spotify"})
    client = SpotifyClient(log_level="WARNING")
    try:
        url_type = detect_spotify_type(spotify_url)
        tracks, content_name = fetch_spotify_content(client, spotify_url, url_type, cb)
    finally:
        client.close()

    content_name = sanitize_filename(content_name, restricted=False)
    content_name = _truncate_path_component(content_name)

    if not tracks:
        raise PipelineError("No tracks found for this Spotify URL.")

    cb({"type": "spotify_done", "content_name": content_name, "track_count": len(tracks)})

    # ── 2. YouTube search + Download + Metadata (all in "tracks" phase) ────
    cb({"type": "phase", "phase": "tracks"})

    display_names = [_track_display_name(t) for t in tracks]
    cb({"type": "tracks_init", "names": display_names})

    with ThreadPoolExecutor() as executor:
        video_ids = list(executor.map(get_youtube_link, tracks))

    youtube_urls = []
    url_to_name: dict[str, str] = {}
    not_found: list[str] = []

    for track, video_id, dname in zip(tracks, video_ids, display_names):
        if video_id:
            url = f"https://www.youtube.com/watch?v={video_id}"
            youtube_urls.append(url)
            url_to_name[url] = dname
            cb({"type": "yt_result", "name": dname, "found": True})
        else:
            not_found.append(dname)
            cb({"type": "yt_result", "name": dname, "found": False})

    if not youtube_urls:
        raise PipelineError("None of the tracks could be found on YouTube.")

    mp3_dir = job_dir / content_name
    mp3_dir.mkdir(parents=True, exist_ok=True)
    workers = max(1, min(5, workers))

    with ThreadPoolExecutor(max_workers=2) as executor:
        dl_future = executor.submit(
            download_youtube_content,
            urls=youtube_urls,
            output_path=str(mp3_dir),
            audio_only=True,
            max_workers=workers,
            on_track_start=lambda name: cb({"type": "dl_start", "name": name}),
            on_track_done=lambda name, ok: cb({"type": "dl_done", "name": name, "success": ok}),
            url_to_name=url_to_name,
            global_semaphore=global_semaphore,
        )
        csv_future = executor.submit(
            generate_song_info,
            tracks,
            video_ids,
            content_name,
            str(mp3_dir),
            cb,
        )
        download_results = dl_future.result()
        csv_path = csv_future.result()

    rename_downloaded_files(tracks, video_ids, download_results or [], mp3_dir)

    downloaded_count = sum(1 for r in (download_results or []) if r.get("success"))

    return PipelineResult(
        content_name=content_name,
        track_count=len(tracks),
        downloaded_count=downloaded_count,
        not_found=not_found,
        csv_path=csv_path,
        mp3_dir=mp3_dir,
    )
