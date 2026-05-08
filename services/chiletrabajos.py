import asyncio
import html as html_lib
import re
import urllib.parse

from sqlalchemy.ext.asyncio import AsyncSession

from helpers.http_scraper import HttpScraper
from models.chiletrabajos import ChileTrabajosSearchParams
from models.common import Job, JobResponse
from services.job_store import upsert_jobs

LOCATION_IDS = {
    "santiago": "1022",
    "concepción": "1002",
    "valparaíso": "1003",
    "antofagasta": "1004",
    "rancagua": "1005",
    "arica": "1006",
    "iquique": "1007",
    "temuco": "1008",
    "puerto montt": "1009",
    "la serena": "1010",
}

ITEMS_PER_PAGE = 30
REQUEST_DELAY = 1.5


async def run(
    session: AsyncSession,
    *,
    query: str | None = None,
    max_pages: int = 1,
    **kwargs,
) -> dict:
    from models.chiletrabajos import ChileTrabajosSearchParams
    service = ChileTrabajosService()
    params = ChileTrabajosSearchParams(
        keywords=query or "",
        max_pages=max_pages,
        location=kwargs.get("location") or "",
    )
    result = await service.search(params)
    jobs_inserted = await upsert_jobs(session, result.jobs, platform_name="chiletrabajos")
    return {
        "platform": "chiletrabajos",
        "jobs_found": result.total,
        "jobs_inserted": jobs_inserted,
        "errors": [],
    }


class ChileTrabajosService:
    """Servicio de scraping de ofertas ChileTrabajos (HTTP público)."""

    def __init__(self, scraper: HttpScraper | None = None, db: AsyncSession | None = None):
        self.scraper = scraper or HttpScraper()
        self.db = db

    async def search(self, params: ChileTrabajosSearchParams) -> JobResponse:
        all_jobs: list[dict] = []
        seen_ids: set[str] = set()

        print(f'[ChileTrabajos] Buscando: "{params.keywords}" en {params.location or "Todo Chile"}')

        for page in range(params.max_pages):
            jobs = self._search_page(params, page)

            if not jobs:
                break

            new_jobs = [j for j in jobs if j["job_id"] not in seen_ids]
            for j in new_jobs:
                seen_ids.add(j["job_id"])

            all_jobs.extend(new_jobs)
            print(f"  Página {page}: {len(new_jobs)} nuevos ({len(jobs)} total)")

            if len(jobs) < ITEMS_PER_PAGE:
                break

        if params.fetch_details:
            print(f"[ChileTrabajos] Obteniendo detalles de {len(all_jobs)} jobs...")
            for i, job in enumerate(all_jobs):
                desc = self._get_description(job["url"])
                if desc:
                    job["description"] = desc
                if i < len(all_jobs) - 1:
                    await asyncio.sleep(REQUEST_DELAY)

        print(f"[ChileTrabajos] Total: {len(all_jobs)} ofertas")

        response_jobs = [
            Job(
                platform="chiletrabajos",
                external_id=j["job_id"],
                title=j["title"],
                company=j["company"],
                location=j["location"],
                posted_date=j["posted_date"],
                url=j["url"],
                description=j.get("description", ""),
            )
            for j in all_jobs
        ]

        if self.db:
            stored = await upsert_jobs(self.db, response_jobs, platform_name="chiletrabajos")
            print(f"[ChileTrabajos] {stored} jobs nuevos guardados en DB")

        return JobResponse(source="chiletrabajos", total=len(response_jobs), jobs=response_jobs)

    def _search_page(
        self,
        search_params: ChileTrabajosSearchParams,
        page: int,
    ) -> list[dict]:
        offset = page * ITEMS_PER_PAGE

        query_params: dict[str, str] = {
            "action": "search",
            "filterSearch": "Buscar",
            "within": "25",
        }

        if search_params.keywords:
            query_params["2"] = search_params.keywords
        if search_params.location:
            loc_id = LOCATION_IDS.get(search_params.location.lower(), "")
            if loc_id:
                query_params["13"] = loc_id
        if search_params.date_filter and search_params.date_filter != "":
            query_params["5"] = search_params.date_filter.value
        if search_params.category:
            query_params["categoria"] = search_params.category
        if search_params.job_type:
            query_params["14"] = search_params.job_type.value
        if search_params.company:
            query_params["8"] = search_params.company

        query = urllib.parse.urlencode(query_params)
        path = f"/encuentra-un-empleo/{offset}?{query}" if offset > 0 else f"/encuentra-un-empleo?{query}"

        status, body = self.scraper.get("www.chiletrabajos.cl", path)

        if status != 200:
            print(f"  [Search] Error: {status}")
            return []

        return self._parse_cards(body)

    def _parse_cards(self, html: str) -> list[dict]:
        jobs = []

        card_re = re.compile(
            r'<div\s+class="job-item[^"]*"[^>]*>(.*?)</div>\s*(?=<div\s+class="job-item|<nav\s|<footer|$)',
            re.DOTALL,
        )

        for match in card_re.finditer(html):
            card = match.group(1)

            url_m = re.search(
                r'<h2\s+class="title[^"]*">\s*<a\s+href="(https?://www\.chiletrabajos\.cl/trabajo/[^"]+)"',
                card,
            )
            if not url_m:
                continue
            url = html_lib.unescape(url_m.group(1))

            id_m = re.search(r'-(\d+)$', url)
            if not id_m:
                continue
            job_id = id_m.group(1)

            title_m = re.search(
                r'class="font-weight-bold"[^>]*>(.*?)</a>',
                card,
                re.DOTALL,
            )
            title = html_lib.unescape(title_m.group(1).strip()) if title_m else ""

            meta_matches = re.findall(
                r'<h3\s+class="meta"[^>]*>(.*?)</h3>',
                card,
                re.DOTALL,
            )

            company = ""
            location = ""
            posted_date = ""

            if len(meta_matches) >= 1:
                first_meta = re.sub(r'<[^>]+>', '', meta_matches[0]).strip()
                parts = [p.strip() for p in first_meta.split(",")]
                if len(parts) >= 2:
                    company = parts[0]
                    location = parts[1]
                elif len(parts) == 1:
                    company = parts[0]

            if len(meta_matches) >= 2:
                date_text = re.sub(r'<[^>]+>', '', meta_matches[1]).strip()
                posted_date = date_text

            if title and url:
                jobs.append({
                    "job_id": job_id,
                    "title": title,
                    "company": company,
                    "location": location,
                    "posted_date": posted_date,
                    "url": url,
                    "description": "",
                })

        return jobs

    def _get_description(self, job_url: str) -> str:
        parsed = urllib.parse.urlparse(job_url)
        host = parsed.hostname or "www.chiletrabajos.cl"
        status, body = self.scraper.get(host, parsed.path)

        if status != 200:
            return ""

        desc_m = re.search(
            r'<div\s+class="job-item[^"]*detalle[^"]*"[^>]*>.*?<p\s+class="mb-0">(.*?)</p>',
            body,
            re.DOTALL,
        )
        if not desc_m:
            return ""

        raw = desc_m.group(1)
        text = re.sub(r'<br\s*/?>', '\n', raw)
        text = re.sub(r'<li>', '\n- ', text)
        text = re.sub(r'<[^>]+>', '', text)
        text = html_lib.unescape(text)
        text = re.sub(r'\n{3,}', '\n\n', text.strip())
        return text
