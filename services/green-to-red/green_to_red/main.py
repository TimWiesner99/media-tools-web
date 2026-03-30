"""FastAPI sub-application for the green-to-red (Spotify → MP3) service."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from green_to_red.job_runner import cleanup_old_jobs
from green_to_red.router import router

BASE_DIR = Path(__file__).parent


async def _periodic_cleanup():
    while True:
        await asyncio.sleep(30 * 60)  # every 30 minutes
        await cleanup_old_jobs(max_age_minutes=30)


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
app.include_router(router)
