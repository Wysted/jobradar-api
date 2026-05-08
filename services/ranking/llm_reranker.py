import json
import logging
from pathlib import Path

from tenacity import retry

from dependencies.openai_client import RERANK_MODEL
from models.database.job import JobRecord
from models.database.profile import UserProfile
from models.ranking import LLMScoreOutput
from services.retry import openai_retry_policy

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt (batch v2 — loaded once at import time)
# ---------------------------------------------------------------------------

_PROMPT_PATH = Path(__file__).parent / "prompts" / "rerank_v2.md"
_PROMPT_TEXT: str = _PROMPT_PATH.read_text(encoding="utf-8")

_SYSTEM_MARKER = "## System\n"
_USER_MARKER = "## User\n"


def _parse_prompt(raw: str) -> tuple[str, str]:
    sys_start = raw.index(_SYSTEM_MARKER) + len(_SYSTEM_MARKER)
    user_start = raw.index(_USER_MARKER)
    system = raw[sys_start:user_start].strip()
    user_template = raw[user_start + len(_USER_MARKER):].strip()
    return system, user_template


_SYSTEM_PROMPT, _USER_TEMPLATE = _parse_prompt(_PROMPT_TEXT)

# ---------------------------------------------------------------------------
# JSON Schema — batch: results[] with job index
# OpenAI strict mode requires a top-level object, so we wrap the array.
# ---------------------------------------------------------------------------

_RESULT_ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "index",
        "score_0_100",
        "matched_areas",
        "matched_techs",
        "missing_skills",
        "red_flags",
        "pitch_angles",
        "reasoning",
    ],
    "properties": {
        "index": {"type": "integer", "minimum": 0},
        "score_0_100": {"type": "integer", "minimum": 0, "maximum": 100},
        "matched_areas": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "profile_sen", "job_sen"],
                "properties": {
                    "name": {"type": "string"},
                    "profile_sen": {"type": "integer", "minimum": 1, "maximum": 5},
                    "job_sen": {"type": "integer", "minimum": 1, "maximum": 5},
                },
            },
        },
        "matched_techs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "profile_sen", "job_sen"],
                "properties": {
                    "name": {"type": "string"},
                    "profile_sen": {"type": "integer", "minimum": 1, "maximum": 5},
                    "job_sen": {"type": "integer", "minimum": 1, "maximum": 5},
                },
            },
        },
        "missing_skills": {"type": "array", "items": {"type": "string"}},
        "red_flags": {"type": "array", "items": {"type": "string"}},
        "pitch_angles": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string"},
    },
}

RERANK_BATCH_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "RerankBatchResult",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["results"],
            "properties": {
                "results": {
                    "type": "array",
                    "items": _RESULT_ITEM_SCHEMA,
                },
            },
        },
    },
}

DEFAULT_BATCH_SIZE = 10


def _profile_payload(profile: UserProfile) -> dict:
    return {
        k: profile.payload.get(k)
        for k in [
            "headline", "total_years", "areas", "technologies", "soft_skills",
            "roles_target", "roles_excluded", "industries_preferred",
            "industries_excluded", "location", "work_mode", "salary",
            "languages", "contract_types", "company_size_pref", "deal_breakers",
        ]
    }


def _offer_payload(job: JobRecord, index: int) -> dict:
    return {
        "index": index,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "tags": sorted(job.tags or []),
        # Truncate description — boilerplate after ~2k chars adds noise without signal.
        "description": (job.description or "").strip()[:2000],
    }


def _build_messages(profile: UserProfile, jobs: list[JobRecord]) -> list[dict]:
    profile_data = _profile_payload(profile)
    offers = [_offer_payload(job, i) for i, job in enumerate(jobs)]
    user_content = _USER_TEMPLATE.format(
        profile_json=json.dumps(profile_data, sort_keys=True, ensure_ascii=False),
        offers_json=json.dumps(offers, ensure_ascii=False),
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


@retry(**openai_retry_policy)
async def _call_llm_batch(client, messages: list[dict]) -> list[dict]:
    response = await client.chat.completions.create(
        model=RERANK_MODEL,
        response_format=RERANK_BATCH_SCHEMA,
        messages=messages,
    )
    raw = json.loads(response.choices[0].message.content)
    return raw.get("results", [])


async def rerank_jobs(
    client,
    profile: UserProfile,
    jobs: list[JobRecord],
    *,
    min_score: float,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[tuple[JobRecord, LLMScoreOutput]]:
    """Score jobs against a profile using batched LLM calls.

    Returns list of `(job, output)` tuples, preserving the match between
    each score and its source job. Only results with `score_0_100 >= min_score`
    are returned.

    Cost: ~1 LLM call per `batch_size` jobs (vs 1-per-job before). The system
    prompt + profile are shared across the batch, which also benefits from
    automatic prompt caching on gpt-4o-mini for prefixes >1024 tokens.
    """
    results: list[tuple[JobRecord, LLMScoreOutput]] = []

    for chunk_start in range(0, len(jobs), batch_size):
        chunk = jobs[chunk_start : chunk_start + batch_size]
        messages = _build_messages(profile, chunk)

        try:
            raw_results = await _call_llm_batch(client, messages)
        except Exception as exc:
            logger.exception(
                "llm batch rerank failed chunk=%d-%d: %s",
                chunk_start, chunk_start + len(chunk), exc,
            )
            continue

        for item in raw_results:
            idx = item.get("index")
            if not isinstance(idx, int) or idx < 0 or idx >= len(chunk):
                logger.warning("rerank batch: invalid index=%r, skipping", idx)
                continue
            try:
                # LLMScoreOutput doesn't have `index` — strip it.
                clean = {k: v for k, v in item.items() if k != "index"}
                output = LLMScoreOutput(**clean)
            except Exception as exc:
                logger.warning("rerank batch: invalid item for index=%s: %s", idx, exc)
                continue
            if output.score_0_100 >= min_score:
                results.append((chunk[idx], output))

    return results
