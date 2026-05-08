import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import retry

from config import OPENAI_DIGEST_MODEL
from models.database.profile import UserProfile
from services.retry import openai_retry_policy

SYSTEM_PROMPT = """You are a professional profile compactor.

Your only task: read the full profile payload and return a "digest" — a compact but complete representation, intended to be consumed by ANOTHER LLM that will generate tailored CVs per job posting.

INPUT LANGUAGE: Spanish. OUTPUT: structured JSON. Preserve Spanish text verbatim in all string fields (company names, roles, achievements, degrees — do NOT translate).

RULES:
1. NEVER invent new information. Only condense what is already there.
2. NEVER drop any experience or education. Include them all.
3. NEVER drop any technology or area — all go in, with their seniority.
4. DO preserve achievements text verbatim — the adapter will rewrite them. Do not summarize.
5. Each achievement's `tags` are critical — the adapter uses them for matching. Normalize tags to canonical technical terms (e.g., "Oracle Integration Cloud (OIC)" → "OIC"; "XP (Extreme Programming)" → "XP").
6. Omit user preference fields (salary, deal_breakers, work_mode, contract_types, industries_*, company_size_pref). The adapter does not use them.
7. Omit `personal_info` and `links` — the adapter does not need them (they go directly into the PDF header).
8. Respond ONLY with valid JSON matching the schema. No extra text."""

JSON_SCHEMA = {
    "name": "profile_digest",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "identity", "skills", "methodologies", "soft_skills",
            "experiences", "education", "languages",
        ],
        "properties": {
            "identity": {
                "type": "object",
                "additionalProperties": False,
                "required": ["headline", "summary", "total_years", "roles_target"],
                "properties": {
                    "headline": {"type": ["string", "null"]},
                    "summary": {"type": ["string", "null"]},
                    "total_years": {"type": ["number", "null"]},
                    "roles_target": {"type": "array", "items": {"type": "string"}},
                },
            },
            "skills": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "area", "seniority"],
                    "properties": {
                        "name": {"type": "string"},
                        "area": {"type": "string"},
                        "seniority": {"type": "integer"},
                    },
                },
            },
            "methodologies": {"type": "array", "items": {"type": "string"}},
            "soft_skills": {"type": "array", "items": {"type": "string"}},
            "experiences": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "id", "company", "role", "dates", "location",
                        "is_current", "techs", "achievements",
                    ],
                    "properties": {
                        "id": {"type": "string"},
                        "company": {"type": "string"},
                        "role": {"type": "string"},
                        "dates": {"type": "string"},
                        "location": {"type": ["string", "null"]},
                        "is_current": {"type": "boolean"},
                        "techs": {"type": "array", "items": {"type": "string"}},
                        "achievements": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["text", "tags"],
                                "properties": {
                                    "text": {"type": "string"},
                                    "tags": {"type": "array", "items": {"type": "string"}},
                                },
                            },
                        },
                    },
                },
            },
            "education": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["institution", "degree", "dates", "location"],
                    "properties": {
                        "institution": {"type": "string"},
                        "degree": {"type": "string"},
                        "dates": {"type": "string"},
                        "location": {"type": ["string", "null"]},
                    },
                },
            },
            "languages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "level"],
                    "properties": {
                        "name": {"type": "string"},
                        "level": {"type": ["string", "null"]},
                    },
                },
            },
        },
    },
}


@retry(**openai_retry_policy)
async def _call_digester_llm(client, model: str, messages: list[dict]) -> dict:
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_schema", "json_schema": JSON_SCHEMA},
        temperature=0,
    )
    return json.loads(response.choices[0].message.content)


async def refresh_digest(
    client,
    payload: dict[str, Any],
    model: str | None = None,
) -> dict[str, Any]:
    model_name = model or OPENAI_DIGEST_MODEL
    input_for_llm = {
        k: v for k, v in payload.items()
        if k not in {"personal_info", "links", "digest", "_profile_id"}
    }
    user_prompt = (
        "# PAYLOAD A DIGESTAR\n"
        f"{json.dumps(input_for_llm, ensure_ascii=False, indent=2)}\n\n"
        "Produce el digest compacto siguiendo el schema."
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    return await _call_digester_llm(client, model_name, messages)


async def persist_digest(
    session: AsyncSession,
    profile: UserProfile,
    digest: dict[str, Any],
) -> None:
    payload = dict(profile.payload or {})
    payload["digest"] = digest
    profile.payload = payload
    await session.commit()
    await session.refresh(profile)
