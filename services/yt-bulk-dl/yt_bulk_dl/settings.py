"""Server-side download settings for yt-bulk-dl (admin-controlled)."""

import threading
from dataclasses import dataclass


@dataclass
class DownloadSettings:
    max_workers_per_job: int = 3   # concurrent downloads per session
    max_workers_global: int = 6    # total concurrent downloads server-wide


_settings = DownloadSettings()
_semaphore = threading.Semaphore(_settings.max_workers_global)
_lock = threading.Lock()


def get_settings() -> DownloadSettings:
    return _settings


def get_semaphore() -> threading.Semaphore:
    return _semaphore


def update_settings(
    max_workers_per_job: int | None = None,
    max_workers_global: int | None = None,
) -> None:
    global _settings, _semaphore
    with _lock:
        if max_workers_per_job is not None:
            _settings.max_workers_per_job = max_workers_per_job
        if max_workers_global is not None:
            _settings.max_workers_global = max_workers_global
            _semaphore = threading.Semaphore(_settings.max_workers_global)
