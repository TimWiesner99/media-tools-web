"""Authentication middleware.

Sits inside the gateway's ASGI stack and intercepts ALL requests — including
those routed to mounted sub-apps (green-to-red, yt-bulk-dl, edl-to-archive).

Starlette's middleware stack wraps the entire app tree, so this middleware
executes for every request regardless of path prefix.

Middleware ordering in main.py (add_middleware is LIFO — last added = outermost):
    app.add_middleware(SessionMiddleware, ...)   # added 1st → outermost → parses cookie
    app.add_middleware(AuthMiddleware)           # added 2nd → inner → reads session

This guarantees request.session is populated before AuthMiddleware runs.

Additionally, this middleware injects an X-User-Id header into the request scope
so that mounted sub-apps can identify the current user without touching the
gateway's session directly.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse

# Paths accessible without authentication
_PUBLIC_PATHS = {"/login", "/logout", "/health"}
_PUBLIC_PREFIXES = ("/static",)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Allow public paths
        if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        # Check session for an authenticated user
        user_id = request.session.get("user_id")
        if user_id is None:
            # Preserve the intended destination for post-login redirect
            safe_next = path if path.startswith("/") else "/"
            return RedirectResponse(url=f"/login?next={safe_next}", status_code=302)

        # Load user from DB
        from gateway.auth.db import User, get_db

        with get_db() as db:
            user = db.get(User, user_id)

        if user is None:
            # Session references a deleted user — clear and redirect
            request.session.clear()
            return RedirectResponse(url="/login", status_code=302)

        # Attach user to request state for downstream gateway routes
        request.state.user = user

        # Inject user headers so mounted sub-apps can identify the user
        # without sharing the gateway's session middleware
        scope = request.scope
        headers = list(scope.get("headers", []))
        headers.append((b"x-user-id", str(user.id).encode()))
        headers.append((b"x-user-role", user.role.encode()))
        scope["headers"] = headers

        return await call_next(request)
