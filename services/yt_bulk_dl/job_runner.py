"""In-memory job store for yt-bulk-dl downloads."""

import asyncio
import json
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
    user_id: str           # ID of the user who owns this job
    status: str            # pending | running | done | error
    phase: str             # pending | download | done | error
    video_states: list[VideoState]
    prefix: str | None
    max_length: int
    output_dir: Path | None
    error: str | None
    created_at: datetime
    last_accessed: datetime
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


def create_job(urls: list[str], prefix: str | None, max_length: int, user_id: str = "anonymous") -> "Job":
    now = datetime.utcnow()
    job_id = uuid.uuid4().hex
    job = Job(
        job_id=job_id,
        user_id=user_id,
        status="pending",
        phase="pending",
        video_states=[],
        prefix=prefix,
        max_length=max_length,
        output_dir=None,
        error=None,
        created_at=now,
        last_accessed=now,
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


@dataclass
class ZipPart:
    path: Path
    part_number: int
    is_standalone: bool
    filename: str


def get_zip_parts(job: Job) -> list[ZipPart]:
    """Build chunked zip files on disk and return the part manifest.

    Idempotent: if the manifest already exists, just reads it back.
    Thread-safe via job._lock.
    """
    if job.output_dir is None or not job.output_dir.exists():
        return []

    manifest_path = job.output_dir / "_parts.json"

    with job._lock:
        # Return cached manifest if already built
        if manifest_path.exists():
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            return [
                ZipPart(
                    path=job.output_dir / d["path"],
                    part_number=d["part_number"],
                    is_standalone=d["is_standalone"],
                    filename=d["filename"],
                )
                for d in data
            ]

        settings = get_settings()
        max_bytes = settings.max_zip_size_mb * 1024 * 1024

        # Collect downloadable files (exclude internal _parts.json / _part*.zip)
        media_files: list[Path] = []
        metadata_file: Path | None = None
        for f in sorted(job.output_dir.iterdir()):
            if f.name.startswith("_"):
                continue
            if f.suffix.lower() in (".mp4", ".srt", ".csv"):
                if f.name == "metadata.csv":
                    metadata_file = f
                else:
                    media_files.append(f)

        if not media_files:
            return []

        metadata_size = metadata_file.stat().st_size if metadata_file else 0
        prefix = job.prefix or "videos"

        # Group files into chunks
        chunks: list[list[Path]] = []  # each inner list is files for one zip
        standalone: list[Path] = []     # oversized files served as-is
        current_chunk: list[Path] = []
        current_size = metadata_size  # metadata goes in every chunk

        for f in media_files:
            fsize = f.stat().st_size
            # Single file exceeds limit -> standalone
            if fsize > max_bytes:
                # Flush current chunk first
                if current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = []
                    current_size = metadata_size
                standalone.append(f)
                continue
            # Would exceed limit -> start new chunk
            if current_chunk and current_size + fsize > max_bytes:
                chunks.append(current_chunk)
                current_chunk = []
                current_size = metadata_size
            current_chunk.append(f)
            current_size += fsize

        if current_chunk:
            chunks.append(current_chunk)

        # Build zip files on disk
        parts: list[ZipPart] = []
        part_num = 0
        total_parts = len(chunks) + len(standalone)

        for chunk_files in chunks:
            part_num += 1
            if total_parts == 1:
                zip_name = f"_part1.zip"
                dl_filename = f"{prefix}_videos.zip"
            else:
                zip_name = f"_part{part_num}.zip"
                dl_filename = f"{prefix}_videos_part{part_num}.zip"

            zip_path = job.output_dir / zip_name
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                if metadata_file:
                    zf.write(metadata_file, metadata_file.name)
                for f in chunk_files:
                    zf.write(f, f.name)

            parts.append(ZipPart(
                path=zip_path,
                part_number=part_num,
                is_standalone=False,
                filename=dl_filename,
            ))

        for sf in standalone:
            part_num += 1
            parts.append(ZipPart(
                path=sf,
                part_number=part_num,
                is_standalone=True,
                filename=sf.name,
            ))

        # Write manifest
        manifest_data = [
            {
                "path": p.path.name,
                "part_number": p.part_number,
                "is_standalone": p.is_standalone,
                "filename": p.filename,
            }
            for p in parts
        ]
        manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

        return parts


def get_file_path(job: Job, filename: str) -> Path | None:
    """Return the full path for a file in the job output dir, or None if invalid."""
    if job.output_dir is None or not job.output_dir.exists():
        return None
    if ".." in filename or "/" in filename or "\\" in filename:
        return None
    allowed_ext = {".mp4", ".srt", ".csv"}
    path = job.output_dir / filename
    if path.suffix.lower() not in allowed_ext:
        return None
    if not path.exists():
        return None
    return path


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
