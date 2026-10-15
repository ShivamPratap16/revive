"""Test setup: a real Postgres (the revive_test database), not mocks.

Mocking the database would hide exactly the things worth testing here: unique
constraints, ON CONFLICT, row locks and transactions.
"""

import os

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://revive:revive@localhost:5442/revive_test"
)
os.environ["LOG_LEVEL"] = "WARNING"

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import models  # noqa: E402,F401
from app.db import Base, SessionFactory, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Tenant  # noqa: E402
from app.security import generate_api_key, hash_api_key  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
async def schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean_tables():
    yield
    tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))


async def _make_tenant(name: str) -> str:
    key = generate_api_key()
    async with SessionFactory() as db:
        db.add(Tenant(name=name, api_key_hash=hash_api_key(key), api_key_prefix=key[:12]))
        await db.commit()
    return key


@pytest.fixture
async def api_key() -> str:
    return await _make_tenant("KreditBee (test)")


@pytest.fixture
async def other_api_key() -> str:
    return await _make_tenant("PhonePe (test)")


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
