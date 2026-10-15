"""Deployment settings: hosted-Postgres URLs and the all-in-one mode."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import get_settings, normalise_database_url


@pytest.mark.parametrize(
    "given, expected",
    [
        # Neon's default copy-paste string: pooled host, libpq options
        (
            "postgresql://alex:pw@ep-cool-sky-a1b2c3-pooler.ap-southeast-1.aws.neon.tech/revive"
            "?sslmode=require&channel_binding=require",
            "postgresql+asyncpg://alex:pw@ep-cool-sky-a1b2c3.ap-southeast-1.aws.neon.tech/revive"
            "?ssl=require",
        ),
        # Heroku/Render style scheme
        ("postgres://u:p@host:5432/db", "postgresql+asyncpg://u:p@host:5432/db"),
        # Already right: unchanged (local development)
        (
            "postgresql+asyncpg://revive:revive@localhost:5442/revive",
            "postgresql+asyncpg://revive:revive@localhost:5442/revive",
        ),
        # sslmode=disable just disappears
        ("postgresql://u:p@h/db?sslmode=disable", "postgresql+asyncpg://u:p@h/db"),
    ],
)
def test_database_url_normalisation(given, expected):
    assert normalise_database_url(given) == expected


async def test_all_in_one_mounts_the_simulator(monkeypatch):
    monkeypatch.setattr(get_settings(), "mount_simulator", True)
    from app.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # The simulator answers under /sim, and still checks its API key
        r = await c.post("/sim/v1/calls", json={})
        assert r.status_code in (401, 422)
        assert (await c.get("/health/live")).status_code == 200
