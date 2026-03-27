"""In-memory job store for yt-bulk-dl downloads."""

import asyncio
import io
import shutil
import tempfile
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from yt_bulk_dl.core.downloader import download_all, parse_urls, write_metadata_csv
from yt_bulk_dl.settings import get_semaphore, get_settings

_executor = ThreadPoolExecutor(max_workers=4)
_jobs: dict[str, "Job"] = {}
_jobs_lock = threading.Lock()


@dataclass
class VideoState:
    url: str
    display: str         # URL initially, replaced by title when known
    status: str = "pending"   # pending | downloading | done | error
    filename: str = ""
    channel: str = ""


@dataclass
class Job:
    job_id: str
    status: str       # pending | running | done | error
    phase: str        # pending | download | done | error
    video_states: list[VideoState]
    prefix: str | None
    max_length: int
    output_dir: Path | None
    error: str | None
    created_at: datetime
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def on_event(self, event: dict) -> None:
        with self._lock:
            t = event.get("type")
            if t == "phase":
                self.phase = event["phase"]
            elif t == "videos_init":
                self.video_states = [VideoState(url=u, display=u) for u in event["urls"]]
            elif t == "video_info":
                for vs in self.video_states:
                    if vs.url == event["url"]:
                        vs.display = event["title"] or vs.url
                        vs.channel = event.get("channel", "")
                        break
            elif t == "video_start":
                for vs in self.video_states:
                    if vs.url == event["url"]:
                        vs.status = "downloading"
                        break
            elif t == "video_done":
                for vs in self.video_states:
                    if vs.url == event["url"]:
                        vs.status = "done" if event.get("success") else "error"
                        vs.filename = event.get("filename", "")
                        if event.get("title"):
                            vs.display = event["title"]
                        if event.get("channel"):
                            vs.channel = event["channel"]
                        break

    @property
    def done_count(self) -> int:
        return sum(1 for v in self.video_states if v.status == "done")

    @property
    def error_count(self) -> int:
        return sum(1 for v in self.video_states if v.status == "error")


def create_job(urls: list[str], prefix: str | None, max_length: int) -> "Job":
    job_id = uuid.uuid4().hex
    job = Job(
        job_id=job_id,
        status="pending",
        phase="pending",
        video_states=[],
        prefix=prefix,
        max_length=max_length,
        output_dir=None,
        error=None,
        created_at=datetime.utcnow(),
    )
    with _jobs_lock:
        _jobs[job_id] = job
    return job


def get_job(job_id: str) -> "Job | None":
    with _jobs_lock:
        return _jobs.get(job_id)


def _run_job(job: Job, urls: list[str]) -> None:
    job.status = "running"
    output_dir = Path(tempfile.mkdtemp(prefix=f"ytdl_{job.job_id}_"))
    job.output_dir = output_dir

    settings = get_settings()
    semaphore = get_semaphore()

    try:
        job.on_event({"type": "phase", "phase": "download"})
        job.on_event({"type": "videos_init", "urls": urls})

        metadata_rows = download_all(
            urls=urls,
            download_dir=output_dir,
            prefix=job.prefix,
            max_len=job.max_length,
            max_workers=settings.max_workers_per_job,
            on_event=job.on_event,
            global_semaphore=semaphore,
        )

        write_metadata_csv(metadata_rows, output_dir / "metadata.csv")
        job.status = "done"
        job.phase = "done"
    except Exception as e:
        job.error = f"Download failed: {e}"
        job.status = "error"
        job.phase = "error"


async def launch_job(job_id: str, urls: list[str]) -> None:
    job = get_job(job_id)
    if job is None:
        return
    loop = asyncio.get_event_loop()
    loop.run_in_executor(_executor, _run_job, job, urls)


def build_zip(job: Job) -> io.BytesIO:
    buf = io.BytesIO()
    if job.output_dir is None or not job.output_dir.exists():
        return buf
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(job.output_dir.iterdir()):
            if file.suffix.lower() in (".mp4", ".srt", ".csv"):
                zf.write(file, file.name)
    buf.seek(0)
    return buf


async def cleanup_old_jobs(max_age_hours: int = 2) -> None:
    cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
    to_delete = []
    with _jobs_lock:
        for job_id, job in _jobs.items():
            if job.created_at < cutoff:
                to_delete.append(job_id)
    for job_id in to_delete:
        with _jobs_lock:
            job = _jobs.pop(job_id, None)
        if job and job.output_dir and job.output_dir.exists():
            shutil.rmtree(job.output_dir, ignore_errors=True)
