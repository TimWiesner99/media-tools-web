"""YT Bulk Download — FastAPI sub-app."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from yt_bulk_dl.job_runner import cleanup_old_jobs
from yt_bulk_dl.router import router

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


def create_app() -> FastAPI:
    app = FastAPI(title="YT Bulk Download", lifespan=lifespan)
    templates = Jinja2Templates(directory=BASE_DIR / "templates")
    app.state.templates = templates
    app.include_router(router)
    return app


app = create_app()
