from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.cv_generator.orchestrator import generate_one  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera un CV puntual por score_id")
    parser.add_argument("--score-id", required=True)
    args = parser.parse_args()

    try:
        path = asyncio.run(generate_one(UUID(args.score_id)))
        print(f"✓ CV generado: {path}")
    except Exception as exc:
        print(f"✗ {type(exc).__name__}: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
