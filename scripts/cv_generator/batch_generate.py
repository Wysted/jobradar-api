from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from database import async_session  # noqa: E402
from models.database.pipeline_run import KIND_CV_BATCH  # noqa: E402
from services import pipeline_runs  # noqa: E402
from services.cv_generator.orchestrator import generate_one, run_cv_batch  # noqa: E402


async def _run_single(score_id: UUID) -> None:
    path = await generate_one(score_id)
    print(f"✓ CV generado: {path}")


async def _run_batch(min_score: int, limit: int, force: bool) -> None:
    async with async_session() as session:
        run = await pipeline_runs.create_run(
            session, kind=KIND_CV_BATCH, trigger="cli"
        )
    stats = await run_cv_batch(run.id, min_score, limit, force)
    print(f"✓ Run {run.id} completado")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch CV generator (thin CLI wrapper)")
    parser.add_argument("--min-score", type=int, default=60)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--score-id", default=None)
    args = parser.parse_args()

    try:
        if args.score_id:
            asyncio.run(_run_single(UUID(args.score_id)))
        else:
            asyncio.run(_run_batch(args.min_score, args.limit, args.force))
    except Exception as exc:
        print(f"✗ {type(exc).__name__}: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
