"""FastAPI sub-application for the green-to-red (Spotify → MP3) service."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from green_to_red.job_runner import cleanup_old_jobs
from green_to_red.router import router

# ── Logging setup ───────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s  %(name)-18s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
# Suppress noisy yt-dlp / ffmpeg / urllib3 output
logging.getLogger("yt_dlp").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

BASE_DIR = Path(__file__).parent


async def _periodic_cleanup():
    while True:
        await asyncio.sleep(30 * 60)  # every 30 minutes
        await cleanup_old_jobs(max_age_hours=2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_periodic_cleanup())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Spotify → MP3", lifespan=lifespan)
app.state.templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Custom Jinja2 filter for formatting activity log timestamps
from datetime import datetime as _dt, timezone as _tz
app.state.templates.env.filters["logtime"] = lambda ts: _dt.fromtimestamp(ts, tz=_tz.utc).strftime("%H:%M:%S")

app.include_router(router)
