"""In-memory job store and background task runner for green-to-red conversions."""

import io
import asyncio
import logging
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from green_to_red.core.pipeline import PipelineError, PipelineResult, run_pipeline
from green_to_red.settings import get_semaphore, get_settings

logger = logging.getLogger("g2r.job")

# Thread pool for pipeline orchestration (not for yt-dlp downloads themselves)
_executor = ThreadPoolExecutor(max_workers=4)
_jobs: dict[str, "Job"] = {}
_jobs_lock = threading.Lock()

MAX_LOG_ENTRIES = 200


@dataclass
class TrackState:
    display_name: str
    yt_status: str = "pending"  # pending | found | not_found
    dl_status: str = "pending"  # pending | downloading | done | error


@dataclass
class Job:
    job_id: str
    user_id: str           # ID of the user who owns this job
    status: str            # pending | running | done | error
    phase: str             # pending | spotify | tracks | done | error
    mb_status: str         # pending | running | done
    content_name: str | None
    track_states: list[TrackState]
    result: PipelineResult | None
    error: str | None
    created_at: datetime
    last_accessed: datetime
    output_dir: Path | None
    activity_log: list[tuple[float, str]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _log(self, msg: str) -> None:
        """Append a timestamped message to the activity log (must be called under _lock)."""
        self.activity_log.append((time.time(), msg))
        if len(self.activity_log) > MAX_LOG_ENTRIES:
            self.activity_log = self.activity_log[-MAX_LOG_ENTRIES:]

    def on_event(self, event: dict) -> None:
        """Callback called by the pipeline thread with structured progress events."""
        with self._lock:
            t = event.get("type")
            if t == "phase":
                self.phase = event["phase"]
                self._log(f"Phase: {event['phase']}")
            elif t == "spotify_done":
                self.content_name = event.get("content_name")
                self._log(f"Fetched: {event.get('content_name')} ({event.get('track_count')} tracks)")
            elif t == "tracks_init":
                self.track_states = [TrackState(display_name=n) for n in event["names"]]
                self._log(f"Searching YouTube for {len(event['names'])} tracks…")
            elif t == "yt_result":
                name = event["name"]
                found = event["found"]
                for ts in self.track_states:
                    if ts.display_name == name:
                        ts.yt_status = "found" if found else "not_found"
                        break
                self._log(f"YT {'found' if found else 'NOT found'}: {name}")
            elif t == "dl_start":
                name = event["name"]
                for ts in self.track_states:
                    if ts.display_name == name:
                        ts.dl_status = "downloading"
                        break
                self._log(f"Downloading: {name}")
            elif t == "dl_done":
                name = event["name"]
                ok = event.get("success")
                for ts in self.track_states:
                    if ts.display_name == name:
                        ts.dl_status = "done" if ok else "error"
                        break
                self._log(f"{'Downloaded' if ok else 'Download failed'}: {name}")
            elif t == "mb_start":
                self.mb_status = "running"
                self._log("MusicBrainz metadata lookup started")
            elif t == "mb_done":
                self.mb_status = "done"
                self._log("Metadata CSV ready")
            elif t == "note":
                self._log(event.get("msg", ""))

    def get_activity_log(self) -> list[tuple[float, str]]:
        """Return a thread-safe snapshot of the activity log."""
        with self._lock:
            return list(self.activity_log)

    @property
    def dl_done_count(self) -> int:
        return sum(1 for t in self.track_states if t.dl_status == "done")

    @property
    def dl_found_count(self) -> int:
        return sum(1 for t in self.track_states if t.yt_status == "found")


def create_job(user_id: str) -> "Job":
    now = datetime.utcnow()
    job_id = uuid.uuid4().hex
    job = Job(
        job_id=job_id,
        user_id=user_id,
        status="pending",
        phase="pending",
        mb_status="pending",
        content_name=None,
        track_states=[],
        result=None,
        error=None,
        created_at=now,
        last_accessed=now,
        output_dir=None,
    )
    with _jobs_lock:
        _jobs[job_id] = job
    return job


def get_job(job_id: str) -> "Job | None":
    with _jobs_lock:
        return _jobs.get(job_id)


def touch_job(job_id: str) -> None:
    """Update last_accessed timestamp to reset the 30-minute inactivity timer."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.last_accessed = datetime.utcnow()


def get_active_job_for_user(user_id: str) -> "Job | None":
    """Return the first non-terminal job belonging to this user, or None."""
    with _jobs_lock:
        for job in _jobs.values():
            if job.user_id == user_id and job.status not in ("done", "error"):
                return job
    return None


def cleanup_jobs_for_user(user_id: str) -> None:
    """Delete all jobs (and their output files) belonging to a user.
    Called on logout to free storage immediately.
    """
    to_delete = []
    with _jobs_lock:
        for job_id, job in _jobs.items():
            if job.user_id == user_id:
                to_delete.append(job_id)

    for job_id in to_delete:
        with _jobs_lock:
            job = _jobs.pop(job_id, None)
        if job and job.output_dir and job.output_dir.exists():
            shutil.rmtree(job.output_dir, ignore_errors=True)


def _run_job(job: "Job", spotify_url: str) -> None:
    """Blocking function executed in the thread pool."""
    logger.info("Job %s started — URL: %s", job.job_id[:8], spotify_url)
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
        logger.info(
            "Job %s done — %d/%d tracks downloaded",
            job.job_id[:8], result.downloaded_count, result.track_count,
        )
    except PipelineError as e:
        job.error = str(e)
        job.status = "error"
        job.phase = "error"
        logger.error("Job %s pipeline error: %s", job.job_id[:8], e)
    except Exception as e:
        job.error = f"Unexpected error: {e}"
        job.status = "error"
        job.phase = "error"
        logger.exception("Job %s unexpected error", job.job_id[:8])


async def launch_job(job_id: str, spotify_url: str) -> None:
    job = get_job(job_id)
    if job is None:
        return
    loop = asyncio.get_event_loop()
    loop.run_in_executor(_executor, _run_job, job, spotify_url)


def build_zip(job: "Job") -> io.BytesIO:
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


async def cleanup_old_jobs(max_age_minutes: int = 30) -> None:
    """Remove jobs that have been inactive for longer than max_age_minutes."""
    cutoff = datetime.utcnow() - timedelta(minutes=max_age_minutes)
    to_delete = []

    with _jobs_lock:
        for job_id, job in _jobs.items():
            if job.last_accessed < cutoff:
                to_delete.append(job_id)

    for job_id in to_delete:
        with _jobs_lock:
            job = _jobs.pop(job_id, None)
        if job and job.output_dir and job.output_dir.exists():
            shutil.rmtree(job.output_dir, ignore_errors=True)
