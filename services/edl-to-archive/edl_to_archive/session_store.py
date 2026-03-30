"""Per-user settings store for the EDL to Archive service.

Settings are keyed by user ID (from the X-User-Id header injected by the
gateway's AuthMiddleware). This replaces the previous cookie-based session
approach, so settings now persist across logins and browser sessions.

Set the MEDIA_TOOLS_DATA environment variable to choose where data is stored.
Defaults to ~/.media-tools/
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from fastapi import Request

_DATA_DIR = Path(os.environ.get("MEDIA_TOOLS_DATA", Path.home() / ".media-tools")) / "edl_sessions"

DEFAULT_EXCLUSION_RULES = """\
# Exclude MXF SYNC files
Bestandsnaam IS "" AND Name INCLUDES ".MXF.SYNC"

# Exclude MP4 SYNC files
Bestandsnaam IS "" AND Name INCLUDES ".MP4.SYNC"
"""


@dataclass
class UserSession:
    user_id: str
    exclusion_rules: str = DEFAULT_EXCLUSION_RULES
    fps: int = 25
    collapse: bool = True
    include_frames: bool = False


def _session_path(user_id: str) -> Path:
    return _DATA_DIR / f"user_{user_id}.json"


def _load(user_id: str) -> UserSession | None:
    path = _session_path(user_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        fields = UserSession.__dataclass_fields__
        return UserSession(**{k: v for k, v in data.items() if k in fields})
    except Exception:
        return None


def save_session(session: UserSession) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _session_path(session.user_id).write_text(
        json.dumps(asdict(session), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_or_create_session(request: Request) -> UserSession:
    """Return the UserSession for the current user (from X-User-Id header).

    Falls back to a transient session keyed "anonymous" if no user ID is present.
    Settings for anonymous users are not persisted across requests.
    """
    user_id = request.headers.get("x-user-id", "anonymous")
    session = _load(user_id)
    if session is not None:
        return session
    return UserSession(user_id=user_id)
