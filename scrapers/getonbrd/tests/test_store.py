import logging
from pathlib import Path

import pytest
from fakes import FakeSession, ctx_for
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import ProgrammingError
from visualizer_getonbrd.scraper import SearchParams, collect
from visualizer_getonbrd.store import Job, read_dsn, upsert_jobs
from visualizer_sdk import InvalidParams

pytestmark = pytest.mark.anyio

SECRET_TEXT = "SECRET-JOB-TEXT"  # noqa: S105
SENTINEL_PASSWORD = "PW-SENTINEL-123"  # noqa: S105


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _job(external_id: str = "a", description: str = "d", **overrides: str) -> Job:
    fields = {
        "external_id": external_id,
        "title": "T",
        "company": "Acme",
        "company_id": "",
        "location": "Chile",
        "posted_date": "2026-10-27",
        "url": "https://www.getonbrd.com/jobs/a",
        "description": description,
    } | overrides
    return Job(**fields)


def _rows(store: Engine) -> list[tuple[str, str, str, list[str]]]:
    with store.connect() as conn:
        result = conn.execute(
            text("SELECT external_id, title, description, tags FROM jobs ORDER BY external_id")
        )
        return [(r.external_id, r.title, r.description, r.tags) for r in result]


def test_the_same_external_id_twice_is_one_row_and_one_new(store: Engine, dsn: str) -> None:
    assert upsert_jobs(dsn, [_job("a"), _job("b")]) == 2
    assert upsert_jobs(dsn, [_job("a"), _job("b")]) == 0
    assert [r[0] for r in _rows(store)] == ["a", "b"]


def test_new_rows_carry_an_id_and_empty_tags(store: Engine, dsn: str) -> None:
    upsert_jobs(dsn, [_job("a")])
    with store.connect() as conn:
        row = conn.execute(text("SELECT id, tags, scraped_at FROM jobs")).one()
    assert row.id is not None and row.tags == [] and row.scraped_at is not None


def test_a_changed_description_updates_the_row_and_counts_no_new(store: Engine, dsn: str) -> None:
    upsert_jobs(dsn, [_job("a", description="old", title="kept")])
    assert upsert_jobs(dsn, [_job("a", description="new", title="ignored")]) == 0
    assert _rows(store) == [("a", "kept", "new", [])]  # only the description moves


def test_an_empty_description_does_not_erase_a_stored_one(store: Engine, dsn: str) -> None:
    upsert_jobs(dsn, [_job("a", description="old")])
    upsert_jobs(dsn, [_job("a", description="")])
    assert _rows(store)[0][2] == "old"


def test_oversized_and_nul_values_do_not_fail_the_batch(store: Engine, dsn: str) -> None:
    assert upsert_jobs(dsn, [_job("a", title="x" * 5000, company="Ac\x00me")]) == 1
    with store.connect() as conn:
        row = conn.execute(text("SELECT length(title) AS n, company FROM jobs")).one()
    assert (row.n, row.company) == (500, "Acme")


def test_a_missing_platform_row_is_invalid_params(store: Engine, dsn: str) -> None:
    with store.begin() as conn:
        conn.execute(text("DELETE FROM platforms"))
    with pytest.raises(InvalidParams, match="not found"):
        upsert_jobs(dsn, [_job()])


def test_a_missing_or_empty_sink_file_is_invalid_params(tmp_path: Path) -> None:
    with pytest.raises(InvalidParams, match="missing or unreadable"):
        read_dsn(tmp_path / "dsn")
    (tmp_path / "dsn").write_text("\n")
    with pytest.raises(InvalidParams, match="empty"):
        read_dsn(tmp_path / "dsn")
    (tmp_path / "dsn").write_text("not a url")
    with pytest.raises(InvalidParams, match="not a postgresql URL"):
        read_dsn(tmp_path / "dsn")
    (tmp_path / "dsn").write_bytes(b"\xff\xfe postgresql://u:" + SENTINEL_PASSWORD.encode())
    with pytest.raises(InvalidParams, match="not UTF-8") as err:
        read_dsn(tmp_path / "dsn")
    assert SENTINEL_PASSWORD not in str(err.value)


def test_the_upsert_works_with_only_the_granted_privileges(store: Engine, role_dsn: str) -> None:
    assert upsert_jobs(role_dsn, [_job("a", description="old")]) == 1
    assert upsert_jobs(role_dsn, [_job("a", description="new")]) == 0
    assert _rows(store) == [("a", "T", "new", [])]
    role = create_engine(role_dsn.replace("postgresql://", "postgresql+psycopg://"))
    try:
        for denied in ("DELETE FROM jobs", "UPDATE jobs SET title = 'x'", "CREATE TEMP TABLE t (a int)"):
            with pytest.raises(ProgrammingError), role.begin() as conn:
                conn.execute(text(denied))
    finally:
        role.dispose()


async def test_collect_counts_found_and_new_in_the_final_status(
    store: Engine, dsn: str, tmp_path: Path
) -> None:
    sink = tmp_path / "dsn"
    sink.write_text(dsn + "\n")
    page = {"data": [{"id": i, "attributes": {"title": SECRET_TEXT}} for i in ("a", "b")]}
    params = SearchParams(max_pages=1)

    ctx = ctx_for(FakeSession({1: page}))
    await collect(ctx, params, sink)
    assert ctx.status_text == "done, 2 jobs, 2 new" and ctx.progress == 1.0

    ctx = ctx_for(FakeSession({1: page}))
    await collect(ctx, params, sink)
    assert ctx.status_text == "done, 2 jobs, 0 new"  # the same postings add nothing
    assert len(_rows(store)) == 2


async def test_collect_without_a_sink_fails_before_any_request(tmp_path: Path) -> None:
    session = FakeSession({1: {"data": []}})
    with pytest.raises(InvalidParams):
        await collect(ctx_for(session), SearchParams(), tmp_path / "missing")
    assert session.urls == []


async def test_logs_and_status_carry_no_dsn_password_or_job_text(
    store: Engine, dsn: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    page = {"data": [{"id": "a", "attributes": {"title": SECRET_TEXT, "description": SECRET_TEXT}}]}
    bad = tmp_path / "bad"  # nothing listens on port 1: the write fails with the password in the DSN
    bad.write_text(f"postgresql://getonbrd_scraper:{SENTINEL_PASSWORD}@127.0.0.1:1/scraping_masive\n")
    good = tmp_path / "good"
    good.write_text(dsn + "\n")

    for sink, expected in ((good, None), (bad, RuntimeError)):
        ctx = ctx_for(FakeSession({1: page}))
        with caplog.at_level(logging.DEBUG):
            if expected is None:
                await collect(ctx, SearchParams(), sink)
            else:
                with pytest.raises(expected) as err:
                    await collect(ctx, SearchParams(), sink)
                assert SENTINEL_PASSWORD not in str(err.value) and SECRET_TEXT not in str(err.value)
                assert err.value.__cause__ is None and err.value.__suppress_context__
        for text_seen in (caplog.text, ctx.status_text or ""):
            assert SECRET_TEXT not in text_seen and SENTINEL_PASSWORD not in text_seen
