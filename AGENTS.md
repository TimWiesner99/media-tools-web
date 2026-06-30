# AGENTS.md

This file provides guidance to AI agents like Claude Code (claude.ai/code) or GPT when working with code in this repository.

## Hardware and Performance Requirements

This project is a prototype of software that will eventually run on a company-internal server. Heavy AI-services like LLMs will be provided externally. This program must be able to interface with these external services, but internal environments can be spun up for testing and prototyping. The users must be able to run everything on office-grade laptops from their browser, so the heavy-lifting must be done by the server. Keep scalability in mind, but don't worry about throusands of concurrent users. Realistically, this service would be accessed by no more than 50 concurrent users.

The code is being tested on a Dell laptop without dedicated graphics card.

The current production server is an LXC container running on a Proxmox server with 4GB of DDR3 memory and 128GB of mounted storage at `~/data`. A second dedicated LXC on a second Proxmox host (with a GTX 1650, 4GB VRAM) runs a self-hosted, OpenAI-compatible **WhisperX-based transcription service** (built and maintained separately, in its own repo). This `media-tools-web` collection only ever *calls* that service over the network.

## Plan & Review

### Before starting work
- Always in plan mode to make a plan
- After get the plan, make sure you Write the plan to .claude/tasks/TASK_NAME.md.
- The plan should be a detailed implementation plan and the reasoning behind them, as well as tasks broken down.
- If the task require external knowledge or certain package, also research to get latest knowledge (Use Task tool for research)
- Don't over plan it, always think MVP.
- Once you write the plan, firstly ask me to review it. Do not continue until I approve the plan.

### While implementing
- You should update the plan as you work.
- After you complete tasks in the plan, you should update and append detailed descriptions of the changes you made, so following tasks can be easily hand over to other engineers.
- When adding new functions, include a proper docstring.
- Run processes in parallel whenever possible. Use multiple agents in order to do so.
- If the task touches more than one file, summarize the plan in bullet points and wait for confirmation before editing.
- When making changes, keep the code as clean and readable as possible. Removing unnecessary code is imperative, and simplifying existing code, instead of adding on more and more, is always preferred.
- If there are any inconsistencies between existing code, new additions or my prompts, please present them to me and let me make choices, before solving them yourself.

### Validation

- Always run tests before marking work complete.
- Current test command: `uv run pytest`.
- If there are no tests covering the change, run the most relevant command available and say so explicitly.

### Response Style

- Keep responses short and direct.
- Ask one clarifying question when needed, not several.
- Prefer links to existing docs over copying long architecture explanations into responses or instructions.
- When researching ways to implement new features, keep the generated overviews brief and focused on the core techniques and frameworks being used.

### Git

- Branch naming: `feature/short-description` or `fix/short-description`.
- Commit messages should follow conventional commits.


## Commands

```bash
# Run the webserver locally for testing (serves at http://localhost:8000)
uv run --package gateway uvicorn gateway.main:app --reload

# Run a specific service in isolation (example)
uv run --package spotify_dl uvicorn spotify_dl.main:app --reload --port 8001

# In production/on a remote server, run the webserver with
uv run --package gateway uvicorn gateway.main:app --host 0.0.0.0 --port 8000
```

There are no tests or linting configured in this project.

## Architecture

A `uv` workspace monorepo. The `gateway` service is the only entry point — it imports and mounts the other services as ASGI sub-apps at `/spotify-dl`, `/yt-bulk-dl`, and `/edl-to-archive`, and so forth. Each sub-app mount is wrapped in `try/except ImportError` so the server starts even if a service package is missing.

The `transcribe` service follows the **job-queue pattern** (like `spotify_dl` and
`yt_bulk_dl`), with one important difference: it is a **thin client of an external,
OpenAI-compatible transcription microservice that I build and run myself** (a WhisperX-based
service in its own repo, deployed in its own GPU LXC) — not a self-contained pipeline. That
service exposes OpenAI-compatible `/v1/audio/transcriptions` for basic transcription, **plus
extensions** for word-level timestamps and speaker diarization (speaker-labelled SRT). This
minimal tab uses only the basic-transcription path; the diarization extension is reserved for the
richer tool we'll build later. The tab's pipeline does three things: extract audio from the upload
with ffmpeg (then delete the original), call the service's `POST /v1/audio/transcriptions`
endpoint, and relay its result/progress back into local `Job` events. No model runs inside this
repo and there is no GPU dependency here. Because the service speaks the OpenAI audio API, the call
can use the official `openai` Python client (`base_url=TRANSCRIBE_SERVICE_URL`,
`api_key=TRANSCRIBE_API_TOKEN`) or a plain HTTP client.

Mount it in `gateway/main.py` at `/transcribe` using the same `try/except ImportError`
guard as the other services.

Remeber these general rules:

- Prefer GPU-first detection paths, but fall back to CPU-based computation of no GPU is present.
- When working with video files and subtitles, never assume 25 fps. Frame rate and dimensions are normally discovered via ffprobe.
- If an SRT contains large transcript-style blocks, the sidecar exporter must split them into smaller subtitle chunks and trim boundary text proportionally. The goal is to keep each bloack below 42 characters.
- The browser UI is intentionally dependency-light and uses the standard library HTTP server plus static HTML/CSS/JS.
- `transcribe` extracts audio as **16 kHz mono** (what Whisper expects) before sending it on;
  this also minimises the network payload. Probe input with ffprobe; never assume a format.
