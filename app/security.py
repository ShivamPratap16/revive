"""API-key authentication. Each tenant (fintech client) gets one key.

Why SHA-256 and not bcrypt? bcrypt is deliberately slow to protect weak human passwords
from guessing. Our keys are 32 random bytes, which can't be guessed, so a fast hash is
safe here, and it lets us find the tenant with one indexed lookup on every request.
"""

import hashlib
import hmac
import secrets
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import ApiError
from app.models import Tenant

KEY_PREFIX = "rv_live_"


def generate_api_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def current_tenant(
    session: Annotated[AsyncSession, Depends(get_session)],
    x_api_key: Annotated[str | None, Header()] = None,
) -> Tenant:
    """FastAPI dependency: any route that declares it requires a valid X-API-Key header."""
    if not x_api_key:
        raise ApiError(401, "missing_api_key", "Send your key in the X-API-Key header")

    key_hash = hash_api_key(x_api_key)
    tenant = await session.scalar(select(Tenant).where(Tenant.api_key_hash == key_hash))
    # compare_digest takes the same time whether strings match early or late, so an
    # attacker can't learn the hash byte by byte from response timings.
    if tenant is None or not hmac.compare_digest(tenant.api_key_hash, key_hash):
        raise ApiError(401, "invalid_api_key", "API key not recognised")
    return tenant


TenantDep = Annotated[Tenant, Depends(current_tenant)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
