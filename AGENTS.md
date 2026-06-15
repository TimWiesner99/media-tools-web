# AGENTS.md

This file provides guidance to AI agents like Claude Code (claude.ai/code) or GPT when working with code in this repository.

## Hardware and Performance Requirements

This project is a prototype of software that will eventually run on a company-internal server. Heavy AI-services like LLMs will be provided externally. This program must be able to interface with these external services, but internal environments can be spun up for testing and prototyping. The users must be able to run everything on office-grade laptops from their browser, so the heavy-lifting must be done by the server. Keep scalability in mind, but don't worry about throusands of concurrent users. Realistically, this service would be accessed by no more than 50 concurrent users.

The code is being tested on a Dell laptop without dedicated graphics card.

The current production server is an LXC container running on a Proxmox server with 4GB of DDR3 memory and 128GB of mounted storage at `~/data`. A second dedicated LXC for transcription only is running on a second Proxmox with a dedicated GPU (a GTX 1650 with 4GB of VRAM).

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
uv run --package green-to-red uvicorn green_to_red.main:app --reload --port 8001

# In production/on a remote server, run the webserver with
uv run --package gateway uvicorn gateway.main:app --host 0.0.0.0 --port 8000
```

There are no tests or linting configured in this project.

## Architecture

A `uv` workspace monorepo. The `gateway` service is the only entry point — it imports and mounts the other three services as ASGI sub-apps at `/green-to-red`, `/yt-bulk-dl`, and `/edl-to-archive`. Each sub-app mount is wrapped in `try/except ImportError` so the server starts even if a service package is missing.

Remeber these general rules:

- Prefer GPU-first detection paths, but fall back to CPU-based computation of no GPU is present.
- When working with video files and subtitles, never assume 25 fps. Frame rate and dimensions are normally discovered via ffprobe.
- If an SRT contains large transcript-style blocks, the sidecar exporter must split them into smaller subtitle chunks and trim boundary text proportionally. The goal is to keep each bloack below 42 characters.
- The browser UI is intentionally dependency-light and uses the standard library HTTP server plus static HTML/CSS/JS.

### Two patterns for services

**Job-queue services** (green-to-red, yt-bulk-dl): User submits a form → job created with UUID → redirect to status page → HTMX polls `/convert/{job_id}/fragment` every 3s. Polling stops automatically because the fragment omits the `hx-trigger` attribute once `job.status` is `done` or `error`. Jobs run in a `ThreadPoolExecutor`; individual downloads use a nested executor that acquires a global semaphore (`max_workers_global`) before starting.

**Synchronous service** (edl-to-archive): No job queue. Upload → convert → stream XLSX response directly. Session state (exclusion rules, fps, etc.) is persisted as JSON files keyed by a UUID cookie.

### Pipeline → job runner callback pattern

Pipelines (`core/pipeline.py`) receive a `cb: Callable[[dict], None]` argument. They call `cb({"type": "...", ...})` to emit typed events. The job runner (`job_runner.py`) translates these events into mutations on the in-memory `Job` object. New pipeline events must be handled in both `pipeline.py` (emit) and `job_runner.py` (handle).

### Template structure

Each service has its own `templates/<service_name>/` directory and its own `base.html` with identical nav and CDN links (Pico CSS + HTMX). They are not shared. The gateway has its own separate `base.html`. Starlette ≥1.0 `TemplateResponse` signature: `TemplateResponse(request, "name.html", context_dict)` — do **not** pass `{"request": request, ...}` as the context.

### Runtime settings

green-to-red and yt-bulk-dl expose `settings.py` with `max_workers_per_job` and `max_workers_global`. These are in-memory only and reset on restart. The admin panel at `/admin/` (HTTP Basic Auth, password via `ADMIN_PASSWORD` env var) can update them at runtime.

### Cleanup

Both job-queue services register a lifespan coroutine that deletes output directories older than 2 hours, running every 30 minutes. Uploaded files in edl-to-archive are deleted in a `try/finally` block immediately after conversion.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `ADMIN_PASSWORD` | `"admin"` | HTTP Basic Auth for `/admin/` |
| `MEDIA_TOOLS_DATA` | `~/.media-tools` | Root dir for edl-to-archive session JSON files |