- Call the transcription service as an **OpenAI audio endpoint**: pass `model=TRANSCRIBE_MODEL`, `response_format=srt`
  for subtitles (or `verbose_json` when segment detail is needed), `language` optional. Treat it as
  untrusted-network: send the bearer token and set generous timeouts. The service loads models on
  demand and unloads them after an idle timeout (to free VRAM for Ollama/Plex on the same card), so
  the **first call after idle is slow** and it may return **HTTP 429** while busy/loading — retry
  with backoff and surface "warming up"/"busy" to the user rather than failing the job. Handle
  unreachable without hanging.
- The service returns **standard** whisper SRT (and speaker-labelled SRT if diarization is ever
  requested). The collection's ≤42-characters-per-line subtitle rule is applied **here**, in this
  tab's SRT post-processing — not in the service.

### Two patterns for services

**Job-queue services** (spotify_dl, yt_bulk_dl): User submits a form → job created with UUID → redirect to status page → HTMX polls `/convert/{job_id}/fragment` every 3s. Polling stops automatically because the fragment omits the `hx-trigger` attribute once `job.status` is `done` or `error`. Jobs run in a `ThreadPoolExecutor`; individual downloads use a nested executor that acquires a global semaphore (`max_workers_global`) before starting.

**Synchronous service** (edl-to-archive): No job queue. Upload → convert → stream XLSX response directly. Session state (exclusion rules, fps, etc.) is persisted as JSON files keyed by a UUID cookie.

### Adding a new service

- Package lives at `services/transcribe/transcribe/`, added to `[tool.uv.workspace].members`.
- Reuse the job-queue scaffolding from `spotify_dl` verbatim where possible: in-memory job
  store, `ThreadPoolExecutor`, the `pipeline(cb)` → `job.on_event` callback pattern, and the
  HTMX status fragment that returns **HTTP 286** when `job.status in ("done","error")` to stop
  polling. (AGENTS.md elsewhere describes "omit hx-trigger"; the current code uses 286 — follow
  the code.)
- Its own `templates/transcribe/` content templates that extend the shared base layout from
  `services/media_tools_ui/templates/base.html`.
- The browser side stays dependency-light: a file input, a progress view, two download links.

### Pipeline → job runner callback pattern

Pipelines (`core/pipeline.py`) receive a `cb: Callable[[dict], None]` argument. They call `cb({"type": "...", ...})` to emit typed events. The job runner (`job_runner.py`) translates these events into mutations on the in-memory `Job` object. New pipeline events must be handled in both `pipeline.py` (emit) and `job_runner.py` (handle).

### Template structure

Each service keeps its own `templates/<service_name>/` content templates, and the gateway keeps its own page templates, but the shared site layout now lives in `services/media_tools_ui/templates/base.html`. Shared CSS and site-wide logos live in `services/media_tools_ui/static/`. Build Jinja environments through `media_tools_ui.create_templates(local_templates_dir)` so local templates resolve first and shared UI templates resolve second, and mount `/static` from `media_tools_ui.get_static_dir()` in the gateway. If you need to change the global header, footer, shared nav, CSS, or shared logos, update the files in `media_tools_ui` rather than reintroducing per-service copies. Starlette ≥1.0 `TemplateResponse` signature: `TemplateResponse(request, "name.html", context_dict)` — do **not** pass `{"request": request, ...}` as the context.

### Runtime settings

spotify_dl and yt_bulk_dl expose `settings.py` with `max_workers_per_job` and `max_workers_global`. These are in-memory only and reset on restart. The admin panel at `/admin/` (HTTP Basic Auth, password via `ADMIN_PASSWORD` env var) can update them at runtime.

### Cleanup

Both job-queue services register a lifespan coroutine that deletes output directories older than 2 hours, running every 30 minutes. Uploaded files in edl-to-archive are deleted in a `try/finally` block immediately after conversion.

`transcribe` must be aggressive about disk because uploads are large and the server storage is small (128 GB):

- Delete the **original upload immediately** after audio extraction (`try/finally`).
- Delete the extracted audio once the transcription call to Speaches has returned (success or
  failure) — the audio is sent in the request body, so it's no longer needed afterwards.
- Keep only the small transcript outputs, under the same periodic-cleanup lifespan coroutine the
  other job-queue services use. Pick a TTL (the existing services use 30 min of inactivity).
- Write working files under the data dir (`MEDIA_TOOLS_DATA` / `~/data`), not `/tmp`.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `ADMIN_PASSWORD` | `"admin"` | HTTP Basic Auth for `/admin/` |
| `MEDIA_TOOLS_DATA` | `~/.media-tools` | Root dir for edl-to-archive session JSON files |
| `TRANSCRIBE_SERVICE_URL` | _(unset)_ | Base URL of the WhisperX transcription service (OpenAI-style `/v1`) |
| `TRANSCRIBE_API_TOKEN`   | _(unset)_ | Bearer token / API key sent to the transcription service          |
| `TRANSCRIBE_MODEL`       | _(unset)_ | Whisper model id the service should use (e.g. a `large-v3-turbo` CT2 model) |
