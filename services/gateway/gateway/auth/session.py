"""Session helpers.

The session cookie is managed by Starlette's SessionMiddleware (signed via
itsdangerous). This module provides a thin, typed wrapper so the rest of the
code never scatters raw dict key names.
"""

from __future__ import annotations

from fastapi import Request

from gateway.auth.db import User, get_db

_SESSION_KEY = "user_id"


def set_session_user(request: Request, user_id: int) -> None:
    """Store the authenticated user's ID in the session cookie."""
    request.session[_SESSION_KEY] = user_id


def clear_session(request: Request) -> None:
    """Destroy the current session (logout)."""
    request.session.clear()


def get_current_user(request: Request) -> User | None:
    """Look up the User for the current session, or None if not authenticated."""
    user_id = request.session.get(_SESSION_KEY)
    if user_id is None:
        return None
    with get_db() as db:
        return db.get(User, user_id)
