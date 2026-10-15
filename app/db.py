"""Database engine and session handling.

The engine owns a pool of open connections to Postgres. A session is one unit of work
borrowed from that pool for the length of a request, then handed back.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    get_settings().database_url,
    pool_size=get_settings().db_pool_size,
    max_overflow=get_settings().db_pool_size,
    # Test a pooled connection before use, so a restarted DB doesn't break requests.
    pool_pre_ping=True,
)

# expire_on_commit=False: after commit we can still read attributes of returned objects
# (to build the response) without SQLAlchemy firing another query.
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, always closed afterwards."""
    async with SessionFactory() as session:
        yield session
