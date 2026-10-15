"""Admin commands. Usage:  python -m app.cli create-tenant "KreditBee (demo)" """

import asyncio
import sys

from app.db import SessionFactory, engine
from app.models import Tenant
from app.security import generate_api_key, hash_api_key


async def create_tenant(name: str) -> None:
    key = generate_api_key()
    async with SessionFactory() as db:
        tenant = Tenant(name=name, api_key_hash=hash_api_key(key), api_key_prefix=key[:12])
        db.add(tenant)
        await db.commit()
    await engine.dispose()
    print(f"Tenant created: {name} ({tenant.id})")
    print(f"API key (shown once, store it now): {key}")


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] != "create-tenant":
        print(__doc__)
        sys.exit(1)
    asyncio.run(create_tenant(sys.argv[2]))


if __name__ == "__main__":
    main()
