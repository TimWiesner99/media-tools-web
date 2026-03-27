# media-tools-web — Project Guide

A unified self-hosted web application wrapping three CLI media tools.
Each tool is a FastAPI sub-app mounted inside a single gateway process.

## Running the app

```bash
uv run --package gateway uvicorn gateway.main:app --reload
```

Serves at `http://localhost:8000`.

## Workspace layout

```
media-tools-web/
├── pyproject.toml                   # uv workspace root (no [project])
├── uv.lock
├── .python-version                  # 3.13
└── services/
    ├── gateway/                     # Root app — homepage + admin + sub-app mounts
    │   └── gateway/
    │       ├── main.py              # app factory; mounts sub-apps + static
    │       ├── admin.py             # /admin — HTTP Basic Auth, runtime settings
    │       ├── static/style.css
    │       └── templates/
    │           ├── base.html        # Shared nav (all four links)
    │           ├── index.html       # Tool cards homepage
    │           └── admin/index.html
    ├── green-to-red/                # Spotify → MP3 converter
    │   └── green_to_red/
    │       ├── main.py
    │       ├── router.py
    │       ├── job_runner.py
    │       ├── settings.py
    │       ├── core/
    │       │   ├── pipeline.py      # run_pipeline() — top-level orchestrator
    │       │   ├── youtube_search.py
    │       │   └── downloader.py
    │       └── templates/green_to_red/
    │           ├── form.html
    │           ├── job_page.html
    │           └── _status_fragment.html
    ├── yt-bulk-dl/                  # YouTube bulk video downloader
    │   └── yt_bulk_dl/
    │       ├── main.py
    │       ├── router.py
    │       ├── job_runner.py
    │       ├── settings.py
    │       ├── core/downloader.py   # download_all(), parse_urls()
    │       └── templates/yt_bulk_dl/
    │           ├── form.html
    │           ├── job_page.html
    │           └── _status_fragment.html
    └── edl-to-archive/              # EDL + source metadata → Excel archive
        └── edl_to_archive/
            ├── main.py
            ├── router.py
            ├── session_store.py     # Cookie-based session + exclusion rules
            ├── core/
            │   ├── pipeline.py      # run_conversion() — synchronous wrapper
            │   ├── converter.py
            │   ├── models.py
            │   ├── exclusion.py
            │   └── timecode.py
            └── templates/edl_to_archive/
                └── form.html
```

## Routes

### Gateway (`/`)
| Method | Path | Notes |
|--------|------|-------|
| GET | `/` | Homepage (tool cards) |
| GET | `/health` | `{"status": "ok"}` |
| GET | `/admin/` | Admin panel — HTTP Basic Auth |
| POST | `/admin/settings/green-to-red` | Update green-to-red runtime settings |
| POST | `/admin/settings/yt-bulk-dl` | Update yt-bulk-dl runtime settings |

### green-to-red (`/green-to-red/`)
| Method | Path | Notes |
|--------|------|-------|
| GET | `/` | Spotify URL form |
| POST | `/convert` | Create job → redirect to status page |
| GET | `/convert/{job_id}` | Status page (full) |
| GET | `/convert/{job_id}/fragment` | HTMX fragment — polled every 3 s |
| GET | `/convert/{job_id}/download` | Stream ZIP (MP3s + CSV) |

### yt-bulk-dl (`/yt-bulk-dl/`)
| Method | Path | Notes |
|--------|------|-------|
| GET | `/` | URL textarea form |
| POST | `/convert` | Create job → redirect to status page |
| GET | `/convert/{job_id}` | Status page (full) |
| GET | `/convert/{job_id}/fragment` | HTMX fragment — polled every 3 s |
| GET | `/convert/{job_id}/download` | Stream ZIP (videos + CSV) |

### edl-to-archive (`/edl-to-archive/`)
| Method | Path | Notes |
|--------|------|-------|
| GET | `/` | Upload form (session loaded from cookie) |
| POST | `/convert` | Upload → convert → stream XLSX directly |
| GET | `/template/{kind}` | Download blank `edl` or `source` template |

