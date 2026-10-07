"""Evaluate TypeSafe JEV against existing L3 rerank scores (job_scores.llm_score).

Does NOT touch the ranking pipeline. Two modes:

  --dry-run  Build state + questions for N existing scores, estimate input tokens
             (tiktoken approximation) and cost, write payloads to
             output/jev/dry_run.jsonl. No API key or SDK needed.
  (default)  Call AsyncTypeSafeClient once per offer, compute metrics
             (Spearman vs llm_score, false-discard rate for llm_score >= 70,
             latency, tokens, cost) and write a CSV + summary JSON to output/jev/.

Usage (inside the api container):
  docker compose exec api python scripts/jev/eval_rerank.py --dry-run --limit 20
  docker compose exec api python scripts/jev/eval_rerank.py --limit 20
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

import config  # noqa: E402
from database import async_session  # noqa: E402
from models.database.job_score import JobScore  # noqa: E402
from scripts.jev import questions as q  # noqa: E402

OUTPUT_DIR = BACKEND_ROOT / "output" / "jev"
DRY_RUN_FILE = "dry_run.jsonl"
DEFAULT_LIMIT = 20
# Offers the LLM rated as strong fits; discarding them is the main risk.
STRONG_FIT_LLM_SCORE = 70
TOKENS_PER_MTOK = 1_000_000
# Fallback when tiktoken cannot load an encoding (offline container).
CHARS_PER_TOKEN_FALLBACK = 4
TIKTOKEN_ENCODING = "o200k_base"
MS_PER_SECOND = 1000
P95 = 0.95


@dataclass
class Item:
    score: JobScore
    state: dict
    questions: dict[str, dict]
    signals: q.CodeSignals

    @property
    def request(self) -> dict:
        return {"model": config.JEV_MODEL, "state": self.state, "questions": self.questions}


@dataclass
class Row:
    score_id: str
    job_id: str
    title: str
    company: str
    llm_score: int
    jev_score: float | None = None
    optimistic_score: float | None = None
    decision: str | None = None
    reasons: str = ""
    min_score_confidence: float | None = None
    seniority_fit: float | None = None
    required_years: int | None = None
    max_salary_clp: int | None = None
    latency_ms: float | None = None
    input_tokens: int | None = None
    model: str | None = None
    error: str | None = None
    answers: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


async def _load_items(limit: int, reverse_choices: bool = False) -> list[Item]:
    async with async_session() as session:
        result = await session.execute(
            select(JobScore).order_by(JobScore.scored_at.desc()).limit(limit)
        )
        scores = list(result.scalars().all())

    items: list[Item] = []
    for score in scores:
        items.append(
            Item(
                score=score,
                state=q.build_state(score.profile, score.job),
                questions=q.build_questions(score.profile, reverse_choices=reverse_choices),
                signals=q.code_signals(score.profile, score.job),
            )
        )
    return items


# ---------------------------------------------------------------------------
# Token / cost estimation
# ---------------------------------------------------------------------------


def _token_counter():
    try:
        import tiktoken

        encoding = tiktoken.get_encoding(TIKTOKEN_ENCODING)
        return lambda text: len(encoding.encode(text)), f"tiktoken:{TIKTOKEN_ENCODING}"
    except Exception as exc:  # noqa: BLE001 — estimation only, degrade to chars/4
        print(f"! tiktoken unavailable ({type(exc).__name__}), using chars/{CHARS_PER_TOKEN_FALLBACK}")
        return lambda text: len(text) // CHARS_PER_TOKEN_FALLBACK, f"chars/{CHARS_PER_TOKEN_FALLBACK}"


def _cost_usd(tokens: float) -> float:
    return tokens / TOKENS_PER_MTOK * config.JEV_PRICE_PER_MTOK_INPUT


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2 + 1  # average rank for ties
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    try:
        return statistics.correlation(rx, ry)
    except statistics.StatisticsError:
        return None  # constant input


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(pct * len(ordered)))]


def _summary(rows: list[Row], elapsed_s: float, top_k: int) -> dict:
    ok = [r for r in rows if r.error is None]
    strong = [r for r in ok if r.llm_score >= STRONG_FIT_LLM_SCORE]
    strong_dropped = [r for r in strong if r.decision != q.DECISION_ESCALATE]
    tokens = [r.input_tokens for r in ok if r.input_tokens is not None]
    latencies = [r.latency_ms for r in ok if r.latency_ms is not None]
    decisions = {d: sum(1 for r in ok if r.decision == d) for d in
                 (q.DECISION_ESCALATE, q.DECISION_DISCARD, q.DECISION_BLOCKED)}
    avg_tokens = statistics.mean(tokens) if tokens else 0.0
    rho = spearman([r.jev_score for r in ok], [float(r.llm_score) for r in ok])
    return {
        "question_set": q.QUESTION_SET_VERSION,
        "model_requested": config.JEV_MODEL,
        "models_returned": sorted({r.model for r in ok if r.model}),
        "n": len(rows),
        "n_ok": len(ok),
        "n_errors": len(rows) - len(ok),
        "spearman_vs_llm_score": round(rho, 4) if rho is not None else None,
        "strong_fit_threshold": STRONG_FIT_LLM_SCORE,
        "n_strong_fit": len(strong),
        "false_discards": len(strong_dropped),
        "false_discard_rate": round(len(strong_dropped) / len(strong), 4) if strong else None,
        "false_discard_ids": [r.score_id for r in strong_dropped],
        "decisions": decisions,
        "escalation_rate": round(decisions[q.DECISION_ESCALATE] / len(ok), 4) if ok else None,
        "latency_ms_p50": round(statistics.median(latencies), 1) if latencies else None,
        "latency_ms_p95": round(_percentile(latencies, P95), 1) if latencies else None,
        "wall_time_s": round(elapsed_s, 2),
        "input_tokens_total": sum(tokens),
        "input_tokens_avg": round(avg_tokens, 1),
        "cost_usd_total": round(_cost_usd(sum(tokens)), 6),
        "cost_usd_per_rank_run": round(_cost_usd(avg_tokens * top_k), 6),
        "rank_top_k": top_k,
        "thresholds": {
            "blocker_noul": config.JEV_BLOCKER_NOUL_THRESHOLD,
            "choice_blocker_confidence": config.JEV_CHOICE_BLOCKER_CONFIDENCE,
            "discard_score": config.JEV_DISCARD_SCORE,
            "discard_upper_quantile": config.JEV_DISCARD_UPPER_QUANTILE,
            "years_gap_tolerance": config.JEV_YEARS_GAP_TOLERANCE,
        },
        "weights": {
            "tech": config.JEV_WEIGHT_TECH,
            "area": config.JEV_WEIGHT_AREA,
            "role": config.JEV_WEIGHT_ROLE,
            "seniority": config.JEV_WEIGHT_SENIORITY,
        },
    }


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


async def _dry_run(limit: int, top_k: int) -> None:
    items = await _load_items(limit)
    if not items:
        raise ValueError("no job_scores found")

    count_tokens, method = _token_counter()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / DRY_RUN_FILE

    token_counts: list[int] = []
    with out_path.open("w", encoding="utf-8") as fh:
        for item in items:
            body = json.dumps(item.request, ensure_ascii=False)
            est_tokens = count_tokens(body)
            token_counts.append(est_tokens)
            fh.write(json.dumps({
                "score_id": str(item.score.id),
                "job_id": str(item.score.job_id),
                "llm_score": item.score.llm_score,
                "est_input_tokens": est_tokens,
                "code_signals": asdict(item.signals),
                "request": item.request,
            }, ensure_ascii=False) + "\n")

    avg_tokens = statistics.mean(token_counts)
    summary = {
        "mode": "dry_run",
        "n": len(items),
        "token_estimator": method,
        "questions_per_request": sorted(items[0].questions),
        "input_tokens_avg": round(avg_tokens, 1),
        "input_tokens_min": min(token_counts),
        "input_tokens_max": max(token_counts),
        "input_tokens_total": sum(token_counts),
        "price_per_mtok_input_usd": config.JEV_PRICE_PER_MTOK_INPUT,
        "est_cost_usd_n": round(_cost_usd(sum(token_counts)), 6),
        "rank_top_k": top_k,
        "est_cost_usd_per_rank_run": round(_cost_usd(avg_tokens * top_k), 6),
        "n_strong_fit": sum(1 for i in items if i.score.llm_score >= STRONG_FIT_LLM_SCORE),
        "code_blockers": sum(1 for i in items if i.signals.blockers),
        "offers_with_required_years": sum(1 for i in items if i.signals.required_years is not None),
        "offers_with_salary": sum(1 for i in items if i.signals.max_salary_clp is not None),
        "output": str(out_path.relative_to(BACKEND_ROOT)),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Real run
# ---------------------------------------------------------------------------


def _require_sdk():
    if not config.TYPESAFE_API_KEY.strip():
        raise RuntimeError(
            "TYPESAFE_API_KEY is not set. Add it to backend/.env (create one at "
            "https://console.typesafe.ai/), recreate the api container "
            "(docker compose up -d api) and re-run. Use --dry-run to estimate cost without it."
        )
    try:
        import typesafe_sdk
    except ImportError as exc:
        raise RuntimeError(
            "typesafe-sdk is not installed. Rebuild the image (it is in requirements.txt) "
            "or run: docker compose exec api pip install typesafe-sdk"
        ) from exc
    return typesafe_sdk


async def _ask(client, item: Item, semaphore: asyncio.Semaphore) -> Row:
    score = item.score
    row = Row(
        score_id=str(score.id),
        job_id=str(score.job_id),
        title=score.job.title,
        company=score.job.company,
        llm_score=score.llm_score,
        required_years=item.signals.required_years,
        max_salary_clp=item.signals.max_salary_clp,
    )
    async with semaphore:
        started = time.perf_counter()
        try:
            resp = await client.system_one(
                state=item.state, questions=item.questions, model=config.JEV_MODEL
            )
        except Exception as exc:  # noqa: BLE001 — per-offer failure is recorded, run continues
            row.error = f"{type(exc).__name__}: {exc}"
            return row
        row.latency_ms = round((time.perf_counter() - started) * MS_PER_SECOND, 1)

    answers = {key: answer.model_dump() for key, answer in resp.answers.items()}
    try:
        evaluation = q.evaluate(answers, score.profile, item.signals)
    except (KeyError, ValueError, ZeroDivisionError) as exc:
        row.error = f"evaluate {type(exc).__name__}: {exc}"
        return row
    row.jev_score = evaluation.jev_score
    row.optimistic_score = evaluation.optimistic_score
    row.decision = evaluation.decision
    row.reasons = ",".join(evaluation.reasons)
    row.min_score_confidence = evaluation.min_score_confidence
    row.seniority_fit = evaluation.seniority_fit
    row.input_tokens = resp.usage.input_tokens
    row.model = resp.model
    row.answers = answers
    return row


def _answer_columns(row: Row) -> dict:
    cols: dict = {}
    for key, ans in row.answers.items():
        kind = ans.get("type")
        if kind == "score":
            cols[f"{key}_score"] = round(ans["score"], 3)
            cols[f"{key}_conf"] = round(ans["confidence"], 3)
        elif kind == "noul":
            cols[f"{key}_noul"] = round(ans["noul"], 3)
        elif kind == "choice":
            cols[f"{key}_choice"] = ans["choice"]
            cols[f"{key}_conf"] = round(ans["confidence"], 3)
    return cols


def _write_csv(rows: list[Row], path: Path) -> None:
    base = [{k: v for k, v in asdict(r).items() if k != "answers"} | _answer_columns(r) for r in rows]
    fieldnames: list[str] = []
    for record in base:
        fieldnames.extend(k for k in record if k not in fieldnames)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(base)


def _write_answers(rows: list[Row], items: list[Item], path: Path) -> None:
    """Raw JEV answers (with probabilities) + code signals, for offline re-scoring."""
    signals_by_id = {str(item.score.id): asdict(item.signals) for item in items}
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps({
                "score_id": row.score_id,
                "title": row.title,
                "llm_score": row.llm_score,
                "error": row.error,
                "input_tokens": row.input_tokens,
                "latency_ms": row.latency_ms,
                "model": row.model,
                "code_signals": signals_by_id.get(row.score_id),
                "answers": row.answers,
            }, ensure_ascii=False) + "\n")


async def _real_run(limit: int, top_k: int, reverse_choices: bool) -> None:
    sdk = _require_sdk()
    items = await _load_items(limit, reverse_choices)
    if not items:
        raise ValueError("no job_scores found")

    semaphore = asyncio.Semaphore(config.JEV_MAX_CONCURRENCY)
    started = time.perf_counter()
    async with sdk.AsyncTypeSafeClient(api_key=config.TYPESAFE_API_KEY, model=config.JEV_MODEL) as client:
        rows = await asyncio.gather(*(_ask(client, item, semaphore) for item in items))
    elapsed = time.perf_counter() - started

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    csv_path = OUTPUT_DIR / f"eval_rerank_{stamp}.csv"
    summary_path = OUTPUT_DIR / f"eval_rerank_{stamp}_summary.json"
    answers_path = OUTPUT_DIR / f"eval_rerank_{stamp}_answers.jsonl"
    _write_csv(list(rows), csv_path)
    _write_answers(list(rows), items, answers_path)
    summary = _summary(list(rows), elapsed, top_k)
    summary["reverse_choice_options"] = reverse_choices
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    for row in rows:
        if row.error:
            print(f"! {row.score_id} {row.error}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"✓ CSV: {csv_path.relative_to(BACKEND_ROOT)}")
    print(f"✓ Summary: {summary_path.relative_to(BACKEND_ROOT)}")
    print(f"✓ Answers: {answers_path.relative_to(BACKEND_ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate TypeSafe JEV vs job_scores.llm_score (L3 rerank)")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="number of job_scores to evaluate (most recent first)")
    parser.add_argument("--top-k", type=int, default=config.RANK_TOP_K, help="offers per rank run, for the per-run cost estimate (default: RANK_TOP_K)")
    parser.add_argument("--dry-run", action="store_true", help="build payloads and estimate tokens/cost without calling JEV")
    parser.add_argument("--reverse-choice-options", action="store_true", help="reverse Choice option order (first-option bias check)")
    args = parser.parse_args()

    try:
        if args.dry_run:
            asyncio.run(_dry_run(args.limit, args.top_k))
        else:
            asyncio.run(_real_run(args.limit, args.top_k, args.reverse_choice_options))
    except Exception as exc:
        print(f"✗ {type(exc).__name__}: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
