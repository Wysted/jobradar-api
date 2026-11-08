"""Writes GetOnBoard jobs to `scraping_masive.jobs` with the `getonbrd_scraper` role (visualizer ADR 0019 and 0020).

The table shape comes from the backend's own models, so a schema change and this scraper change land in one repo. The role
may only SELECT `platforms`, SELECT and INSERT `jobs`, and UPDATE `jobs.description`
(backend/scripts/visualizer/create_scraper_role.sh): the upsert below uses nothing else.

Nothing here logs. An SQLAlchemy error carries the statement and its parameters (job text), so failures are re-raised
as a bare type name.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from models.database.job import JobRecord
from models.database.platform import Platform
from sqlalchemy import Table, create_engine, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from visualizer_sdk import InvalidParams

PLATFORM_NAME = "getonbrd"
SINK_DSN_FILE = Path("/var/run/secrets/sink/dsn")
DRIVER = "postgresql+psycopg"  # the DSN file is a plain postgresql:// URL
CONNECT_TIMEOUT_S = 10
_JOBS = cast(Table, JobRecord.__table__)  # declared as FromClause by the stubs


@dataclass(frozen=True, slots=True)
class Job:
    external_id: str
    title: str
    company: str  # empty until resolved: the API often only gives a company id
    company_id: str
    location: str
    posted_date: str
    url: str
    description: str


def _url(dsn: str) -> URL:
    try:
        return make_url(dsn).set(drivername=DRIVER)
    except ArgumentError:
        raise InvalidParams("sink dsn is not a postgresql URL") from None  # never echo the value


def read_dsn(path: Path = SINK_DSN_FILE) -> str:
    """Raises InvalidParams (exit 78, never retried) when the sink is not mounted or does not hold a URL. The messages
    name the path, never the content."""
    try:
        dsn = path.read_text().strip()
    except OSError:
        raise InvalidParams(f"sink file {path} is missing or unreadable") from None
    except UnicodeDecodeError:
        raise InvalidParams(f"sink file {path} is not UTF-8 text") from None
    if not dsn:
        raise InvalidParams(f"sink file {path} is empty")
    _url(dsn)
    return dsn


def _clip(column: str, value: str) -> str:
    """Postgres rejects NUL in text and a value over the column's length; both would fail the whole run.
    shortcut: two external_ids that differ only past the column length collide and abort the batch; ids are far
    shorter in practice, revisit if the portal's ids grow."""
    return value.replace("\x00", "")[: getattr(_JOBS.c[column].type, "length", None)]


def upsert_jobs(dsn: str, jobs: Sequence[Job]) -> int:
    """Inserts the jobs not stored yet and refreshes a changed description, in one transaction. Returns how many rows
    were new."""
    engine = create_engine(
        _url(dsn), hide_parameters=True, connect_args={"connect_timeout": CONNECT_TIMEOUT_S}
    )
    new = 0
    try:
        with engine.begin() as conn:
            platform_id = conn.execute(
                select(Platform.id).where(Platform.name == PLATFORM_NAME)
            ).scalar_one_or_none()
            if platform_id is None:
                raise InvalidParams(f"platform {PLATFORM_NAME!r} not found in the store")  # 78: a retry will not add it
            for job in jobs:
                stmt = pg_insert(_JOBS).values(
                    # The model's defaults are Python-side and the column has no server default: set them here.
                    id=uuid.uuid4(),
                    tags=[],
                    platform_id=platform_id,
                    external_id=_clip("external_id", job.external_id),
                    title=_clip("title", job.title),
                    company=_clip("company", job.company),
                    location=_clip("location", job.location),
                    posted_date=_clip("posted_date", job.posted_date),
                    url=_clip("url", job.url),
                    description=job.description.replace("\x00", ""),
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=[_JOBS.c.platform_id, _JOBS.c.external_id],
                    set_={"description": stmt.excluded.description},
                    # job_store.upsert_jobs semantics: only a non-empty, changed description overwrites.
                    where=(stmt.excluded.description != "")
                    & _JOBS.c.description.is_distinct_from(stmt.excluded.description),
                ).returning(literal_column("xmax = 0").label("inserted"))  # no row back: unchanged
                row = conn.execute(stmt).first()
                new += bool(row and row.inserted)
    except SQLAlchemyError as exc:
        raise RuntimeError(f"store write failed: {type(exc).__name__}") from None
    finally:
        engine.dispose()
    return new
