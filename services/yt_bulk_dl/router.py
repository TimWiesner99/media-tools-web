"""FastAPI router for the yt-bulk-dl service."""

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from yt_bulk_dl.core.downloader import parse_urls
from yt_bulk_dl.job_runner import (
    create_job,
    get_active_job_for_user,
    get_file_path,
    get_job,
    get_zip_parts,
    launch_job,
    touch_job,
)

router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


def _user_id(request: Request) -> str:
    """Read the X-User-Id header injected by the gateway AuthMiddleware."""
    return request.headers.get("x-user-id", "anonymous")


@router.get("/")
async def form(request: Request):
    user_id = _user_id(request)
    # Redirect to active job if one exists
    active = get_active_job_for_user(user_id)
    if active is not None:
        return RedirectResponse(
            url=request.url_for("ytdl_job_page", job_id=active.job_id), status_code=302
        )
    return _templates(request).TemplateResponse(request, "yt_bulk_dl/form.html")


@router.post("/convert")
async def start_convert(
    request: Request,
    urls_text: str = Form(...),
    prefix: str = Form(""),
    max_length: int = Form(40),
):
    urls = parse_urls(urls_text)
    if not urls:
        return _templates(request).TemplateResponse(
            request, "yt_bulk_dl/form.html",
            {"error": "No URLs found. Enter one YouTube URL per line.",
             "urls_text": urls_text, "prefix": prefix, "max_length": max_length},
            status_code=422,
        )

    prefix_val = prefix.strip() or None
    max_length = max(10, min(100, max_length))
    user_id = _user_id(request)

    job = create_job(urls, prefix=prefix_val, max_length=max_length, user_id=user_id)
    await launch_job(job.job_id, urls)
    return RedirectResponse(
        url=request.url_for("ytdl_job_page", job_id=job.job_id).path, status_code=303
    )


@router.get("/convert/{job_id}", name="ytdl_job_page")
async def job_page(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        return _templates(request).TemplateResponse(
            request, "yt_bulk_dl/form.html",
            {"error": "Job not found. It may have expired."},
            status_code=404,
        )
    fragment_url = request.url_for("ytdl_job_fragment", job_id=job_id).path
    download_url = request.url_for("ytdl_job_download", job_id=job_id).path
    file_download_base = request.url_for(
        "ytdl_job_download_file", job_id=job_id, filename="__PH__"
    ).path.replace("__PH__", "")

    zip_parts = []
    if job.status == "done":
        zip_parts = get_zip_parts(job)

    return _templates(request).TemplateResponse(
        request, "yt_bulk_dl/job_status.html",
        {
            "job": job,
            "fragment_url": fragment_url,
            "download_url": download_url,
            "file_download_base": file_download_base,
            "zip_parts": zip_parts,
        },
    )


@router.get("/convert/{job_id}/fragment", name="ytdl_job_fragment")
async def job_fragment(request: Request, job_id: str):
    """HTML partial for HTMX polling. Touching the job resets the 30-min timer."""
    job = get_job(job_id)
    if job is None:
        return JSONResponse({"error": "Job not found."}, status_code=404)
    touch_job(job_id)
    fragment_url = request.url_for("ytdl_job_fragment", job_id=job_id).path
    download_url = request.url_for("ytdl_job_download", job_id=job_id).path
    file_download_base = request.url_for(
        "ytdl_job_download_file", job_id=job_id, filename="__PH__"
    ).path.replace("__PH__", "")

    zip_parts = []
    if job.status == "done":
        zip_parts = get_zip_parts(job)

    # HTTP 286 tells HTMX to swap the content AND stop polling
    status_code = 286 if job.status in ("done", "error") else 200
    return _templates(request).TemplateResponse(
        request, "yt_bulk_dl/_status_fragment.html",
        {
            "job": job,
            "fragment_url": fragment_url,
            "download_url": download_url,
            "file_download_base": file_download_base,
            "zip_parts": zip_parts,
        },
        status_code=status_code,
    )


def _stream_file(path: Path, chunk_size: int = 65536):
    """Generator that streams a file from disk in chunks."""
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            yield chunk


def _content_type(path: Path) -> str:
    ext = path.suffix.lower()
    return {".mp4": "video/mp4", ".srt": "text/plain", ".csv": "text/csv",
            ".zip": "application/zip"}.get(ext, "application/octet-stream")


@router.get("/convert/{job_id}/download", name="ytdl_job_download")
async def job_download(job_id: str):
    job = get_job(job_id)
    if job is None or job.status != "done":
        return JSONResponse({"error": "Job not ready."}, status_code=404)
    parts = get_zip_parts(job)
    if len(parts) == 1:
        part = parts[0]
        return StreamingResponse(
            _stream_file(part.path),
            media_type=_content_type(part.path),
            headers={"Content-Disposition": f'attachment; filename="{part.filename}"'},
        )
    return JSONResponse(
        {"error": "Multiple parts available. Use /download/{part} endpoints."},
        status_code=400,
    )


@router.get("/convert/{job_id}/download/{part}", name="ytdl_job_download_part")
async def job_download_part(job_id: str, part: int):
    job = get_job(job_id)
    if job is None or job.status != "done":
        return JSONResponse({"error": "Job not ready."}, status_code=404)
    parts = get_zip_parts(job)
    for p in parts:
        if p.part_number == part:
            return StreamingResponse(
                _stream_file(p.path),
                media_type=_content_type(p.path),
                headers={"Content-Disposition": f'attachment; filename="{p.filename}"'},
            )
    return JSONResponse({"error": "Part not found."}, status_code=404)


@router.get("/convert/{job_id}/download/file/{filename}", name="ytdl_job_download_file")
async def job_download_file(job_id: str, filename: str):
    job = get_job(job_id)
    if job is None:
        return JSONResponse({"error": "Job not found."}, status_code=404)
    path = get_file_path(job, filename)
    if path is None:
        return JSONResponse({"error": "File not found."}, status_code=404)
    return StreamingResponse(
        _stream_file(path),
        media_type=_content_type(path),
        headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
    )
