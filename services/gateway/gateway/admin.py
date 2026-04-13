"""Admin panel — protected by session-based auth (role: admin).

The HTTP Basic Auth from the original implementation has been replaced by
the session auth system. The _require_admin() helper reads request.state.user
which is set by AuthMiddleware for every authenticated request.
"""

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

router = APIRouter(prefix="/admin")


def _require_admin(request: Request) -> None:
    user = getattr(request.state, "user", None)
    if user is None or user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")


def _get_all_settings():
    settings = {}
    try:
        from green_to_red.settings import get_settings as g2r_settings
        settings["green_to_red"] = g2r_settings()
    except ImportError:
        pass
    try:
        from yt_bulk_dl.settings import get_settings as ytdl_settings
        settings["yt_bulk_dl"] = ytdl_settings()
    except ImportError:
        pass
    try:
        from edl_to_archive.settings import get_settings as edl_settings
        settings["edl_to_archive"] = edl_settings()
    except ImportError:
        pass
    return settings


@router.get("/")
async def admin_page(request: Request):
    _require_admin(request)
    return templates.TemplateResponse(
        request, "admin/index.html", _admin_context(request),
    )


def _admin_context(request: Request, **extra):
    from gateway.auth.db import User, get_db
    with get_db() as db:
        users = db.query(User).order_by(User.created_at).all()
    return {"settings": _get_all_settings(), "user": request.state.user, "users": users, **extra}


@router.post("/settings/green-to-red")
async def update_green_to_red_settings(
    request: Request,
    max_workers_per_job: int = Form(...),
    max_workers_global: int = Form(...),
):
    _require_admin(request)
    from green_to_red.settings import update_settings
    update_settings(
        max_workers_per_job=max(1, min(20, max_workers_per_job)),
        max_workers_global=max(1, min(100, max_workers_global)),
    )
    return templates.TemplateResponse(
        request, "admin/index.html", _admin_context(request, saved="green_to_red"),
    )


@router.post("/settings/yt-bulk-dl")
async def update_yt_bulk_dl_settings(
    request: Request,
    max_workers_per_job: int = Form(...),
    max_workers_global: int = Form(...),
    max_zip_size_mb: int = Form(...),
):
    _require_admin(request)
    from yt_bulk_dl.settings import update_settings
    update_settings(
        max_workers_per_job=max(1, min(20, max_workers_per_job)),
        max_workers_global=max(1, min(100, max_workers_global)),
        max_zip_size_mb=max(100, min(10240, max_zip_size_mb)),
    )
    return templates.TemplateResponse(
        request, "admin/index.html", _admin_context(request, saved="yt_bulk_dl"),
    )


@router.post("/settings/edl-to-archive")
async def update_edl_to_archive_settings(
    request: Request,
    min_match_length: int = Form(...),
):
    _require_admin(request)
    from edl_to_archive.settings import update_settings
    update_settings(
        min_match_length=max(2, min(20, min_match_length)),
    )
    return templates.TemplateResponse(
        request, "admin/index.html", _admin_context(request, saved="edl_to_archive"),
    )
