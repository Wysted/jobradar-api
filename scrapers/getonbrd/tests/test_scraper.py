import json
import logging
from pathlib import Path

import pytest
from fakes import FakeSession, ctx_for, jobs_page
from visualizer_getonbrd.scraper import (
    MAX_BODY_BYTES,
    SearchParams,
    parse_jobs,
    search,
    search_url,
)
from visualizer_sdk import InvalidParams, load_params

pytestmark = pytest.mark.anyio

FIXTURE = json.loads((Path(__file__).parent / "search_page.json").read_text())


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_search_url() -> None:
    assert (
        search_url(SearchParams(query="python dev", country_code="CL"), 2)
        == "https://www.getonbrd.com/api/v0/search/jobs?query=python+dev&page=2&per_page=10&country_code=CL"
    )
    assert "country_code" not in search_url(SearchParams(query="x"), 1)


def test_parse_jobs_real_shape() -> None:
    jobs = parse_jobs(FIXTURE)
    assert len(jobs) == 4
    first = jobs[0]
    assert first.external_id == "desarrollador-a-full-stack-financia-capital-santiago"
    assert first.title == "Desarrollador/a Full-Stack"
    assert first.location == "Chile, On-site"
    assert first.posted_date == "2026-10-27"
    assert jobs[1].location == "Remote"  # countries ["Remote"] + remote_local, deduped
    assert first.company == "" and first.company_id == "12324"
    assert first.url.endswith("/jobs/" + first.external_id)
    assert "Laravel" in first.description


def test_parse_jobs_flat_list_inline_company_and_drops_invalid() -> None:
    jobs = parse_jobs([{"id": 7, "title": "T", "company": "Acme"}, {"id": 8}, {"title": "no id"}])
    assert [(j.external_id, j.company, j.company_id) for j in jobs] == [("7", "Acme", "")]
    assert parse_jobs({"data": None}) == []


def test_location_and_date_fallbacks() -> None:
    (job,) = parse_jobs([{"id": 1, "title": "T", "published_at": 10**30, "remote_modality": "x_y"}])
    assert (job.location, job.posted_date) == ("x_y", "")
    (job,) = parse_jobs(
        [{"id": 1, "title": "T", "countries": ["Chile", "Peru"], "remote_modality": "hybrid"}]
    )
    assert job.location == "Chile, Peru, Hybrid"
    (job,) = parse_jobs(
        [{"id": 1, "title": "T", "published_at": "x", "modality": {"data": {"id": 1}}}]
    )
    assert (job.location, job.posted_date) == ("", "")


async def test_stops_at_the_first_emptypage() -> None:
    session = FakeSession({1: jobs_page("a", "b"), 2: jobs_page("b", "c")})
    jobs = await search(ctx_for(session), SearchParams(query="q", max_pages=5))
    assert [j.external_id for j in jobs] == ["a", "b", "c"]  # "b" deduplicated
    assert len(session.urls) == 3  # page 3 was empty, so 4 and 5 never ran


async def test_failed_pages_are_skipped_and_max_pages_bounds_the_run() -> None:
    session = FakeSession({1: OSError("boom"), 2: 503, 3: jobs_page("x"), 4: jobs_page("y")})
    jobs = await search(ctx_for(session), SearchParams(max_pages=3))
    assert [j.external_id for j in jobs] == ["x"]
    assert len(session.urls) == 3


async def test_every_page_failing_fails_the_run() -> None:
    session = FakeSession({1: OSError("boom"), 2: 503})
    with pytest.raises(RuntimeError, match="all pages failed"):
        await search(ctx_for(session), SearchParams(max_pages=2))


async def test_an_empty_200_first_page_is_a_normal_empty_result() -> None:
    session = FakeSession({1: {"data": []}})
    assert await search(ctx_for(session), SearchParams(max_pages=2)) == []


async def test_an_oversized_body_is_a_failedpage() -> None:
    session = FakeSession({1: {"data": [], "pad": "x" * MAX_BODY_BYTES}, 2: jobs_page("ok")})
    jobs = await search(ctx_for(session), SearchParams(max_pages=2))
    assert [j.external_id for j in jobs] == ["ok"]
    session = FakeSession({1: {"data": [], "pad": "x" * MAX_BODY_BYTES}})
    with pytest.raises(RuntimeError, match="all pages failed"):
        await search(ctx_for(session), SearchParams(max_pages=1))


def test_an_item_with_non_mapping_attributes_is_skipped_not_thepage() -> None:
    items = [
        {"id": "1", "attributes": "oops"},
        {"id": "2", "attributes": None},
        {"id": "3", "attributes": {"title": "good"}},
    ]
    assert [j.external_id for j in parse_jobs({"data": items})] == ["3"]


async def test_company_name_is_resolved_once_per_company() -> None:
    session = FakeSession({1: FIXTURE})
    jobs = await search(ctx_for(session), SearchParams(max_pages=1))
    assert {j.company for j in jobs} == {"Acme"}
    companies = [u for u in session.urls if "/companies/" in u]
    assert len(companies) == len({j.company_id for j in jobs})


def test_params_come_from_visualizer_params_with_defaults() -> None:
    got = load_params(
        SearchParams,
        {"VISUALIZER_PARAMS": '{"query":"py","country_code":"CL","max_pages":3}'},
    )
    assert (got.query, got.country_code, got.max_pages) == ("py", "CL", 3)
    assert load_params(SearchParams, {}) == SearchParams()


@pytest.mark.parametrize(
    "raw",
    ['{"max_pages":0}', '{"max_pages":6}', '{"max_pages":"x"}', '{"max_page":2}', '{"query":1}'],
)
def test_bad_params_are_invalid(raw: str) -> None:
    with pytest.raises(InvalidParams):
        load_params(SearchParams, {"VISUALIZER_PARAMS": raw})


async def test_counts_new_jobs_once_and_logs_no_job_text(caplog: pytest.LogCaptureFixture) -> None:
    secret = "SECRET-DESCRIPTION-TEXT"  # noqa: S105
    page = {"data": [{"id": "a", "attributes": {"title": secret, "description": secret}}]}
    ctx = ctx_for(FakeSession({1: page, 2: page}))
    with caplog.at_level(logging.DEBUG):
        jobs = await search(ctx, SearchParams(max_pages=2))
    assert len(jobs) == 1  # the same external_id on page 2 counts once
    assert "SECRET" not in (ctx.status_text or "")
    assert "SECRET" not in caplog.text


def test_a_null_or_non_string_title_or_url_drops_the_item_not_the_batch() -> None:
    items = [
        {"id": "1", "attributes": {"title": "ok", "url": None}},
        {"id": "2", "attributes": {"title": 5, "url": "https://x"}},
        {"id": "3", "attributes": {"title": None}},
        {"id": "4", "attributes": {"title": "good", "company": {"name": 7}, "description": 9}},
    ]
    jobs = parse_jobs({"data": items})
    assert [(j.external_id, j.company, j.description) for j in jobs] == [("4", "", "")]
