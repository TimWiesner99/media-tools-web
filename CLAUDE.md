# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
