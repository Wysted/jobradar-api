import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import config
from dependencies.openai_client import EMBEDDING_MODEL
from models.database.profile import UserProfile
from fastapi import HTTPException
from models.profile import ProfileIn, ProfilePatch
from services.embedding import embed_one

logger = logging.getLogger(__name__)

# All semantic fields — any change here shifts embedding space (design §5).
_EMBEDDING_AFFECTING_FIELDS = [
    "headline",
    "total_years",
    "areas",
    "technologies",
    "soft_skills",
    "roles_target",
    "roles_excluded",
    "industries_preferred",
    "industries_excluded",
    "location",
    "work_mode",
    "salary",
    "languages",
    "contract_types",
    "company_size_pref",
    "deal_breakers",
]


def serialize_profile(profile: ProfileIn) -> str:
    """Deterministic JSON for hashing — sort_keys guarantees byte-identical output."""
    payload = json.loads(profile.model_dump_json())
    subset = {k: payload.get(k) for k in _EMBEDDING_AFFECTING_FIELDS}
    return json.dumps(subset, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_profile_hash(profile: ProfileIn) -> str:
    canonical = serialize_profile(profile)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _serialize_for_embedding(profile: ProfileIn) -> str:
    """Human-readable projection used as embedding input (design §4).

    Sections are omitted when the underlying list/object is empty/falsy,
    so that the embedding space is not polluted by empty field tokens.
    Determinism: every list is sorted alphabetically by name.
    """
    lines: list[str] = []

    if profile.headline:
        lines.append(f"Headline: {profile.headline}")

    lines.append(f"Total experience: {profile.total_years} years.")

    if profile.areas:
        parts = ", ".join(
            f"{a.name} (seniority {a.seniority})"
            for a in sorted(profile.areas, key=lambda a: a.name)
        )
        lines.append(f"Areas: {{{parts}}}")

    if profile.technologies:
        parts = ", ".join(
            f"{t.name}/{t.area_name} ({t.seniority})"
            for t in sorted(profile.technologies, key=lambda t: (t.area_name, t.name))
        )
        lines.append(f"Technologies: {{{parts}}}")

    if profile.soft_skills:
        lines.append(f"Soft skills: {{{', '.join(sorted(profile.soft_skills))}}}")

    if profile.roles_target:
        lines.append(f"Target roles: {{{', '.join(sorted(profile.roles_target))}}}")

    if profile.roles_excluded:
        lines.append(f"Excluded roles: {{{', '.join(sorted(profile.roles_excluded))}}}")

    if profile.industries_preferred:
        lines.append(
            f"Preferred industries: {{{', '.join(sorted(profile.industries_preferred))}}}"
        )

    if profile.industries_excluded:
        lines.append(
            f"Excluded industries: {{{', '.join(sorted(profile.industries_excluded))}}}"
        )

    loc = profile.location
    if loc.city or loc.country:
        loc_str = ", ".join(filter(None, [loc.city, loc.country]))
        lines.append(
            f"Location: {{{loc_str}; willing_to_relocate={str(loc.willing_to_relocate).lower()}}}"
        )

    if profile.work_mode:
        lines.append(f"Work mode: {{{', '.join(sorted(profile.work_mode))}}}")

    if profile.salary:
        s = profile.salary
        salary_parts = [f"{s.currency} {s.min_monthly}"]
        if s.expected_monthly is not None:
            salary_parts.append(f"expected {s.currency} {s.expected_monthly}")
        lines.append(f"Salary floor: {{{'; '.join(salary_parts)}}}")

    if profile.languages:
        parts = ", ".join(
            f"{lang.name} ({lang.level})"
            for lang in sorted(profile.languages, key=lambda l: l.name)
        )
        lines.append(f"Languages: {{{parts}}}")

    if profile.contract_types:
        lines.append(f"Contract types: {{{', '.join(sorted(profile.contract_types))}}}")

    if profile.company_size_pref:
        lines.append(
            f"Company size preference: {{{', '.join(sorted(profile.company_size_pref))}}}"
        )

    if profile.deal_breakers:
        lines.append(f"Deal breakers: {{{', '.join(sorted(profile.deal_breakers))}}}")

    return "\n".join(lines)


async def upsert_profile(
    session: AsyncSession, profile_in: ProfileIn
) -> tuple[UserProfile, bool]:
    new_hash = compute_profile_hash(profile_in)
    payload = json.loads(profile_in.model_dump_json())

    result = await session.execute(select(UserProfile).limit(1))
    existing = result.scalar_one_or_none()

    if existing is None:
        # Spec R1: first creation yields embedding_hash=null (pending embed).
        profile = UserProfile(
            payload=payload,
            embedding_hash=None,
        )
        session.add(profile)
        await session.commit()
        await session.refresh(profile)
        return profile, True

    existing.payload = payload
    if existing.embedding_hash != new_hash:
        existing.embedding_hash = new_hash
        existing.embedding = None
        existing.embedded_at = None
        existing.embedded_hash = None
        if isinstance(existing.payload, dict):
            existing.payload["digest"] = None
    existing.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(existing)
    return existing, False


async def patch_profile(session: AsyncSession, patch: ProfilePatch) -> UserProfile:
    result = await session.execute(select(UserProfile).limit(1))
    existing = result.scalar_one_or_none()
    if existing is None:
        raise HTTPException(status_code=404, detail="profile not found")

    merged = {**existing.payload, **patch.model_dump(exclude_unset=True, mode="json")}
    merged.pop("digest", None)
    validated = ProfileIn(**merged)
    new_hash = compute_profile_hash(validated)

    existing.payload = json.loads(validated.model_dump_json())
    if existing.embedding_hash != new_hash:
        existing.embedding_hash = new_hash
        existing.embedding = None
        existing.embedded_at = None
        existing.embedded_hash = None
        if isinstance(existing.payload, dict):
            existing.payload["digest"] = None
    existing.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(existing)
    return existing


async def get_profile(session: AsyncSession) -> UserProfile | None:
    result = await session.execute(select(UserProfile).limit(1))
    return result.scalar_one_or_none()


async def ensure_profile_embedded(session: AsyncSession, client) -> UserProfile:
    profile = await get_profile(session)
    if profile is None:
        raise ValueError("No profile found; run PUT /api/v1/profile first")

    profile_in = ProfileIn(**profile.payload)
    current_hash = compute_profile_hash(profile_in)

    # On first embed (post-create) embedding_hash is null — materialize it now.
    if profile.embedding_hash != current_hash:
        profile.embedding_hash = current_hash

    stale = profile.embedding is None or profile.embedded_hash != profile.embedding_hash
    if not stale:
        return profile

    text = _serialize_for_embedding(profile_in)
    vector = await embed_one(client, text)

    profile.embedding = vector
    profile.embedded_at = datetime.now(timezone.utc)
    profile.embedded_hash = profile.embedding_hash
    profile.embedding_model = EMBEDDING_MODEL
    await session.commit()
    await session.refresh(profile)
    return profile
