from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from gateway.admin import router as admin_router

BASE_DIR = Path(__file__).parent

app = FastAPI(title="Media Tools")

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

app.include_router(admin_router)

# Mount microservices
try:
    from green_to_red.main import app as green_to_red_app
    app.mount("/green-to-red", green_to_red_app)
except ImportError:
    pass

try:
    from yt_bulk_dl.main import app as yt_bulk_dl_app
    app.mount("/yt-bulk-dl", yt_bulk_dl_app)
except ImportError:
    pass

try:
    from edl_to_archive.main import app as edl_to_archive_app
    app.mount("/edl-to-archive", edl_to_archive_app)
except ImportError:
    pass


@app.get("/")
async def homepage(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})
