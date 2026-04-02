"""FastAPI router for the yt-bulk-dl service."""

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from yt_bulk_dl.core.downloader import parse_urls
from yt_bulk_dl.job_runner import (
    build_zip,
    create_job,
    get_active_job_for_user,
    get_job,
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
    return _templates(request).TemplateResponse(
        request, "yt_bulk_dl/job_status.html",
        {"job": job, "fragment_url": fragment_url, "download_url": download_url},
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
    # HTTP 286 tells HTMX to swap the content AND stop polling
    status_code = 286 if job.status in ("done", "error") else 200
    return _templates(request).TemplateResponse(
        request, "yt_bulk_dl/_status_fragment.html",
        {"job": job, "fragment_url": fragment_url, "download_url": download_url},
        status_code=status_code,
    )


@router.get("/convert/{job_id}/download", name="ytdl_job_download")
async def job_download(job_id: str):
    job = get_job(job_id)
    if job is None or job.status != "done":
        return JSONResponse({"error": "Job not ready."}, status_code=404)
    zip_buf = build_zip(job)
    filename = f"{job.prefix}_videos.zip" if job.prefix else "videos.zip"
    return StreamingResponse(
        zip_buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
