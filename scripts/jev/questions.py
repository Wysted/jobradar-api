"""JEV question set for the L3 rerank evaluation (jev/rerank-questions).

Questions are plain question dictionaries (`{"type": "score" | "noul" | "choice", ...}`),
which the typesafe-sdk accepts as-is (`typesafe_sdk.QuestionModel`). This keeps the
dry-run free of the SDK dependency and makes payloads JSON-serializable.

Rules (see .claude/agents/_shared/jev.md):
- instructions/criteria in English, one decision per question;
- numbers (salary, years) are compared in code, never asked to JEV;
- state carries only the profile/offer fields the questions need.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import config
from models.database.job import JobRecord
from models.database.profile import UserProfile

QUESTION_SET_VERSION = "v3"

# ---------------------------------------------------------------------------
# Score questions (composite scoring, weights in config.py)
# ---------------------------------------------------------------------------

TECH_FIT = "tech_fit"
AREA_FIT = "area_fit"
ROLE_FIT = "role_fit"

SCORE_QUESTIONS: dict[str, dict] = {
    TECH_FIT: {
        "type": "score",
        "instructions": (
            "How well do the technologies listed in the candidate profile cover "
            "the technologies that the job offer requires?"
        ),
        "criteria": [
            "The job offer is built on technologies that the candidate does not list at all",
            "The candidate lists a few secondary technologies of the offer, but not its core language or framework",
            "The candidate lists the core language or framework of the offer, but several other required technologies are missing",
            "The candidate lists nearly every technology that the job offer requires",
        ],
    },
    AREA_FIT: {
        "type": "score",
        "instructions": (
            "How well does the main area of work of the job offer match the "
            "professional areas listed in the candidate profile?"
        ),
        "criteria": [
            "The main area of the job offer is unrelated to every area in the candidate profile",
            "The main area of the job offer is adjacent to an area in the candidate profile, sharing only some tasks",
            "The main area of the job offer is one of the areas listed in the candidate profile",
        ],
    },
    ROLE_FIT: {
        "type": "score",
        "instructions": (
            "How closely does the role described in the job offer match the roles "
            "the candidate is targeting?"
        ),
        "criteria": [
            "The role is a different kind of job from the roles the candidate targets",
            "The role partially overlaps with a targeted role but its focus is different",
            "The role is essentially one of the roles the candidate targets",
        ],
    },
}

# ---------------------------------------------------------------------------
# Choice questions (classify the offer; the comparison with the profile is code)
# Choice is biased towards the first option: the first label is the neutral one.
# ---------------------------------------------------------------------------

OFFER_SENIORITY = "offer_seniority"
OFFER_WORK_MODE = "offer_work_mode"

SENIORITY_NOT_STATED = "not_stated"
SENIORITY_LEVELS = ["trainee", "junior", "semi_senior", "senior", "lead"]

OFFER_SENIORITY_QUESTION: dict = {
    "type": "choice",
    "instructions": "What seniority level does the job offer ask for?",
    "criteria": {
        SENIORITY_NOT_STATED: "The offer does not state or imply a seniority level",
        "trainee": "Internship, trainee or student position",
        "junior": "Junior or entry-level position",
        "semi_senior": "Semi-senior or mid-level position",
        "senior": "Senior position",
        "lead": "Lead, staff, architect or management position",
    },
}

WORK_MODE_NOT_STATED = "not_stated"
WORK_MODE_LABELS = ["remote", "hybrid", "onsite"]

OFFER_WORK_MODE_QUESTION: dict = {
    "type": "choice",
    "instructions": "What work mode does the job offer require?",
    "criteria": {
        WORK_MODE_NOT_STATED: "The offer does not state the work mode",
        "remote": "Fully remote work",
        "hybrid": "Hybrid work: some days remote and some days at the office",
        "onsite": "Work at the office or on site every day",
    },
}

# ---------------------------------------------------------------------------
# Noul questions (blockers; only asked when the profile field is non-empty)
# ---------------------------------------------------------------------------

EXCLUDED_ROLE = "excluded_role"
EXCLUDED_INDUSTRY = "excluded_industry"
DEAL_BREAKER = "deal_breaker"
LANGUAGE_GAP = "language_gap"
ONSITE_ELSEWHERE = "onsite_elsewhere"

NOUL_QUESTIONS: dict[str, dict] = {
    EXCLUDED_ROLE: {
        "type": "noul",
        "instructions": "Is the role of the job offer one of the roles the candidate excludes?",
        "criteria": {
            "true": "The offered role matches an entry in the candidate's excluded roles",
            "false": "The offered role matches none of the candidate's excluded roles",
        },
    },
    EXCLUDED_INDUSTRY: {
        "type": "noul",
        "instructions": "Does the hiring company operate in one of the industries the candidate excludes?",
        "criteria": {
            "true": "The company's industry matches an entry in the candidate's excluded industries",
            "false": "The company's industry matches none of the excluded industries, or it is not stated",
        },
    },
    DEAL_BREAKER: {
        "type": "noul",
        "instructions": "Does the job offer contain any of the candidate's deal breakers?",
        "criteria": {
            "true": "The offer explicitly includes a condition listed in the candidate's deal breakers",
            "false": "The offer includes none of the candidate's deal breakers",
        },
    },
    LANGUAGE_GAP: {
        "type": "noul",
        "instructions": (
            "Does the job offer require a spoken language that the candidate "
            "does not speak at the required level?"
        ),
        "criteria": {
            "true": "The offer requires a language that is missing from the candidate's languages, or a higher level than the candidate has",
            "false": "Every language the offer requires is in the candidate's languages at a sufficient level, or the offer requires no specific language",
        },
    },
    ONSITE_ELSEWHERE: {
        "type": "noul",
        "instructions": (
            "Does the job offer require working on site in a city outside the "
            "candidate's metropolitan area?"
        ),
        "criteria": {
            "true": "The offer requires on-site or hybrid presence in a different city or metropolitan area than the candidate's location",
            "false": "The offer is remote, or its office is in the candidate's city or metropolitan area, or the location is not stated",
        },
    },
}

# Noul key -> profile field that must be non-empty for the question to be asked.
NOUL_PROFILE_FIELDS: dict[str, str] = {
    EXCLUDED_ROLE: "roles_excluded",
    EXCLUDED_INDUSTRY: "industries_excluded",
    DEAL_BREAKER: "deal_breakers",
    LANGUAGE_GAP: "languages",
}

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def _names(items: list | None) -> list[str]:
    """Areas/technologies are `{name, seniority}` objects; JEV only needs names."""
    out: list[str] = []
    for item in items or []:
        if isinstance(item, dict) and item.get("name"):
            out.append(str(item["name"]))
        elif isinstance(item, str):
            out.append(item)
    return out


def _languages(items: list | None) -> list[str]:
    out: list[str] = []
    for item in items or []:
        if isinstance(item, dict) and item.get("name"):
            level = item.get("level")
            out.append(f"{item['name']} ({level})" if level else str(item["name"]))
        elif isinstance(item, str):
            out.append(item)
    return out


def _relocation_blocked(payload: dict) -> bool:
    location = payload.get("location") or {}
    return bool(location.get("city")) and not location.get("willing_to_relocate", True)


def build_candidate_state(profile: UserProfile) -> dict:
    """Minimal candidate view. Fields mirror llm_reranker._profile_payload, minus
    numeric fields (total_years, salary) that are compared in code."""
    payload = profile.payload or {}
    location = payload.get("location") or {}
    candidate: dict = {
        "headline": payload.get("headline"),
        "areas": _names(payload.get("areas")),
        "technologies": _names(payload.get("technologies")),
        "roles_target": payload.get("roles_target") or [],
    }
    optional = {
        "roles_excluded": payload.get("roles_excluded") or [],
        "industries_excluded": payload.get("industries_excluded") or [],
        "deal_breakers": payload.get("deal_breakers") or [],
        "languages": _languages(payload.get("languages")),
    }
    candidate.update({k: v for k, v in optional.items() if v})
    if _relocation_blocked(payload):
        candidate["location"] = ", ".join(
            str(v) for v in (location.get("city"), location.get("country")) if v
        )
    return candidate


# Headers that open the requirements section. Only header-like lines match
# (line start, short line ending in ':' or newline) to skip prose and EEO text.
_REQUIREMENTS_MARKERS = (
    "requisitos", "requerimientos", "lo que buscamos", "perfil buscado", "qué buscamos",
    "requirements", "qualifications", "what you'll bring", "what we're looking for",
    "you're the engineer", "the bits and bytes", "must have",
)
_REQUIREMENTS_HEADER = re.compile(
    r"(?:^|\n)[ \t\-*\u2022]*(?:[^\n]{0,30}?)(?:"
    + "|".join(re.escape(m) for m in _REQUIREMENTS_MARKERS)
    + r")[^\n]{0,60}(?::|\n)",
    re.IGNORECASE,
)
_EXCERPT_SEPARATOR = "\n[...]\n"


def description_excerpt(description: str | None) -> str:
    """Head of the offer plus its requirements section, within the char budget.

    v1 sent the first JEV_DESCRIPTION_MAX_CHARS chars, which for long offers is
    company boilerplate only (requirements start after ~2-4k chars)."""
    text = (description or "").strip()
    budget = config.JEV_DESCRIPTION_MAX_CHARS
    head_chars = config.JEV_DESCRIPTION_HEAD_CHARS
    if len(text) <= budget:
        return text
    normalized = text.replace("\u2019", "'")  # same length: positions stay valid
    match = _REQUIREMENTS_HEADER.search(normalized, head_chars)
    if match is None or match.start() < budget - head_chars:
        return text[:budget]  # requirements already inside the plain prefix (or not found)
    start = match.start() + (1 if normalized[match.start()] == "\n" else 0)
    tail_budget = budget - head_chars - len(_EXCERPT_SEPARATOR)
    return text[:head_chars] + _EXCERPT_SEPARATOR + text[start:start + tail_budget]


def build_offer_state(job: JobRecord) -> dict:
    return {
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "tags": sorted(job.tags or []),
        "description": description_excerpt(job.description),
    }


def build_state(profile: UserProfile, job: JobRecord) -> dict:
    return {"candidate": build_candidate_state(profile), "job_offer": build_offer_state(job)}


def _reversed_choice(question: dict) -> dict:
    """Same Choice with options in reverse order (first-option bias check)."""
    return {**question, "criteria": dict(reversed(list(question["criteria"].items())))}


def build_questions(profile: UserProfile, *, reverse_choices: bool = False) -> dict[str, dict]:
    """Question set for one profile. Blocker questions are skipped when the
    profile field they depend on is empty (nothing to compare against)."""
    payload = profile.payload or {}
    choice = _reversed_choice if reverse_choices else (lambda question: question)
    questions: dict[str, dict] = dict(SCORE_QUESTIONS)
    questions[OFFER_SENIORITY] = choice(OFFER_SENIORITY_QUESTION)
    if payload.get("work_mode"):
        questions[OFFER_WORK_MODE] = choice(OFFER_WORK_MODE_QUESTION)
    for key, profile_field in NOUL_PROFILE_FIELDS.items():
        if payload.get(profile_field):
            questions[key] = NOUL_QUESTIONS[key]
    if _relocation_blocked(payload):
        questions[ONSITE_ELSEWHERE] = NOUL_QUESTIONS[ONSITE_ELSEWHERE]
    return questions


# ---------------------------------------------------------------------------
# Code-side numeric checks (salary, years)
# ---------------------------------------------------------------------------

_YEARS_PATTERNS = [
    re.compile(r"(\d{1,2})\s*\+?\s*(?:[-–]\s*\d{1,2}\s*)?años?\s+de\s+experiencia", re.IGNORECASE),
    re.compile(r"(?:al\s+menos|m[ií]nimo(?:\s+de)?)\s+(\d{1,2})\s+años?", re.IGNORECASE),
    # "5+ años en desarrollo": the explicit "+" marks a requirement, not company age.
    re.compile(r"(\d{1,2})\s*\+\s*años?\b", re.IGNORECASE),
    re.compile(r"(\d{1,2})\s*\+?\s*(?:[-–]\s*\d{1,2}\s*)?years?\s+(?:of\s+)?(?:\w+\s+){0,3}experience", re.IGNORECASE),
    re.compile(r"(?:at\s+least|minimum(?:\s+of)?)\s+(\d{1,2})\s+years?", re.IGNORECASE),
]
_MAX_PLAUSIBLE_YEARS = 20

# CLP amounts: "$1.200.000", "$ 900.000", "1.500.000 líquidos".
_CLP_PATTERNS = [
    re.compile(r"\$\s?(\d{1,3}(?:\.\d{3}){1,2})(?!\d)"),
    re.compile(r"(\d{1,3}(?:\.\d{3}){2})\s*(?:clp|pesos|l[ií]quidos?|brutos?)", re.IGNORECASE),
]
_MIN_PLAUSIBLE_MONTHLY_CLP = 300_000
_MAX_PLAUSIBLE_MONTHLY_CLP = 20_000_000


def extract_required_years(description: str) -> int | None:
    """Smallest explicit years-of-experience requirement (conservative)."""
    found: list[int] = []
    for pattern in _YEARS_PATTERNS:
        for match in pattern.finditer(description or ""):
            years = int(match.group(1))
            if 0 < years <= _MAX_PLAUSIBLE_YEARS:
                found.append(years)
    return min(found) if found else None


def extract_max_salary_clp(description: str) -> int | None:
    """Largest plausible monthly CLP amount mentioned in the offer."""
    found: list[int] = []
    for pattern in _CLP_PATTERNS:
        for match in pattern.finditer(description or ""):
            amount = int(match.group(1).replace(".", ""))
            if _MIN_PLAUSIBLE_MONTHLY_CLP <= amount <= _MAX_PLAUSIBLE_MONTHLY_CLP:
                found.append(amount)
    return max(found) if found else None


def candidate_seniority(total_years: float | None) -> str:
    years = float(total_years or 0)
    if years < 1:
        return "trainee"
    if years < 3:
        return "junior"
    if years < 6:
        return "semi_senior"
    if years < 10:
        return "senior"
    return "lead"


_SENIORITY_FIT_BY_DISTANCE = {0: 1.0, 1: 0.5}
_SENIORITY_FIT_NOT_STATED = 0.75


def seniority_fit(probabilities: dict[str, float], candidate_level: str) -> float:
    """Expected fit over the Choice distribution; distance between levels in code."""
    cand_idx = SENIORITY_LEVELS.index(candidate_level)
    fit = 0.0
    for label, prob in probabilities.items():
        if label == SENIORITY_NOT_STATED:
            fit += prob * _SENIORITY_FIT_NOT_STATED
            continue
        distance = abs(SENIORITY_LEVELS.index(label) - cand_idx)
        fit += prob * _SENIORITY_FIT_BY_DISTANCE.get(distance, 0.0)
    return fit


def _normalize_work_mode(value: str) -> str | None:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    if "remot" in text or "teletrabajo" in text:
        return "remote"
    if "hibrid" in text or "hybrid" in text:
        return "hybrid"
    if "presencial" in text or "onsite" in text or "on-site" in text or "office" in text:
        return "onsite"
    return None


# ---------------------------------------------------------------------------
# Decision (simulated cascade, used by the evaluation only)
# ---------------------------------------------------------------------------

DECISION_DISCARD = "discard"
DECISION_BLOCKED = "blocked"
DECISION_ESCALATE = "escalate"


@dataclass
class CodeSignals:
    required_years: int | None
    max_salary_clp: int | None
    candidate_years: float
    candidate_level: str
    min_salary_clp: int | None
    blockers: list[str] = field(default_factory=list)


def code_signals(profile: UserProfile, job: JobRecord) -> CodeSignals:
    payload = profile.payload or {}
    salary = payload.get("salary") or {}
    candidate_years = float(payload.get("total_years") or 0)
    min_salary = salary.get("min_monthly") if salary.get("currency") == "CLP" else None
    signals = CodeSignals(
        required_years=extract_required_years(job.description or ""),
        max_salary_clp=extract_max_salary_clp(job.description or ""),
        candidate_years=candidate_years,
        candidate_level=candidate_seniority(candidate_years),
        min_salary_clp=min_salary,
    )
    if signals.max_salary_clp and min_salary and signals.max_salary_clp < min_salary:
        signals.blockers.append("salary_below_min")
    return signals


def upper_level(probabilities: dict[str, float], quantile: float) -> int:
    """Smallest Score level whose cumulative probability reaches `quantile`."""
    cumulative = 0.0
    levels = sorted(probabilities, key=int)
    for level in levels:
        cumulative += probabilities[level]
        if cumulative >= quantile:
            return int(level)
    return int(levels[-1])


def _composite(normalized: dict[str, float], sen_fit: float) -> float:
    total_weight = (
        config.JEV_WEIGHT_TECH + config.JEV_WEIGHT_AREA
        + config.JEV_WEIGHT_ROLE + config.JEV_WEIGHT_SENIORITY
    )
    return 100 * (
        config.JEV_WEIGHT_TECH * normalized[TECH_FIT]
        + config.JEV_WEIGHT_AREA * normalized[AREA_FIT]
        + config.JEV_WEIGHT_ROLE * normalized[ROLE_FIT]
        + config.JEV_WEIGHT_SENIORITY * sen_fit
    ) / total_weight


@dataclass
class Evaluation:
    jev_score: float
    optimistic_score: float
    min_score_confidence: float
    seniority_fit: float
    decision: str
    reasons: list[str]


def evaluate(answers: dict, profile: UserProfile, signals: CodeSignals) -> Evaluation:
    """Combine JEV answers (plain dicts: `{"type", "score"/"noul"/"choice",
    "confidence", "probabilities"}`) and code signals into a routing decision."""
    payload = profile.payload or {}
    normalized = {
        key: answers[key]["score"] / (len(SCORE_QUESTIONS[key]["criteria"]) - 1)
        for key in SCORE_QUESTIONS
    }
    sen_fit = seniority_fit(answers[OFFER_SENIORITY]["probabilities"], signals.candidate_level)
    years_gap_blocked = (
        signals.required_years is not None
        and signals.required_years > signals.candidate_years + config.JEV_YEARS_GAP_TOLERANCE
    )
    if years_gap_blocked:
        sen_fit = min(sen_fit, config.JEV_YEARS_GAP_SENIORITY_FIT)  # soft penalty, not a gate

    jev_score = round(_composite(normalized, sen_fit), 1)
    # Optimistic composite: each Score at its upper quantile. Discarding only when
    # even this is low is the confidence gate (per-Score argmax confidence is low
    # whenever mass splits between two bad levels, which blocked obvious discards).
    optimistic = {
        key: upper_level(answers[key]["probabilities"], config.JEV_DISCARD_UPPER_QUANTILE)
        / (len(SCORE_QUESTIONS[key]["criteria"]) - 1)
        for key in SCORE_QUESTIONS
    }
    optimistic_score = round(_composite(optimistic, sen_fit), 1)
    min_conf = min(answers[key]["confidence"] for key in SCORE_QUESTIONS)

    reasons = list(signals.blockers)
    soft_reasons: list[str] = []
    for key in NOUL_QUESTIONS:
        if key in answers and answers[key]["noul"] >= config.JEV_BLOCKER_NOUL_THRESHOLD:
            if key == LANGUAGE_GAP and not config.JEV_LANGUAGE_GAP_BLOCKS:
                soft_reasons.append(f"{key}_soft")  # informative: the LLM decides
            else:
                reasons.append(key)

    profile_modes = {m for m in (_normalize_work_mode(v) for v in payload.get("work_mode") or []) if m}
    work_mode = answers.get(OFFER_WORK_MODE)
    if (
        work_mode
        and profile_modes
        and work_mode["choice"] in WORK_MODE_LABELS
        and work_mode["choice"] not in profile_modes
        and work_mode["confidence"] >= config.JEV_CHOICE_BLOCKER_CONFIDENCE
    ):
        reasons.append("work_mode_mismatch")

    if reasons:
        decision = DECISION_BLOCKED
    elif optimistic_score < config.JEV_DISCARD_SCORE:
        decision = DECISION_DISCARD
        reasons.append("low_composite")
    else:
        decision = DECISION_ESCALATE
    if years_gap_blocked:
        reasons.append("years_gap")  # informative: lowers seniority fit, not a hard block
    reasons.extend(soft_reasons)

    return Evaluation(
        jev_score=jev_score,
        optimistic_score=optimistic_score,
        min_score_confidence=round(min_conf, 3),
        seniority_fit=round(sen_fit, 3),
        decision=decision,
        reasons=reasons,
    )
