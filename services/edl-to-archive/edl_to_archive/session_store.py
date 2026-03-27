"""Cookie-based session store for persistent user settings (exclusion rules).

Session ID is stored in a browser cookie (1-year expiry).
Settings are persisted as JSON files in DATA_DIR/edl_sessions/.

Set the MEDIA_TOOLS_DATA environment variable to choose where data is stored.
Defaults to ~/.media-tools/
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from fastapi import Request, Response

_DATA_DIR = Path(os.environ.get("MEDIA_TOOLS_DATA", Path.home() / ".media-tools")) / "edl_sessions"

_COOKIE_NAME = "edl_session"
_COOKIE_MAX_AGE = 365 * 24 * 3600  # 1 year

DEFAULT_EXCLUSION_RULES = """\
# Exclude MXF SYNC files
Bestandsnaam IS "" AND Name INCLUDES ".MXF.SYNC"

# Exclude MP4 SYNC files
Bestandsnaam IS "" AND Name INCLUDES ".MP4.SYNC"
"""


@dataclass
class UserSession:
    session_id: str
    exclusion_rules: str = DEFAULT_EXCLUSION_RULES
    fps: int = 25
    collapse: bool = True
    include_frames: bool = False


def _session_path(session_id: str) -> Path:
    return _DATA_DIR / f"{session_id}.json"


def _load(session_id: str) -> UserSession | None:
    path = _session_path(session_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return UserSession(**{k: v for k, v in data.items() if k in UserSession.__dataclass_fields__})
    except Exception:
        return None


def save_session(session: UserSession) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _session_path(session.session_id).write_text(
        json.dumps(asdict(session), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_or_create_session(request: Request) -> tuple[UserSession, bool]:
    """Return (session, is_new). is_new=True means a cookie must be set."""
    raw_id = request.cookies.get(_COOKIE_NAME, "").strip()
    if raw_id:
        session = _load(raw_id)
        if session is not None:
            return session, False
    new_id = uuid.uuid4().hex
    return UserSession(session_id=new_id), True


def attach_session_cookie(response: Response, session: UserSession) -> None:
    response.set_cookie(
        _COOKIE_NAME,
        session.session_id,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
