"""Auth routes: login, logout, account, and admin user management."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from gateway.auth.crypto import hash_password, verify_password
from gateway.auth.db import User, get_db
from gateway.auth.session import clear_session, get_current_user, set_session_user

BASE_DIR = Path(__file__).parent.parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

router = APIRouter()


# ── Login ──────────────────────────────────────────────────────────────────

@router.get("/login")
async def login_page(request: Request):
    # Already logged in → redirect to home
    if request.session.get("user_id"):
        return RedirectResponse("/", 302)
    next_url = request.query_params.get("next", "/")
    return templates.TemplateResponse(request, "auth/login.html", {"next": next_url})


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password_hash: str = Form(...),  # SHA-256 hex from browser
    next_url: str = Form(default="/"),
):
    with get_db() as db:
        user = (
            db.query(User)
            .filter(User.username == username, User.auth_provider == "local")
            .first()
        )

    if user and user.hashed_pw and verify_password(password_hash, user.hashed_pw):
        # Clear old session data first to prevent session fixation
        request.session.clear()
        set_session_user(request, user.id)
        # Validate next_url to prevent open redirect
        safe_next = next_url if (next_url.startswith("/") and not next_url.startswith("//")) else "/"
        return RedirectResponse(safe_next, status_code=303)

    return templates.TemplateResponse(
        request,
        "auth/login.html",
        {"error": "Invalid username or password.", "next": next_url},
        status_code=401,
    )


# ── Logout ─────────────────────────────────────────────────────────────────

@router.post("/logout")
async def logout(request: Request):
    user_id = request.session.get("user_id")
    if user_id is not None:
        # Clean up job data for this user before clearing the session
        _cleanup_user_jobs(str(user_id))
    clear_session(request)
    return RedirectResponse("/login", status_code=303)


def _cleanup_user_jobs(user_id: str) -> None:
    """Delete all in-progress job data for a user on logout."""
    try:
        from green_to_red.job_runner import cleanup_jobs_for_user as g2r_cleanup
        g2r_cleanup(user_id)
    except ImportError:
        pass
    try:
        from yt_bulk_dl.job_runner import cleanup_jobs_for_user as ytdl_cleanup
        ytdl_cleanup(user_id)
    except ImportError:
        pass


# ── Account (change own password) ──────────────────────────────────────────

@router.get("/account")
async def account_page(request: Request):
    user = get_current_user(request)
    if user is None:
        return RedirectResponse("/login", 302)
    return templates.TemplateResponse(request, "auth/account.html", {"user": user})


@router.post("/account/password")
async def change_password(
    request: Request,
    current_hash: str = Form(...),
    new_hash: str = Form(...),
    confirm_hash: str = Form(...),
):
    user = get_current_user(request)
    if user is None:
        return RedirectResponse("/login", 302)

    # Only local accounts can change their password
    if user.auth_provider != "local":
        return templates.TemplateResponse(
            request,
            "auth/account.html",
            {"user": user, "error": "Password change is not available for SSO accounts."},
            status_code=400,
        )

    if new_hash != confirm_hash:
        return templates.TemplateResponse(
            request,
            "auth/account.html",
            {"user": user, "error": "New password and confirmation do not match."},
            status_code=400,
        )

    if not verify_password(current_hash, user.hashed_pw):
        return templates.TemplateResponse(
            request,
            "auth/account.html",
            {"user": user, "error": "Current password is incorrect."},
            status_code=400,
        )

    with get_db() as db:
        db_user = db.get(User, user.id)
        db_user.hashed_pw = hash_password(new_hash)
        db.commit()

    return templates.TemplateResponse(
        request, "auth/account.html", {"user": user, "saved": True}
    )


# ── Admin: User Management ─────────────────────────────────────────────────

def _require_admin(request: Request) -> None:
    user = getattr(request.state, "user", None)
    if user is None or user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")


@router.get("/admin/users")
async def admin_users(request: Request):
    _require_admin(request)
    with get_db() as db:
        users = db.query(User).order_by(User.created_at).all()
    return templates.TemplateResponse(
        request, "admin/users.html", {"users": users, "user": request.state.user}
    )


@router.post("/admin/users/create")
async def admin_create_user(
    request: Request,
    username: str = Form(...),
    password_hash: str = Form(...),  # SHA-256 hex from browser
    role: str = Form(default="user"),
):
    _require_admin(request)

    if role not in ("user", "admin"):
        role = "user"

    # Validate username
    username = username.strip()
    if not username:
        from gateway.admin import _admin_context, _get_all_settings
        return templates.TemplateResponse(
            request,
            "admin/index.html",
            {**_admin_context(request), "error": "Username cannot be empty."},
            status_code=400,
        )

    with get_db() as db:
        existing = db.query(User).filter(User.username == username).first()
        if existing:
            from gateway.admin import _admin_context
            return templates.TemplateResponse(
                request,
                "admin/index.html",
                {**_admin_context(request), "error": f"Username '{username}' is already taken."},
                status_code=400,
            )

        new_user = User(
            username=username,
            hashed_pw=hash_password(password_hash),
            role=role,
            auth_provider="local",
            is_permanent=False,
        )
        db.add(new_user)
        db.commit()

    return RedirectResponse("/admin/", status_code=303)


@router.post("/admin/users/{user_id}/delete")
async def admin_delete_user(request: Request, user_id: int):
    _require_admin(request)

    with get_db() as db:
        user = db.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found.")
        if user.is_permanent:
            raise HTTPException(status_code=400, detail="Cannot delete permanent users.")
        db.delete(user)
        db.commit()

    return RedirectResponse("/admin/", status_code=303)
