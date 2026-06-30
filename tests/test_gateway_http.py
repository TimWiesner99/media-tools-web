"""HTTP-level gateway integration checks."""

from __future__ import annotations

import hashlib
import os

from fastapi.testclient import TestClient

from main import app


def _login_as_admin(client: TestClient) -> None:
    password = os.environ.get("ADMIN_PASSWORD", "admin")
    response = client.post(
        "/login",
        data={
            "username": "admin",
            "password_hash": hashlib.sha256(password.encode("utf-8")).hexdigest(),
            "next_url": "/",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_authenticated_homepage_and_legacy_spotify_route() -> None:
    with TestClient(app) as client:
        _login_as_admin(client)

        home = client.get("/")
        assert home.status_code == 200
        assert "Media Tools" in home.text
        assert "/spotify-dl/" in home.text

        legacy = client.get("/green-to-red/", follow_redirects=False)
        assert legacy.status_code == 307
        assert legacy.headers["location"] == "/spotify-dl/"

        redirected = client.get("/green-to-red/")
        assert redirected.status_code == 200
        assert "spotify_dl" in redirected.text
        assert "Spotify URL" in redirected.text