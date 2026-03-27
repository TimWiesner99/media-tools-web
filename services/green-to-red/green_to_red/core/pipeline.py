"""
green-to-red pipeline — web-adapted version of green_to_red.py.

Changes from the original CLI script:
- print()   -> progress_callback(msg)
- sys.exit() -> raise PipelineError(msg)
- Hardcoded ~/Downloads/... -> caller-supplied job_dir
- load_converter() / load_downloader() -> direct imports
- main() / argparse removed; replaced with run_pipeline()
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


def _truncate_path_component(name, max_bytes=MAX_FILENAME_BYTES):
    if len(name.encode("utf-8")) <= max_bytes:
        return name
    while len(name.encode("utf-8")) > max_bytes:
        name = name[:-1]
    return name.rstrip()


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


def fetch_spotify_content(client, url, url_type, progress_callback):
    if url_type == "track":
        progress_callback("Fetching track from Spotify...")
        track_data = client.get_track_info(url)
        name = track_data["name"]
        artists = track_data.get("artists", [])
        artist_str = artists[0]["name"] if artists else "Unknown"
        display_name = f"{artist_str} - {name}"
        progress_callback(f"Track: {name} — {artist_str}")
        return [_normalize_track(track_data)], display_name

    elif url_type == "album":
        progress_callback("Fetching album from Spotify...")
        album = client.get_album_info(url)
        album_name = album["name"]
        album_artists = album.get("artists", [])
        total_tracks = album.get("total_tracks", len(album.get("tracks", [])))
        artist_str = ", ".join(a["name"] for a in album_artists)
        progress_callback(f"Album: {album_name} — {artist_str} ({total_tracks} tracks)")

        raw_tracks = album.get("tracks", [])
        if total_tracks > len(raw_tracks):
            progress_callback(f"Note: Only {len(raw_tracks)} of {total_tracks} tracks available via scraping.")

        tracks = []
        for t in raw_tracks:
            if not t.get("artists"):
                t["artists"] = album_artists
            tracks.append(_normalize_track(t))
        return tracks, album_name

    else:  # playlist
        progress_callback("Fetching playlist from Spotify...")
        playlist = client.get_playlist_info(url)
        playlist_name = playlist["name"]
        total_tracks = playlist.get("track_count", len(playlist["tracks"]))
        progress_callback(f"Playlist: {playlist_name} ({total_tracks} tracks)")

        if total_tracks > len(playlist["tracks"]):
            progress_callback(f"Note: Only the first {len(playlist['tracks'])} tracks are available via scraping.")

        tracks = [_normalize_track(t) for t in playlist["tracks"]]
        return tracks, playlist_name


def _lookup_artist_formats(tracks, progress_callback):
    unique_names = sorted(
        {a["name"] for t in tracks if t.get("track") for a in t["track"]["artists"]}
    )
    cache = {}
    progress_callback(f"MusicBrainz: looking up {len(unique_names)} artists...")
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


def _lookup_track_metadata(tracks, progress_callback):
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
    progress_callback(f"MusicBrainz: looking up metadata for {n} tracks (~{n * 3}s)...")

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
            progress_callback(f"  MusicBrainz: {i + 1}/{n} tracks done")

    if release_ids:
        progress_callback(f"MusicBrainz: fetching labels for {len(release_ids)} albums...")
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


def generate_song_info(tracks, video_ids, content_name, output_dir, progress_callback):
    progress_callback("Generating song information CSV...")

    artist_fmt = {}
    metadata_map = {}
    if HAS_MUSICBRAINZ:
        musicbrainzngs.set_useragent(*MUSICBRAINZ_USERAGENT)
        artist_fmt = _lookup_artist_formats(tracks, progress_callback)
        metadata_map = _lookup_track_metadata(tracks, progress_callback)
    else:
        progress_callback("musicbrainzngs not installed — skipping metadata lookups.")

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
    progress_callback(f"CSV saved: {len(rows)} tracks, {with_composers} with composer data.")
    return csv_path


def rename_downloaded_files(tracks, video_ids, download_results, output_dir, progress_callback):
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

    renamed = 0
    skipped = 0
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

        if old_path.exists():
            if new_path.exists():
                skipped += 1
                continue
            old_path.rename(new_path)
            renamed += 1
        else:
            skipped += 1

    progress_callback(f"Renamed {renamed} file(s) to 'Artist - Title' format.")


def run_pipeline(
    spotify_url: str,
    job_dir: Path,
    progress_callback: Callable[[str], None],
    workers: int = 3,
) -> PipelineResult:
    """
    Run the full green-to-red pipeline for a web request.

    Raises PipelineError on fatal errors.
    Returns a PipelineResult with paths to output files.
    """
    from spotify_scraper import SpotifyClient
    from yt_dlp.utils import sanitize_filename

    # Detect URL type and fetch Spotify tracks
    url_type = detect_spotify_type(spotify_url)
    client = SpotifyClient(log_level="WARNING")
    try:
        tracks, content_name = fetch_spotify_content(client, spotify_url, url_type, progress_callback)
    finally:
        client.close()

    content_name = sanitize_filename(content_name, restricted=False)
    content_name = _truncate_path_component(content_name)

    if not tracks:
        raise PipelineError("No tracks found for this Spotify URL.")

    progress_callback(f"Searching YouTube for {len(tracks)} tracks...")
    with ThreadPoolExecutor() as executor:
        video_ids = list(executor.map(get_youtube_link, tracks))

    youtube_urls = []
    not_found = []
    for track, video_id in zip(tracks, video_ids):
        track_name = track["track"]["name"]
        artists = ", ".join(a["name"] for a in track["track"]["artists"])
        if video_id:
            youtube_urls.append(f"https://www.youtube.com/watch?v={video_id}")
            progress_callback(f"[+] {track_name} — {artists}")
        else:
            not_found.append(f"{track_name} — {artists}")
            progress_callback(f"[-] Not found: {track_name} — {artists}")

    progress_callback(f"Found {len(youtube_urls)} of {len(tracks)} tracks on YouTube.")

    if not youtube_urls:
        raise PipelineError("None of the tracks could be found on YouTube.")

    mp3_dir = job_dir / content_name
    mp3_dir.mkdir(parents=True, exist_ok=True)
    workers = max(1, min(5, workers))

    progress_callback(f"Downloading {len(youtube_urls)} track(s) as MP3 ({workers} workers)...")

    with ThreadPoolExecutor(max_workers=2) as executor:
        dl_future = executor.submit(
            download_youtube_content,
            urls=youtube_urls,
            output_path=str(mp3_dir),
            audio_only=True,
            max_workers=workers,
            progress_fn=progress_callback,
        )
        csv_future = executor.submit(
            generate_song_info,
            tracks,
            video_ids,
            content_name,
            str(mp3_dir),
            progress_callback,
        )
        download_results = dl_future.result()
        csv_path = csv_future.result()

    progress_callback("Renaming files to 'Artist - Title' format...")
    rename_downloaded_files(tracks, video_ids, download_results or [], mp3_dir, progress_callback)

    downloaded_count = sum(1 for r in (download_results or []) if r.get("success"))
    progress_callback(f"Done! {downloaded_count} MP3(s) ready.")

    return PipelineResult(
        content_name=content_name,
        track_count=len(tracks),
        downloaded_count=downloaded_count,
        not_found=not_found,
        csv_path=csv_path,
        mp3_dir=mp3_dir,
    )
