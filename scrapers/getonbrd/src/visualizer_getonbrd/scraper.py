"""GetOnBoard job search over its public JSON API.

Ported from scraping_jobs/backend/services/getonbrd.py. The jobs go to the scraper's own store
(`store.upsert_jobs`, visualizer ADR 0019); the platform keeps only the status line. The log
carries counts and rule names only, never job text.
"""

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeGuard
from urllib.parse import quote, urlencode

from pydantic import BaseModel, ConfigDict, Field
from visualizer_sdk import Context, load_params, scraper
from visualizer_sdk.engines import ScraplingConfig

from visualizer_getonbrd.store import SINK_DSN_FILE, Job, read_dsn, upsert_jobs

HOST = "www.getonbrd.com"
API_URL = f"https://{HOST}/api/v0/search/jobs"
COMPANY_URL = f"https://{HOST}/api/v0/companies"
PAGE_SIZE = 10
MAX_PAGES_LIMIT = 5  # same cap as the source's search params
MAX_BODY_BYTES = 2 * 1024 * 1024  # a page is ~10 jobs; anything far larger is not this API


class SearchParams(BaseModel):
    """The definition's `params` (`VISUALIZER_PARAMS`)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = ""
    country_code: str = ""
    max_pages: int = Field(default=1, ge=1, le=MAX_PAGES_LIMIT)


def search_url(params: SearchParams, page: int) -> str:
    """`page` is 1-based, as the API expects."""
    q: dict[str, str | int] = {"query": params.query, "page": page, "per_page": PAGE_SIZE}
    if params.country_code:
        q["country_code"] = params.country_code
    return f"{API_URL}?{urlencode(q)}"


def company_url(company_id: str) -> str:
    return f"{COMPANY_URL}/{quote(company_id, safe='')}"


def parse_jobs(data: Any) -> list[Job]:
    """Items without id or title are dropped. Takes the `data` envelope or a list."""
    items = data.get("data", data.get("jobs", [])) if isinstance(data, Mapping) else data
    jobs: list[Job] = []
    for item in items or []:
        if not isinstance(item, Mapping):
            continue
        attrs = item.get("attributes", item)
        if not isinstance(attrs, Mapping):
            continue
        job_id = str(item.get("id", attrs.get("id", "")))
        title = attrs.get("title")
        url = attrs.get("url", attrs.get("remote_url", f"https://{HOST}/jobs/{job_id}"))
        # A null or non-string title or url (the API is not ours) drops the item, not the batch.
        if not (job_id and _is_text(title) and _is_text(url)):
            continue
        description = "\n".join(
            p for k in ("description", "projects", "functions", "benefits") if _is_text(p := attrs.get(k))
        )
        company, company_id = _company(attrs)
        jobs.append(
            Job(
                external_id=job_id,
                title=title,
                company=company,
                company_id=company_id,
                location=_location(attrs),
                posted_date=_iso_date(attrs.get("published_at", attrs.get("created_at"))),
                url=url,
                description=description,
            )
        )
    return jobs


def _is_text(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value)


_MODALITY_LABELS = {"no_remote": "On-site", "hybrid": "Hybrid"}


def _location(attrs: Mapping[str, Any]) -> str:
    """Readable place: country names plus the work modality ("Chile, Hybrid"). The API gives
    cities only as ids, so they are left out."""
    countries = attrs.get("countries")
    parts = (
        [c for c in countries if isinstance(c, str) and c] if isinstance(countries, list) else []
    )
    modality = attrs.get("remote_modality")
    if isinstance(modality, str) and modality:
        parts.append(_MODALITY_LABELS.get(modality, "Remote" if "remote" in modality else modality))
    return ", ".join(dict.fromkeys(parts))  # the API lists "Remote" as a country too


def _iso_date(epoch: Any) -> str:
    """`published_at` epoch seconds -> YYYY-MM-DD (UTC); empty when absent or out of range."""
    if isinstance(epoch, bool) or not isinstance(epoch, int | float):
        return ""
    try:
        return datetime.fromtimestamp(epoch, UTC).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def _company(attrs: Mapping[str, Any]) -> tuple[str, str]:
    """(inline name, id); the name is empty when only a reference came back."""
    raw = attrs.get("company") or attrs.get("organization")
    if isinstance(raw, str):
        return raw, ""
    if not isinstance(raw, Mapping):
        return "", ""
    data = raw.get("data")
    data = data if isinstance(data, Mapping) else {}
    name = raw.get("name") or data.get("attributes", {}).get("name") or ""
    name = name if isinstance(name, str) else ""
    return name, "" if name or data.get("id") is None else str(data["id"])


def company_name(payload: Any) -> str:
    name = (
        (payload.get("data", {}).get("attributes", {}).get("name") or "")
        if isinstance(payload, Mapping)
        else ""
    )
    return name if isinstance(name, str) else ""


async def _get_json(ctx: Context, url: str) -> Any:
    # The Scrapling adapter's content() wraps the body in <html><body>; the native response
    # carries the raw body, so JSON goes through it.
    resp = await ctx.session.native.get(url)
    if resp.status != 200:
        raise RuntimeError(f"HTTP {resp.status}")
    if len(resp.body) > MAX_BODY_BYTES:
        raise RuntimeError("response too large")
    return json.loads(resp.body)


async def search(ctx: Context, params: SearchParams) -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()
    names: dict[str, str] = {}
    answered = 0
    for page in range(1, params.max_pages + 1):
        ctx.status(
            f"page {page}/{params.max_pages}, {len(jobs)} jobs", (page - 1) / params.max_pages
        )
        try:
            page_jobs = parse_jobs(await _get_json(ctx, search_url(params, page)))
        except Exception as exc:  # a bad page is skipped, not fatal
            ctx.log.warning("page %d failed: %s", page, exc)
            continue
        answered += 1  # an empty 200 is a normal empty result
        if not page_jobs:
            ctx.log.info("page %d empty, stopping", page)
            break
        new = [j for j in page_jobs if j.external_id not in seen]
        seen.update(j.external_id for j in new)
        for job in new:
            if job.company_id and job.company_id not in names:
                try:
                    names[job.company_id] = company_name(
                        await _get_json(ctx, company_url(job.company_id))
                    )
                except Exception as exc:
                    ctx.log.warning("company lookup failed: %s", exc)
                    names[job.company_id] = ""
            job = replace(job, company=names.get(job.company_id, job.company))
            jobs.append(job)
        ctx.log.info("page %d: %d new of %d", page, len(new), len(page_jobs))
    if not answered:  # fail the run so max_attempts can retry an outage
        raise RuntimeError("all pages failed")
    ctx.log.info("total: %d jobs", len(jobs))
    return jobs


async def collect(ctx: Context, params: SearchParams, sink_file: Path = SINK_DSN_FILE) -> None:
    """Search, then upsert. The sink is read first so a missing mount fails before any request."""
    dsn = read_dsn(sink_file)
    jobs = await search(ctx, params)
    ctx.status(f"saving {len(jobs)} jobs", 0.95)
    new = await asyncio.to_thread(upsert_jobs, dsn, jobs)  # blocking driver: off the event loop
    ctx.log.info("stored: %d jobs, %d new", len(jobs), new)
    ctx.status(f"done, {len(jobs)} jobs, {new} new", 1.0)


@scraper("getonbrd", engine=ScraplingConfig())
async def getonbrd(ctx: Context) -> None:
    await collect(ctx, load_params(SearchParams))
