"""FastAPI router for the EDL to Archive service."""

from __future__ import annotations

import asyncio
import io
import shutil
import tempfile
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.responses import StreamingResponse

from edl_to_archive.core.pipeline import ConversionError, run_conversion
from edl_to_archive.session_store import (
    UserSession,
    attach_session_cookie,
    get_or_create_session,
    save_session,
)

router = APIRouter()

# Template column headers (for template downloads)
_EDL_COLUMNS = [
    "ID", "Reel", "Name", "File Name", "Track",
    "Timecode In", "Timecode Out", "Duration",
    "Source Start", "Source End", "Audio Channels", "Comment",
]
_SOURCE_COLUMNS = [
    "TC in", "Duur", "Bestandsnaam", "Omschrijving", "Link",
    "Bron", "kosten", "rechten / contact", "to do",
    "Bron in beeld", "Aftiteling",
]


def _templates(request: Request):
    return request.app.state.templates


def _form_response(request: Request, session: UserSession, is_new: bool,
                   error: str | None = None, success_stats: dict | None = None):
    resp = _templates(request).TemplateResponse(
        request, "edl_to_archive/form.html",
        {"session": session, "error": error, "success_stats": success_stats},
    )
    if is_new:
        attach_session_cookie(resp, session)
    return resp


@router.get("/")
async def form(request: Request):
    session, is_new = get_or_create_session(request)
    return _form_response(request, session, is_new)


@router.post("/convert")
async def convert_edl(
    request: Request,
    edl_file: UploadFile = File(...),
    source_file: UploadFile = File(...),
    fps: int = Form(25),
    collapse: str | None = Form(None),        # checkbox — present="on" / absent=None
    include_frames: str | None = Form(None),  # checkbox
    exclusion_rules: str = Form(""),
):
    session, is_new = get_or_create_session(request)

    # Persist updated session settings
    session.exclusion_rules = exclusion_rules
    session.fps = fps
    session.collapse = collapse is not None
    session.include_frames = include_frames is not None
    save_session(session)

    # Read uploads into memory
    edl_bytes = await edl_file.read()
    source_bytes = await source_file.read()

    if not edl_bytes:
        return _form_response(request, session, is_new, error="EDL file is empty.")
    if not source_bytes:
        return _form_response(request, session, is_new, error="Source file is empty.")

    # Determine safe filenames (preserve extension for format detection)
    edl_name = edl_file.filename or "edl.xlsx"
    source_name = source_file.filename or "source.xlsx"

    tmp_dir: Path | None = None

    def _process() -> tuple[bytes, dict]:
        nonlocal tmp_dir
        tmp_dir = Path(tempfile.mkdtemp(prefix="edl_"))
        edl_path = tmp_dir / edl_name
        source_path = tmp_dir / source_name
        output_path = tmp_dir / "DEF.xlsx"

        edl_path.write_bytes(edl_bytes)
        source_path.write_bytes(source_bytes)

        result = run_conversion(
            edl_path=edl_path,
            source_path=source_path,
            output_path=output_path,
            fps=fps,
            collapse=session.collapse,
            include_frames=session.include_frames,
            exclusion_rules_text=exclusion_rules,
        )
        xlsx_bytes = output_path.read_bytes()
        return xlsx_bytes, {
            "edl_count": result.edl_count,
            "source_count": result.source_count,
            "excluded_count": result.excluded_count,
            "collapsed_count": result.collapsed_count,
            "def_count": result.def_count,
            "matched_count": result.matched_count,
        }

    try:
        loop = asyncio.get_event_loop()
        xlsx_bytes, stats = await loop.run_in_executor(None, _process)
    except ConversionError as e:
        return _form_response(request, session, is_new, error=str(e))
    except Exception as e:
        return _form_response(request, session, is_new,
                              error=f"Unexpected error during conversion: {e}")
    finally:
        if tmp_dir and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)

    resp = StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="DEF.xlsx"'},
    )
    attach_session_cookie(resp, session)
    return resp


@router.get("/template/{kind}")
async def download_template(kind: str):
    """Serve a blank EDL or SOURCE template xlsx."""
    if kind == "edl":
        columns, filename = _EDL_COLUMNS, "EDL_template.xlsx"
    elif kind == "source":
        columns, filename = _SOURCE_COLUMNS, "SOURCE_template.xlsx"
    else:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "Unknown template."}, status_code=404)

    buf = io.BytesIO()
    df = pd.DataFrame(columns=columns)
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
