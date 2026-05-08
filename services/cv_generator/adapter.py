import json
from typing import Any

from tenacity import retry

from config import OPENAI_CV_MODEL
from services.retry import openai_retry_policy

SYSTEM_PROMPT = """You are an expert technical CV writer.

INPUT: a candidate master profile, a job posting, and a matching score (all in Spanish).
OUTPUT LANGUAGE: Spanish (Chile). Every string you write MUST be in Spanish. Do NOT translate job titles, company names, or technology names.

Your task: produce a tailored CV following the schema. Rules:

1. `perfil` (summary): 3-5 lines highlighting `matched_techs` and `pitch_angles`. Align tone with the target role. Never invent experience not present in the master profile.

2. `experiencia`: select the MOST RELEVANT roles (all if applicable). For each:
   - Reorder achievements to prioritize those touching `matched_techs` / `matched_areas`.
   - Rewrite each bullet preserving the fact, emphasizing matched technologies.
   - Use Spanish first-person past tense verbs ("Desarrollé", "Implementé", "Diseñé").
   - 3-5 bullets per role — no more.
   - NEVER invent technologies the candidate does not know.

3. `habilidades` (CRITICAL RULE — read twice):
   The array MUST contain EXACTLY ONE entry per category. The `category` value MUST be UNIQUE across the array. NEVER emit two entries with the same `category`.
   The `items` field MUST be a SINGLE comma-separated string containing ALL technologies for that category.

   VALID OUTPUT (one entry per unique category, items joined with commas):
     [
       {"category": "Lenguajes", "items": "Python, JavaScript, TypeScript, Go, SQL"},
       {"category": "Backend", "items": "FastAPI, Node.js, Gin-Gonic"},
       {"category": "Frontend", "items": "React, Vue 3, Nuxt.js, Angular, HTML, CSS"},
       {"category": "Bases de Datos", "items": "PostgreSQL, MySQL, Redis"},
       {"category": "Herramientas", "items": "Docker, Git, GitHub, Playwright, Postman"},
       {"category": "Metodologías", "items": "Clean Architecture, SOLID, Microservicios, XP"}
     ]

   INVALID OUTPUT (repeated category, one tech per entry — NEVER DO THIS):
     [
       {"category": "Lenguajes", "items": "Python"},
       {"category": "Lenguajes", "items": "JavaScript"},
       {"category": "Backend", "items": "FastAPI"}
     ]

   Allowed categories (MAX 6, Spanish labels required): Lenguajes, Backend, Frontend, Bases de Datos, Herramientas, Metodologías.
   Within each category list `matched_techs` FIRST, then other relevant ones. Omit anything that does not add value to the role.
   Before returning, mentally verify: is every `category` value unique? If two entries share a category, MERGE them into one.

4. `educacion`: copy verbatim from the master profile.

Respond ALWAYS with valid JSON matching the schema. No extra text. All content in Spanish."""

JSON_SCHEMA = {
    "name": "cv_content",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["perfil", "experiencia", "habilidades", "educacion"],
        "properties": {
            "perfil": {"type": "string"},
            "experiencia": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["company", "location", "role", "dates", "bullets"],
                    "properties": {
                        "company": {"type": "string"},
                        "location": {"type": "string"},
                        "role": {"type": "string"},
                        "dates": {"type": "string"},
                        "bullets": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
            },
            "habilidades": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["category", "items"],
                    "properties": {
                        "category": {"type": "string"},
                        "items": {"type": "string"},
                    },
                },
            },
            "educacion": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["institution", "location", "degree", "dates", "details"],
                    "properties": {
                        "institution": {"type": "string"},
                        "location": {"type": ["string", "null"]},
                        "degree": {"type": "string"},
                        "dates": {"type": "string"},
                        "details": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
            },
        },
    },
}


def _profile_block(digest: dict[str, Any]) -> str:
    return (
        "# PERFIL CANDIDATO (DIGEST)\n"
        f"{json.dumps(digest, ensure_ascii=False, indent=2)}"
    )


def _job_block(job: dict[str, Any], score: dict[str, Any]) -> str:
    return (
        "# OFERTA LABORAL\n"
        f"Título: {job['title']}\n"
        f"Empresa: {job['company']}\n"
        f"Ubicación: {job['location']}\n"
        f"Fecha: {job.get('posted_date', '')}\n\n"
        "Descripción:\n"
        f"{job['description']}\n\n"
        "# SCORE DE MATCHING (output del ranker)\n"
        f"llm_score: {score['llm_score']}\n"
        f"matched_areas: {json.dumps(score['matched_areas'], ensure_ascii=False)}\n"
        f"matched_techs: {json.dumps(score['matched_techs'], ensure_ascii=False)}\n"
        f"missing_skills: {json.dumps(score['missing_skills'], ensure_ascii=False)}\n"
        f"pitch_angles: {json.dumps(score['pitch_angles'], ensure_ascii=False)}\n"
        f"reasoning: {score['reasoning']}\n\n"
        "Produce el CV adaptado en JSON siguiendo el schema."
    )


def _merge_skill_categories(
    habilidades: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, list[str]] = {}
    order: list[str] = []
    for entry in habilidades:
        category = entry.get("category", "").strip()
        items_raw = entry.get("items", "")
        if not category:
            continue
        if category not in merged:
            merged[category] = []
            order.append(category)
        for item in (s.strip() for s in items_raw.split(",")):
            if item and item.lower() not in {x.lower() for x in merged[category]}:
                merged[category].append(item)
    return [{"category": c, "items": ", ".join(merged[c])} for c in order]


@retry(**openai_retry_policy)
async def _call_adapter_llm(client, model: str, messages: list[dict]) -> dict:
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_schema", "json_schema": JSON_SCHEMA},
        temperature=0.4,
    )
    raw = response.choices[0].message.content
    return json.loads(raw)


async def adapt_profile_to_job(
    client,
    profile_digest: dict[str, Any],
    job: dict[str, Any],
    score: dict[str, Any],
    model: str | None = None,
) -> dict[str, Any]:
    model_name = model or OPENAI_CV_MODEL
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _profile_block(profile_digest)},
        {"role": "user", "content": _job_block(job, score)},
    ]
    data = await _call_adapter_llm(client, model_name, messages)
    data["habilidades"] = _merge_skill_categories(data.get("habilidades", []))
    return data
