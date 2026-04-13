"""Server-side matching settings for edl-to-archive (admin-controlled)."""

from dataclasses import dataclass


@dataclass
class MatchSettings:
    min_match_length: int = 4  # minimum chars for prefix matching


_settings = MatchSettings()


def get_settings() -> MatchSettings:
    return _settings


def update_settings(
    min_match_length: int | None = None,
) -> None:
    if min_match_length is not None:
        _settings.min_match_length = min_match_length
