from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import select  # noqa: E402

from database import async_session  # noqa: E402
from dependencies.openai_client import get_openai_client  # noqa: E402
from models.database.profile import UserProfile  # noqa: E402
from services.cv_generator.digester import persist_digest, refresh_digest  # noqa: E402


async def _run(profile_id: UUID) -> None:
    async with async_session() as session:
        result = await session.execute(
            select(UserProfile).where(UserProfile.id == profile_id)
        )
        profile = result.scalar_one_or_none()
        if profile is None:
            print(f"✗ profile {profile_id} not found")
            sys.exit(1)

        print(f"→ Generando digest para profile {profile_id}...")
        client = get_openai_client()
        digest = await refresh_digest(client, profile.payload or {})
        await persist_digest(session, profile, digest)

    print(f"✓ Digest guardado: {len(digest.get('skills', []))} skills, "
          f"{len(digest.get('experiences', []))} exp, "
          f"{len(digest.get('education', []))} edu")


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresca el digest del perfil")
    parser.add_argument("--profile-id", required=True)
    args = parser.parse_args()

    try:
        asyncio.run(_run(UUID(args.profile_id)))
    except Exception as exc:
        print(f"✗ {type(exc).__name__}: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
