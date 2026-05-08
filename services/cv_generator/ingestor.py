import json
from typing import Any

from tenacity import retry

from config import OPENAI_INGEST_MODEL
from services.retry import openai_retry_policy

SYSTEM_PROMPT = """You are a CV data extractor. Your ONLY job: read a CV text (in Spanish) and return an exact structured JSON.

INPUT LANGUAGE: Spanish. OUTPUT: structured JSON. Preserve Spanish text verbatim in string fields (do NOT translate role names, degrees, company names, or summaries).

CRITICAL RULES:
1. NEVER invent data. If a field is not present in the text, return null or [].
2. NEVER rewrite phrases. Copy the original text as faithfully as possible (preserve Spanish).
3. NEVER infer seniority if not explicit — use 3 as neutral default when a technology is mentioned without context. Assign 4-5 ONLY if the CV explicitly declares it (years, lead role, etc.).
4. For `achievements[].tags`, extract technologies/concepts MENTIONED in that bullet (do not invent).
5. Keep dates and locations exactly as written (do not normalize).
6. `total_years`: estimate by summing `experiences[]` durations. Round to 0.5.
7. For `links`, extract ONLY if the URL appears. If "LinkedIn" appears as a hyperlink label without a visible URL, return null.
8. Respond ONLY with valid JSON matching the schema. No markdown, comments, or extra text."""

JSON_SCHEMA = {
    "name": "user_profile_payload",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "personal_info", "links", "location", "summary", "headline",
            "total_years", "areas", "technologies", "methodologies",
            "soft_skills", "experiences", "education", "languages",
        ],
        "properties": {
            "personal_info": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "email", "phone"],
                "properties": {
                    "name": {"type": ["string", "null"]},
                    "email": {"type": ["string", "null"]},
                    "phone": {"type": ["string", "null"]},
                },
            },
            "links": {
                "type": "object",
                "additionalProperties": False,
                "required": ["linkedin", "github", "portfolio"],
                "properties": {
                    "linkedin": {"type": ["string", "null"]},
                    "github": {"type": ["string", "null"]},
                    "portfolio": {"type": ["string", "null"]},
                },
            },
            "location": {
                "type": "object",
                "additionalProperties": False,
                "required": ["city", "region", "country"],
                "properties": {
                    "city": {"type": ["string", "null"]},
                    "region": {"type": ["string", "null"]},
                    "country": {"type": ["string", "null"]},
                },
            },
            "summary": {"type": ["string", "null"]},
            "headline": {"type": ["string", "null"]},
            "total_years": {"type": ["number", "null"]},
            "areas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "seniority"],
                    "properties": {
                        "name": {"type": "string"},
                        "seniority": {"type": "integer"},
                    },
                },
            },
            "technologies": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "area_name", "seniority"],
                    "properties": {
                        "name": {"type": "string"},
                        "area_name": {"type": "string"},
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
                        "id", "company", "location", "role", "subtitle",
                        "dates", "start", "end", "is_current",
                        "areas", "technologies", "achievements",
                    ],
                    "properties": {
                        "id": {"type": "string"},
                        "company": {"type": "string"},
                        "location": {"type": ["string", "null"]},
                        "role": {"type": "string"},
                        "subtitle": {"type": ["string", "null"]},
                        "dates": {"type": "string"},
                        "start": {"type": ["string", "null"]},
                        "end": {"type": ["string", "null"]},
                        "is_current": {"type": "boolean"},
                        "areas": {"type": "array", "items": {"type": "string"}},
                        "technologies": {"type": "array", "items": {"type": "string"}},
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
                    "required": [
                        "institution", "location", "degree", "dates",
                        "start", "end", "details",
                    ],
                    "properties": {
                        "institution": {"type": "string"},
                        "location": {"type": ["string", "null"]},
                        "degree": {"type": "string"},
                        "dates": {"type": "string"},
                        "start": {"type": ["string", "null"]},
                        "end": {"type": ["string", "null"]},
                        "details": {"type": "array", "items": {"type": "string"}},
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
async def _call_ingestor_llm(client, model: str, messages: list[dict]) -> dict:
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_schema", "json_schema": JSON_SCHEMA},
        temperature=0,
    )
    return json.loads(response.choices[0].message.content)


async def ingest_cv_text(
    client,
    cv_text: str,
    known_links: dict[str, str] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    model_name = model or OPENAI_INGEST_MODEL
    hint = ""
    if known_links:
        hint = (
            "\n\n# LINKS CONOCIDOS (usalos directamente en `links.*`):\n"
            f"{json.dumps(known_links, ensure_ascii=False, indent=2)}"
        )
    user_prompt = (
        "# TEXTO DEL CV\n"
        f"{cv_text}"
        f"{hint}\n\n"
        "Extraé el JSON siguiendo el schema. No inventes."
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    return await _call_ingestor_llm(client, model_name, messages)
