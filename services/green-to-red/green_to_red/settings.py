"""Global download settings for the green-to-red service.

Modifiable at runtime via the admin panel. In Phase 2 (Docker), this
will move to a shared configuration store (Redis/DB).
"""

import threading
from dataclasses import dataclass


@dataclass
class DownloadSettings:
    max_workers_per_job: int = 5   # max concurrent yt-dlp threads per session
    max_workers_global: int = 10   # max concurrent yt-dlp threads across all sessions


_settings = DownloadSettings()
_lock = threading.Lock()
_semaphore = threading.Semaphore(_settings.max_workers_global)


def get_settings() -> DownloadSettings:
    return _settings


def update_settings(
    max_workers_per_job: int | None = None,
    max_workers_global: int | None = None,
) -> None:
    global _semaphore
    with _lock:
        if max_workers_per_job is not None:
            _settings.max_workers_per_job = max(1, int(max_workers_per_job))
        if max_workers_global is not None:
            _settings.max_workers_global = max(1, int(max_workers_global))
            _semaphore = threading.Semaphore(_settings.max_workers_global)


def get_semaphore() -> threading.Semaphore:
    return _semaphore
