"""Store tests run against a real Postgres with pgvector (the backend's `db` compose service), in its own database
`getonbrd_test`, so they never touch `scraping_masive`. The tables come from the backend's models: a schema change that
breaks the upsert fails here."""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from models.database.job import JobRecord
from models.database.platform import Platform
from sqlalchemy import Engine, Table, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

ADMIN_URL = os.environ.get(
    "GETONBRD_TEST_ADMIN_URL",  # the dev compose's own superuser, published on 127.0.0.1 only
    "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/postgres",
)
TEST_DB = "getonbrd_test"
GRANTS_SQL = Path(__file__).parents[3] / "scripts/visualizer/scraper_role_grants.sql"
REAL_ROLE = "getonbrd_scraper"
TEST_ROLE = "getonbrd_scraper_test"  # not the real role: it exists cluster-wide, with the real password
TEST_ROLE_PASSWORD = "test-role-password"  # noqa: S105
PLATFORMS = cast(Table, Platform.__table__)
TABLES = [PLATFORMS, cast(Table, JobRecord.__table__)]


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_DB})
            if not exists:
                conn.execute(text(f"CREATE DATABASE {TEST_DB}"))
    except OperationalError:
        pytest.fail(
            "no Postgres to test against: run `docker compose up -d db` in backend/ "
            "(or set GETONBRD_TEST_ADMIN_URL)"
        )
    finally:
        admin.dispose()
    eng = create_engine(make_url(ADMIN_URL).set(database=TEST_DB))
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    JobRecord.metadata.drop_all(eng, tables=TABLES)
    JobRecord.metadata.create_all(eng, tables=TABLES)
    yield eng
    eng.dispose()


@pytest.fixture
def store(engine: Engine) -> Engine:
    """A clean store with the seeded `getonbrd` platform row (migration 001 seeds it in the real one)."""
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE jobs, platforms CASCADE"))
        conn.execute(
            PLATFORMS.insert().values(
                name="getonbrd", display_name="Get on Board", base_url="https://www.getonbrd.com"
            )
        )
    return engine


@pytest.fixture
def dsn(store: Engine) -> str:
    return store.url.render_as_string(hide_password=False).replace("+psycopg", "")


@pytest.fixture
def role_dsn(store: Engine) -> str:
    """The DSN of a role with exactly the production grants: the script's own SQL, run here with the role renamed."""
    grants = GRANTS_SQL.read_text().replace(REAL_ROLE, TEST_ROLE)
    with store.begin() as conn:
        conn.exec_driver_sql(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{TEST_ROLE}') "
            f"THEN CREATE ROLE {TEST_ROLE} LOGIN PASSWORD '{TEST_ROLE_PASSWORD}'; END IF; END $$"
        )
        conn.exec_driver_sql(grants.replace("%", "%%"))  # psycopg reads % as a placeholder
    url = store.url.set(username=TEST_ROLE, password=TEST_ROLE_PASSWORD)
    return url.render_as_string(hide_password=False).replace("+psycopg", "")
