"""In-memory job store and background task runner for green-to-red conversions."""

import io
import asyncio
import tempfile
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from green_to_red.core.pipeline import PipelineError, PipelineResult, run_pipeline

# Cap at 2 full pipeline runs concurrently to avoid saturating the machine.
_executor = ThreadPoolExecutor(max_workers=2)
_jobs: dict[str, "Job"] = {}
_jobs_lock = threading.Lock()


@dataclass
class Job:
    job_id: str
    status: Literal["pending", "running", "done", "error"]
    messages: list[str]
    result: PipelineResult | None
    error: str | None
    created_at: datetime
    output_dir: Path | None
    _msg_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add_message(self, msg: str) -> None:
        with self._msg_lock:
            self.messages.append(msg)

    @property
    def progress_pct(self) -> int:
        """Rough progress estimate based on known pipeline stages."""
        if self.status == "done":
            return 100
        if self.status == "error":
            return 0
        n = len(self.messages)
        # Heuristic: first ~5 messages = setup, then slow download phase
        return min(95, max(5, n * 3))


def create_job() -> Job:
    job_id = uuid.uuid4().hex
    job = Job(
        job_id=job_id,
        status="pending",
        messages=[],
        result=None,
        error=None,
        created_at=datetime.utcnow(),
        output_dir=None,
    )
    with _jobs_lock:
        _jobs[job_id] = job
    return job


def get_job(job_id: str) -> Job | None:
    with _jobs_lock:
        return _jobs.get(job_id)


def _run_job(job: Job, spotify_url: str, workers: int) -> None:
    """Blocking function executed in the thread pool."""
    job.status = "running"
    output_dir = Path(tempfile.mkdtemp(prefix=f"g2r_{job.job_id}_"))
    job.output_dir = output_dir

    try:
        result = run_pipeline(
            spotify_url=spotify_url,
            job_dir=output_dir,
            progress_callback=job.add_message,
            workers=workers,
        )
        job.result = result
        job.status = "done"
    except PipelineError as e:
        job.error = str(e)
        job.status = "error"
    except Exception as e:
        job.error = f"Unexpected error: {e}"
        job.status = "error"


async def launch_job(job_id: str, spotify_url: str, workers: int) -> None:
    job = get_job(job_id)
    if job is None:
        return
    loop = asyncio.get_event_loop()
    loop.run_in_executor(_executor, _run_job, job, spotify_url, workers)


def build_zip(job: Job) -> io.BytesIO:
    """Build an in-memory ZIP of the job's MP3 files and CSV."""
    buf = io.BytesIO()
    mp3_dir = job.result.mp3_dir if job.result else job.output_dir
    if mp3_dir is None or not mp3_dir.exists():
        return buf

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(mp3_dir.iterdir()):
            if file.suffix.lower() in (".mp3", ".csv"):
                zf.write(file, file.name)

    buf.seek(0)
    return buf


async def cleanup_old_jobs(max_age_hours: int = 2) -> None:
    """Delete output directories and job records older than max_age_hours."""
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
            import shutil
            shutil.rmtree(job.output_dir, ignore_errors=True)
