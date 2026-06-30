"""Smoke tests for workspace package wiring and gateway composition."""

from __future__ import annotations

import importlib


def test_workspace_packages_import() -> None:
    for module_name in [
        "media_tools_ui",
        "spotify_dl.main",
        "yt_bulk_dl.main",
        "edl_to_archive.main",
        "gateway.main",
    ]:
        assert importlib.import_module(module_name) is not None


def test_gateway_mounts_services() -> None:
    from main import app

    mounted_paths = {
        route.path
        for route in app.routes
        if hasattr(route, "path")
    }

    assert "/spotify-dl" in mounted_paths
    assert "/yt-bulk-dl" in mounted_paths
    assert "/edl-to-archive" in mounted_paths