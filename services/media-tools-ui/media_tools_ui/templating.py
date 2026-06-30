"""Shared Jinja template setup for Media Tools services."""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

_PACKAGE_DIR = Path(__file__).parent
_SHARED_TEMPLATES_DIR = _PACKAGE_DIR / "templates"
_SHARED_STATIC_DIR = _PACKAGE_DIR / "static"


def _layout_context(request: Request) -> dict[str, dict[str, bool]]:
    """Expose normalized auth flags for the shared site layout."""
    role = request.headers.get("x-user-role")
    return {
        "layout": {
            "is_authenticated": bool(request.headers.get("x-user-id")),
            "is_admin": role == "admin",
        }
    }


def create_templates(local_templates_dir: Path) -> Jinja2Templates:
    """Create a Jinja environment with local templates first and shared layout templates second."""
    return Jinja2Templates(
        directory=[local_templates_dir, _SHARED_TEMPLATES_DIR],
        context_processors=[_layout_context],
    )


def get_static_dir() -> Path:
    """Return the shared static asset directory for site-wide CSS and logos."""
    return _SHARED_STATIC_DIR