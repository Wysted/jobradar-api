"""`visualizer-getonbrd`: process entry point. Pod wiring comes from env; the search input arrives
as `VISUALIZER_PARAMS` (see `scraper.SearchParams`)."""

import logging
import os
from pathlib import Path

from visualizer_sdk import run

from visualizer_getonbrd.scraper import getonbrd

DEFAULT_BASE_URL = "http://machine-api.visualizer.svc:8081"
DEFAULT_TOKEN_FILE = "/var/run/secrets/visualizer/token"  # noqa: S105 (a path, not a secret)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        run(
            getonbrd,
            base_url=os.environ.get("VISUALIZER_API_URL", DEFAULT_BASE_URL),
            token_file=Path(os.environ.get("VISUALIZER_TOKEN_FILE", DEFAULT_TOKEN_FILE)),
        )
    except (OSError, ValueError) as exc:
        raise SystemExit(f"visualizer-getonbrd: {exc}") from exc
