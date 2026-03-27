"""FastAPI router for the green-to-red service."""

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from green_to_red.job_runner import build_zip, create_job, get_job, launch_job

router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


@router.get("/")
async def form(request: Request):
    return _templates(request).TemplateResponse(request, "green_to_red/form.html")


@router.post("/convert")
async def start_convert(
    request: Request,
    spotify_url: str = Form(...),
    workers: int = Form(3),
):
    spotify_url = spotify_url.strip()
    if not spotify_url:
        return _templates(request).TemplateResponse(
            request, "green_to_red/form.html",
            {"error": "Please enter a Spotify URL."},
            status_code=422,
        )

    if "spotify" not in spotify_url.lower():
        return _templates(request).TemplateResponse(
            request, "green_to_red/form.html",
            {"error": "That doesn't look like a Spotify URL."},
            status_code=422,
        )

    job = create_job()
    await launch_job(job.job_id, spotify_url, max(1, min(5, workers)))
    return RedirectResponse(
        url=request.url_for("job_page", job_id=job.job_id), status_code=303
    )


@router.get("/convert/{job_id}", name="job_page")
async def job_page(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        return _templates(request).TemplateResponse(
            request, "green_to_red/form.html",
            {"error": "Job not found. It may have expired."},
            status_code=404,
        )
    fragment_url = str(request.url_for("job_fragment", job_id=job_id))
    download_url = str(request.url_for("job_download", job_id=job_id))
    return _templates(request).TemplateResponse(
        request, "green_to_red/job_status.html",
        {"job": job, "fragment_url": fragment_url, "download_url": download_url},
    )


@router.get("/convert/{job_id}/status", name="job_status_api")
async def job_status(job_id: str):
    """JSON status endpoint — for programmatic/API use."""
    job = get_job(job_id)
    if job is None:
        return JSONResponse({"status": "error", "error": "Job not found."}, status_code=404)
    return JSONResponse({
        "status": job.status,
        "messages": job.messages,
        "progress_pct": job.progress_pct,
        "error": job.error,
        "content_name": job.result.content_name if job.result else None,
        "track_count": job.result.track_count if job.result else None,
        "downloaded_count": job.result.downloaded_count if job.result else None,
        "not_found_count": len(job.result.not_found) if job.result else None,
    })


@router.get("/convert/{job_id}/fragment", name="job_fragment")
async def job_fragment(request: Request, job_id: str):
    """HTML partial for HTMX polling — returns the status panel div."""
    job = get_job(job_id)
    if job is None:
        return JSONResponse({"error": "Job not found."}, status_code=404)
    fragment_url = str(request.url_for("job_fragment", job_id=job_id))
    download_url = str(request.url_for("job_download", job_id=job_id))
    return _templates(request).TemplateResponse(
        request, "green_to_red/_status_fragment.html",
        {"job": job, "fragment_url": fragment_url, "download_url": download_url},
    )


@router.get("/convert/{job_id}/download", name="job_download")
async def job_download(job_id: str):
    job = get_job(job_id)
    if job is None or job.status != "done":
        return JSONResponse({"error": "Job not ready."}, status_code=404)

    zip_buf = build_zip(job)
    filename = f"{job.result.content_name}.zip" if job.result else "download.zip"

    return StreamingResponse(
        zip_buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
