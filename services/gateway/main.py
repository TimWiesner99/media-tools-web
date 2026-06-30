from contextlib import asynccontextmanager
import hashlib
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env before any other imports that might read env vars
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from media_tools_ui import create_templates, get_static_dir

from admin import router as admin_router
from auth.middleware import AuthMiddleware
from auth.router import router as auth_router

BASE_DIR = Path(__file__).parent

# Required — hard fail on startup if not set so it's never accidentally omitted
_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY")
if not _SECRET_KEY:
    raise RuntimeError(
        "SESSION_SECRET_KEY environment variable is not set. "
        "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
    )

_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")


def _ensure_admin_user(plain_password: str) -> None:
    """Create the permanent admin user on startup if it does not exist.

    The SHA-256 step mirrors exactly what the browser sends during login,
    so this account works with the standard login form without any special casing.
    """
    from auth.crypto import hash_password
    from auth.db import User, get_db

    sha256_hex = hashlib.sha256(plain_password.encode("utf-8")).hexdigest()
    with get_db() as db:
        existing = db.query(User).filter(User.username == "admin").first()
        if existing is None:
            admin = User(
                username="admin",
                hashed_pw=hash_password(sha256_hex),
                role="admin",
                auth_provider="local",
                is_permanent=True,
            )
            db.add(admin)
            db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from auth.db import init_db
    init_db()
    _ensure_admin_user(_ADMIN_PASSWORD)
    yield


app = FastAPI(title="Media Tools", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=get_static_dir()), name="static")
templates = create_templates(BASE_DIR / "templates")

# Middleware — add_middleware is LIFO (last added = outermost wrapper).
# AuthMiddleware added first → inner → request.session already populated when it runs.
# SessionMiddleware added second → outermost → parses the session cookie first.
app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=_SECRET_KEY,
    same_site="strict",
    https_only=False,  # Set to True in production behind HTTPS
    max_age=14 * 24 * 60 * 60,  # 14 days
)

# Routers
app.include_router(auth_router)
app.include_router(admin_router)

# Mount microservices (graceful degradation if a service is unavailable)
try:
    from spotify_dl.main import app as spotify_dl_app
    app.mount("/spotify-dl", spotify_dl_app)
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
    user = getattr(request.state, "user", None)
    return templates.TemplateResponse(request, "index.html", {"user": user})


@app.api_route("/green-to-red", methods=["GET", "POST"], include_in_schema=False)
@app.api_route("/green-to-red/{path:path}", methods=["GET", "POST"], include_in_schema=False)
async def legacy_spotify_redirect(request: Request, path: str = ""):
    suffix = f"/{path}" if path else "/"
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(f"/spotify-dl{suffix}{query}", status_code=307)


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})
