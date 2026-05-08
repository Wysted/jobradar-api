"""Persiste el payload extraído a user_profiles.

Estrategia: merge (payload || extracted) — NO sobrescribe lo que el ranker
ya produjo (areas/technologies custom, salary, deal_breakers, etc).
El LLM de ingesta puede traer areas/technologies aproximadas, pero si ya
hay algo cargado con más granularidad, ese tiene precedencia.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from config import DATABASE_URL


def _to_asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


# Campos que SÍ vienen de la ingesta y deben sobreescribir.
CV_SOURCED_KEYS = {
    "personal_info", "links", "location", "summary",
    "headline", "experiences", "education", "methodologies",
}

# Campos que NO se tocan si ya existen (propiedad del user/ranker).
RANKER_PROTECTED = {
    "salary", "work_mode", "deal_breakers", "contract_types",
    "roles_target", "roles_excluded", "company_size_pref",
    "industries_excluded", "industries_preferred",
}


def merge_payloads(current: dict[str, Any], extracted: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current)
    for key, value in extracted.items():
        if key in CV_SOURCED_KEYS or key not in current:
            merged[key] = value
        elif key in RANKER_PROTECTED:
            continue
        else:
            if not current.get(key):
                merged[key] = value
    return merged


async def persist_profile(
    payload: dict[str, Any],
    profile_id: str | None = None,
) -> str:
    dsn = _to_asyncpg_dsn(DATABASE_URL)
    conn = await asyncpg.connect(dsn)
    try:
        if profile_id:
            row = await conn.fetchrow(
                "SELECT payload FROM user_profiles WHERE id = $1", profile_id,
            )
            if row is None:
                raise RuntimeError(f"profile_id {profile_id} no existe")
            current = row["payload"]
            if isinstance(current, str):
                current = json.loads(current)
            merged = merge_payloads(current, payload)
            await conn.execute(
                "UPDATE user_profiles SET payload = $1::jsonb, updated_at = now() WHERE id = $2",
                json.dumps(merged), profile_id,
            )
            return profile_id
        else:
            new_id = await conn.fetchval(
                "INSERT INTO user_profiles (payload) VALUES ($1::jsonb) RETURNING id",
                json.dumps(payload),
            )
            return str(new_id)
    finally:
        await conn.close()
