# Tim's media tools

A small collection of useful tools for media production, made during my internship at the VPRO.

On a remote server, run he webserver with
```
uv run --package gateway uvicorn gateway.main:app --host 0.0.0.0 --port 8000
```

## edl-to-archive
Simple script to convert EDL and archive source lists to complete archive lists with timecodes and source links.

### Exclusion Rules
Exclusion rules allow you to filter out EDL entries before processing. Rules are defined in a text file, one rule per line.

```
field_name OPERATOR "value"
```

**Operators:**
- `IS` - exact match (case-sensitive)
- `INCLUDES` - substring match (case-sensitive)

**Logical operators:**
- `AND` - both conditions must be true
- `OR` - either condition must be true
- `NOT` - negates the following expression
- `()` - parentheses for grouping

Lines are OR'd together: if ANY line matches, the entry is excluded.

### Available Fields

| Field Name | Aliases | Description |
|------------|---------|-------------|
| `name` | `Name`, `NAME` | Clip name |
| `file_name` | `FileName`, `filename`, `Bestandsnaam` | Source file name |
| `reel` | `Reel`, `REEL` | Reel identifier |
| `track` | `Track`, `TRACK` | Track name |
| `comment` | `Comment`, `COMMENT` | Entry comment |


## green-to-red music
Simple tool that converts a Spotify playlist into downloaded MP3 files. Give it a Spotify playlist URL and it will find each track on YouTube, download them as MP3s, and rename them to the standard "Artist - Title" format.

Also generates a detailed CSV file with licensing metadata (composers, labels, ISRCs) sourced from MusicBrainz.

Built on [SysGarcia's Playlist-converter](https://github.com/SysGarcia/Playlist-converter) and [pH-7's Download-Simply-Videos-From-YouTube](https://github.com/pH-7/Download-Simply-Videos-From-YouTube).

### How it works

1. Fetches all tracks from the given Spotify playlist via web scraping (no API credentials needed)
2. Searches YouTube for each track (by track name + artist) in parallel
3. Downloads every matched video as an MP3 using yt-dlp, while simultaneously looking up licensing metadata (artist formatting, composers) on MusicBrainz
4. Renames downloaded files to "Artist - Title.mp3" format
5. Outputs a CSV with full song metadata for licensing purposes

**No API credentials needed** — the tool uses web scraping for Spotify playlists, `youtube-search` for YouTube lookups, and `yt-dlp` for downloads.

## yt-bulk-dl
Python script that batch-downloads YouTube videos in the highest available quality. Reads URLs from a text file, merges the best video + audio streams, downloads multiple videos in parallel, and optionally fetches manually-added subtitles as `.srt` sidecar files.

`metadata.csv` contains one row per URL from `download-list.txt` (in input order), with columns: `filename`, `youtube_title`, `channel`, `upload_date`, `youtube_url`. The order of rows always matches the order of URLs in `download-list.txt` regardless of which download finishes first.

### Notes
- Auto-generated subtitles are excluded; only manually-added subs are downloaded.
- Playlist links download only the single linked video (set `noplaylist: False` in the script to change this).
