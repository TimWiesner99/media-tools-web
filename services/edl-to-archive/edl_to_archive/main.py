"""EDL to Archive — FastAPI sub-app."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from edl_to_archive.router import router

BASE_DIR = Path(__file__).parent


def create_app() -> FastAPI:
    app = FastAPI(title="EDL to Archive")
    templates = Jinja2Templates(directory=BASE_DIR / "templates")
    app.state.templates = templates
    app.include_router(router)
    return app


app = create_app()
