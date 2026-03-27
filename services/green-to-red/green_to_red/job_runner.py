"""In-memory job store and background task runner for green-to-red conversions."""

import io
import asyncio
import shutil
import tempfile
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from green_to_red.core.pipeline import PipelineError, PipelineResult, run_pipeline
from green_to_red.settings import get_semaphore, get_settings

# Thread pool for pipeline orchestration (not for yt-dlp downloads themselves)
_executor = ThreadPoolExecutor(max_workers=4)
_jobs: dict[str, "Job"] = {}
_jobs_lock = threading.Lock()


@dataclass
class TrackState:
    display_name: str
    yt_status: str = "pending"  # pending | found | not_found
    dl_status: str = "pending"  # pending | downloading | done | error


@dataclass
class Job:
    job_id: str
    status: str     # pending | running | done | error
    phase: str      # pending | spotify | tracks | done | error
    mb_status: str  # pending | running | done
    content_name: str | None
    track_states: list[TrackState]
    result: PipelineResult | None
    error: str | None
    created_at: datetime
    output_dir: Path | None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def on_event(self, event: dict) -> None:
        """Callback called by the pipeline thread with structured progress events."""
        with self._lock:
            t = event.get("type")
            if t == "phase":
                self.phase = event["phase"]
            elif t == "spotify_done":
                self.content_name = event.get("content_name")
            elif t == "tracks_init":
                self.track_states = [TrackState(display_name=n) for n in event["names"]]
            elif t == "yt_result":
                for ts in self.track_states:
                    if ts.display_name == event["name"]:
                        ts.yt_status = "found" if event["found"] else "not_found"
                        break
            elif t == "dl_start":
                for ts in self.track_states:
                    if ts.display_name == event["name"]:
                        ts.dl_status = "downloading"
                        break
            elif t == "dl_done":
                for ts in self.track_states:
                    if ts.display_name == event["name"]:
                        ts.dl_status = "done" if event.get("success") else "error"
                        break
            elif t == "mb_start":
                self.mb_status = "running"
            elif t == "mb_done":
                self.mb_status = "done"
            # "note" events (internal progress strings) are intentionally ignored

    @property
    def dl_done_count(self) -> int:
        return sum(1 for t in self.track_states if t.dl_status == "done")

    @property
    def dl_found_count(self) -> int:
        return sum(1 for t in self.track_states if t.yt_status == "found")


def create_job() -> Job:
    job_id = uuid.uuid4().hex
    job = Job(
        job_id=job_id,
        status="pending",
        phase="pending",
        mb_status="pending",
        content_name=None,
        track_states=[],
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


def _run_job(job: Job, spotify_url: str) -> None:
    """Blocking function executed in the thread pool."""
    job.status = "running"
    output_dir = Path(tempfile.mkdtemp(prefix=f"g2r_{job.job_id}_"))
    job.output_dir = output_dir

    settings = get_settings()
    semaphore = get_semaphore()

    try:
        result = run_pipeline(
            spotify_url=spotify_url,
            job_dir=output_dir,
            progress_callback=job.on_event,
            workers=settings.max_workers_per_job,
            global_semaphore=semaphore,
        )
        job.result = result
        job.status = "done"
        job.phase = "done"
    except PipelineError as e:
        job.error = str(e)
        job.status = "error"
        job.phase = "error"
    except Exception as e:
        job.error = f"Unexpected error: {e}"
        job.status = "error"
        job.phase = "error"


async def launch_job(job_id: str, spotify_url: str) -> None:
    job = get_job(job_id)
    if job is None:
        return
    loop = asyncio.get_event_loop()
    loop.run_in_executor(_executor, _run_job, job, spotify_url)


def build_zip(job: Job) -> io.BytesIO:
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
