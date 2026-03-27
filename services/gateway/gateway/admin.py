"""Admin panel — protected by HTTP Basic Auth.

Password is read from the ADMIN_PASSWORD environment variable.
Default is "admin" — change it before exposing to the internet.

In a later phase this will be replaced by proper user accounts with roles.
"""

import os
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

router = APIRouter(prefix="/admin")
_security = HTTPBasic()


def _require_admin(credentials: HTTPBasicCredentials = Depends(_security)) -> None:
    admin_password = os.environ.get("ADMIN_PASSWORD", "admin")
    ok_user = secrets.compare_digest(credentials.username.encode(), b"admin")
    ok_pass = secrets.compare_digest(credentials.password.encode(), admin_password.encode())
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )


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
    return settings


@router.get("/", dependencies=[Depends(_require_admin)])
async def admin_page(request: Request):
    return templates.TemplateResponse(
        request, "admin/index.html", {"settings": _get_all_settings()}
    )


@router.post("/settings/green-to-red", dependencies=[Depends(_require_admin)])
async def update_green_to_red_settings(
    request: Request,
    max_workers_per_job: int = Form(...),
    max_workers_global: int = Form(...),
):
    from green_to_red.settings import update_settings
    update_settings(
        max_workers_per_job=max(1, min(20, max_workers_per_job)),
        max_workers_global=max(1, min(100, max_workers_global)),
    )
    return templates.TemplateResponse(
        request, "admin/index.html",
        {"settings": _get_all_settings(), "saved": "green_to_red"},
    )


@router.post("/settings/yt-bulk-dl", dependencies=[Depends(_require_admin)])
async def update_yt_bulk_dl_settings(
    request: Request,
    max_workers_per_job: int = Form(...),
    max_workers_global: int = Form(...),
):
    from yt_bulk_dl.settings import update_settings
    update_settings(
        max_workers_per_job=max(1, min(20, max_workers_per_job)),
        max_workers_global=max(1, min(100, max_workers_global)),
    )
    return templates.TemplateResponse(
        request, "admin/index.html",
        {"settings": _get_all_settings(), "saved": "yt_bulk_dl"},
    )