## Job model — green-to-red

```python
job_id: str
status: str          # pending | running | done | error
phase: str           # pending | spotify | tracks | done | error
mb_status: str       # pending | running | done  (MusicBrainz lookup)
content_name: str | None
track_states: list[TrackState]
result: PipelineResult | None
error: str | None
created_at: datetime
output_dir: Path | None
```

`TrackState` fields: `name`, `yt_status` (`pending|found|not_found`), `dl_status` (`pending|downloading|done|error`).

## Job model — yt-bulk-dl

```python
job_id: str
status: str          # pending | running | done | error
phase: str           # pending | download | done | error
video_states: list[VideoState]
prefix: str | None
max_length: int
output_dir: Path | None
error: str | None
created_at: datetime
```

`VideoState` fields: `url`, `display` (URL until title known), `status` (`pending|downloading|done|error`), `filename`, `channel`.

## Progress events

Both job-based services use a `cb(dict)` callback from pipeline → job runner.

### green-to-red events
| `type` | Key fields | Effect |
|--------|-----------|--------|
| `phase` | `phase` | Advance pipeline phase |
| `spotify_done` | `content_name` | Set playlist/album name |
| `tracks_init` | `names[]` | Initialise track list |
| `yt_result` | `name`, `found` | Mark track YouTube status |
| `dl_start` | `name` | Mark track downloading |
| `dl_done` | `name`, `success` | Mark track done/error |
| `mb_start` | — | MusicBrainz → running |
| `mb_done` | — | MusicBrainz → done |

### yt-bulk-dl events
| `type` | Key fields | Effect |
|--------|-----------|--------|
| `phase` | `phase` | Advance pipeline phase |
| `videos_init` | `urls[]` | Initialise video list |
| `video_info` | `url`, `title`, `channel` | Set video display name |
| `video_start` | `url` | Mark video downloading |
| `video_done` | `url`, `success`, `filename` | Mark video done/error |

## Settings (runtime, in-memory)

Both green-to-red and yt-bulk-dl expose `settings.py` with:
- `max_workers_per_job` — concurrent downloads per job
- `max_workers_global` — total concurrent downloads (global semaphore)

Defaults and allowed ranges are enforced in `admin.py`. Settings reset on server restart.

## Session store — edl-to-archive

Sessions are persisted as JSON files keyed by a UUID cookie (`edl_session`, 1-year, httponly).

```
$MEDIA_TOOLS_DATA/edl_sessions/{uuid}.json
```

`UserSession` fields: `session_id`, `exclusion_rules` (list of rule strings), `fps` (25), `collapse` (True), `include_frames` (False).

Uploaded input files are deleted in a `try/finally` block immediately after conversion — they are never stored between requests.

## Environment variables

| Variable | Default | Used by |
|----------|---------|---------|
| `ADMIN_PASSWORD` | `"admin"` | `gateway/admin.py` — HTTP Basic Auth password |
| `MEDIA_TOOLS_DATA` | `~/.media-tools` | `edl-to-archive/session_store.py` — session JSON root |

## Shared patterns

**Template inheritance** — each service has its own `base.html` (identical nav, CDN links for Pico CSS + HTMX). The gateway's `base.html` is used by the gateway and admin pages; sub-apps use their own.

**HTMX polling** — `_status_fragment.html` is the polling target. When `job.status` is `done` or `error` the fragment renders without the `hx-trigger` attribute, stopping polling automatically.

**TemplateResponse API** — Starlette ≥ 1.0 signature: `TemplateResponse(request, "name.html", context_dict)`. Do **not** pass `{"request": request, ...}`.

**Sub-app mounting** — gateway wraps each import in `try/except ImportError` so the server starts even if a service package is missing.

**Concurrency** — pipelines run in `ThreadPoolExecutor`; individual yt-dlp downloads use a nested executor and acquire the global semaphore before starting.

**Cleanup** — green-to-red lifespan coroutine deletes output dirs older than 2 hours every 30 minutes. yt-bulk-dl follows the same pattern.
