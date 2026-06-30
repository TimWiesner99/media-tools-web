# Tim's media tools

A small collection of useful tools for media production, made during my internship at the VPRO.

## Shared UI layout

The site-wide header and footer are now defined in one place:
`services/media_tools_ui/templates/base.html`

The global CSS and shared logos are also now defined in one place:
`services/media_tools_ui/static/`

Gateway pages and module pages still extend `base.html`, but each app now creates its Jinja environment through
`services/media_tools_ui/templating.py`. Template lookup is ordered like this:

1. The app's own local templates directory
2. The shared UI templates directory

That keeps page-specific templates inside each service while making global header, footer, navigation, CSS, and shared logo changes a one-file edit.

On a remote server, run he webserver with
```
uv run --package gateway uvicorn gateway.main:app --host 0.0.0.0 --port 8000
```

## EDL to Archive
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


## Spotify to MP3
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

## YouTube bulk download
Python script that batch-downloads YouTube videos in the highest available quality. Reads URLs from a text file, merges the best video + audio streams, downloads multiple videos in parallel, and optionally fetches manually-added subtitles as `.srt` sidecar files.

`metadata.csv` contains one row per URL from `download-list.txt` (in input order), with columns: `filename`, `youtube_title`, `channel`, `upload_date`, `youtube_url`. The order of rows always matches the order of URLs in `download-list.txt` regardless of which download finishes first.

### Notes
- Auto-generated subtitles are excluded; only manually-added subs are downloaded.
- Playlist links download only the single linked video (set `noplaylist: False` in the script to change this).

## Transcribe

A minimal transcription tab. Upload a video or audio file and get back a transcript as
plain text (`.txt`) or subtitles (`.srt`). The heavy lifting (running Whisper on a GPU) is
done by a **separate, self-hosted transcription service that I build myself** (a WhisperX-based
microservice in its own repo), which exposes an OpenAI-compatible audio API and runs in its own
GPU LXC. Beyond basic transcription it also supports speaker diarization and word-accurate
timestamps, though this minimal tab only uses plain transcription. This tab handles the upload,
the audio extraction, talking to that service, and showing progress.

### How it works

1. You upload a media file (video or audio).
2. The tab immediately transcodes it to a small Whisper-friendly audio file
   (16 kHz mono) with ffmpeg, and **deletes the original upload** to save space on the
   server (which has limited storage).
3. The extracted audio is sent to the transcription service, which exposes an **OpenAI-compatible**
   audio API (the same `POST /v1/audio/transcriptions` endpoint and formats as OpenAI's own Whisper
   API), authenticated with a shared bearer token.
4. The tab runs that call inside a background job and shows live progress in the browser, following
   the same job-status pattern as `spotify_dl` and `yt_bulk_dl`.
5. When the job is done, you download the transcript as `.srt` or `.txt`.

The service loads models on demand and unloads them after an idle timeout to free VRAM (the GPU is
shared with other services), so the **first request after a quiet period is slower** (the model has
to load) and the service may briefly return HTTP 429 while it's busy — the tab handles both
gracefully rather than failing the job.

This tab does **not** run Whisper itself and has no GPU dependency — it can run on the same
office-grade server as the rest of the collection. The transcription service can run anywhere
reachable on the network (currently a dedicated GPU LXC on a second Proxmox host; because it's
OpenAI-compatible it could later be repointed at cloud Whisper or another compatible server without
changing this tab).

### Configuration

| Variable | Purpose |
| --- | --- |
| `TRANSCRIBE_SERVICE_URL` | Base URL of the transcription service (OpenAI-style, e.g. ends in `/v1`) |
| `TRANSCRIBE_API_TOKEN` | Shared bearer token / API key sent with every request |
| `TRANSCRIBE_MODEL` | Whisper model id the service should use (e.g. a `large-v3-turbo` CTranslate2 model) |

Because the service speaks the OpenAI audio API, this tab can talk to it with the official `openai`
client (point `base_url` at `TRANSCRIBE_SERVICE_URL`) or a plain HTTP client — and it could be
repointed at OpenAI's hosted Whisper or any other compatible server without code changes.

If `TRANSCRIBE_SERVICE_URL` is unset the tab still loads but shows a "transcription service
not configured" message instead of accepting uploads.