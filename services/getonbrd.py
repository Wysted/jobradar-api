import json
import urllib.parse

from sqlalchemy.ext.asyncio import AsyncSession

from helpers.http_scraper import HttpScraper
from models.common import Job, JobResponse
from models.getonbrd import GetOnBrdSearchParams
from services.job_store import upsert_jobs

HOST = "www.getonbrd.com"
API_PATH = "/api/v0/search/jobs"
COMPANY_PATH = "/api/v0/companies"
PAGE_SIZE = 10


async def run(
    session: AsyncSession,
    *,
    query: str | None = None,
    max_pages: int = 1,
    **kwargs,
) -> dict:
    from models.getonbrd import GetOnBrdSearchParams
    service = GetOnBrdService()
    params = GetOnBrdSearchParams(keywords=query or "", max_pages=max_pages)
    result = await service.search(params)
    jobs_inserted = await upsert_jobs(session, result.jobs, platform_name="getonbrd")
    return {
        "platform": "getonbrd",
        "jobs_found": result.total,
        "jobs_inserted": jobs_inserted,
        "errors": [],
    }


class GetOnBrdService:
    """Servicio de scraping de ofertas Get on Board vía API pública."""

    def __init__(self, scraper: HttpScraper | None = None, db: AsyncSession | None = None):
        self.scraper = HttpScraper(headers={
            "user-agent": "Mozilla/5.0",
            "accept": "application/json",
        })
        self.db = db
        self._company_cache: dict[str, str] = {}

    async def search(self, params: GetOnBrdSearchParams) -> JobResponse:
        all_jobs: list[dict] = []
        seen_ids: set[str] = set()

        print(f'[GetOnBrd] Buscando: "{params.keywords}"')

        for page in range(params.max_pages):
            print(f"[GetOnBrd] Scrapeando página {page}...")
            jobs = self._search_page(params, page)
            print(f"[GetOnBrd] _search_page retornó {len(jobs)} jobs")

            if not jobs:
                print("[GetOnBrd] Sin resultados, deteniendo paginación")
                break

            new_jobs = [j for j in jobs if j["job_id"] not in seen_ids]
            for j in new_jobs:
                seen_ids.add(j["job_id"])

            all_jobs.extend(new_jobs)
            print(f"  Página {page}: {len(new_jobs)} nuevos ({len(jobs)} total)")

            if len(jobs) < PAGE_SIZE:
                break

        print(f"[GetOnBrd] Total: {len(all_jobs)} ofertas")

        response_jobs = [
            Job(
                platform="getonbrd",
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
            stored = await upsert_jobs(self.db, response_jobs, platform_name="getonbrd")
            print(f"[GetOnBrd] {stored} jobs nuevos guardados en DB")

        return JobResponse(source="getonbrd", total=len(response_jobs), jobs=response_jobs)

    def _search_page(self, params: GetOnBrdSearchParams, page: int) -> list[dict]:
        query_params: dict = {
            "query": params.keywords,
            "page": page + 1,
            "per_page": PAGE_SIZE,
        }
        if params.country_code:
            query_params["country_code"] = params.country_code
        query = urllib.parse.urlencode(query_params)
        path = f"{API_PATH}?{query}"

        print(f"[GetOnBrd] GET https://{HOST}{path}")
        status, body = self.scraper.get(HOST, path)
        print(f"[GetOnBrd] Respuesta HTTP: {status}, body length: {len(body)} chars")

        if status != 200:
            print(f"  [Search] Error HTTP: {status}")
            print(f"  Body: {body[:500]}")
            return []

        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            print(f"  [Search] Error al parsear JSON: {e}")
            print(f"  Body snippet: {body[:500]}")
            return []

        print(f"[GetOnBrd] JSON keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
        return self._parse_response(data)

    def _parse_response(self, data: dict | list) -> list[dict]:
        jobs = []

        # La API puede retornar una lista directa o un dict con key "data"
        if isinstance(data, dict):
            items = data.get("data", data.get("jobs", []))
        else:
            items = data

        print(f"[GetOnBrd] _parse_response: {len(items)} items recibidos")
        if items:
            print(f"[GetOnBrd] Estructura item[0]:\n{json.dumps(items[0], indent=2, ensure_ascii=False)}")

        for idx, item in enumerate(items):
            # Soporta estructura plana o anidada bajo "attributes"
            attrs = item.get("attributes", item)

            job_id = str(item.get("id", attrs.get("id", "")))
            title = attrs.get("title", "")
            url = attrs.get("url", attrs.get("remote_url", f"https://{HOST}/jobs/{job_id}"))

            description_parts = [
                attrs.get("description", ""),
                attrs.get("projects", ""),
                attrs.get("functions", ""),
                attrs.get("benefits", ""),
            ]
            description = "\n".join(p for p in description_parts if p)

            company = self._extract_company(attrs)

            location_raw = attrs.get("modality", "")
            if isinstance(location_raw, dict):
                location = str(location_raw.get("data", {}).get("id", ""))
            else:
                location = str(location_raw) if location_raw else ""
            posted_date = attrs.get("published_at", attrs.get("created_at", ""))

            print(f"  [item {idx}] job_id={job_id!r} | title={title!r} | company={company!r} | location={location!r}")

            if job_id and title:
                jobs.append({
                    "job_id": job_id,
                    "title": title,
                    "company": company,
                    "location": location,
                    "posted_date": str(posted_date),
                    "url": url,
                    "description": description,
                })
            else:
                print(f"  [item {idx}] Descartado (sin job_id o title)")

        print(f"[GetOnBrd] _parse_response: {len(jobs)} jobs válidos")
        return jobs

    def _extract_company(self, attrs: dict) -> str:
        raw = attrs.get("company") or attrs.get("organization")
        if not raw:
            return ""

        if isinstance(raw, str):
            return raw

        if not isinstance(raw, dict):
            return ""

        inline = raw.get("name") or raw.get("data", {}).get("attributes", {}).get("name")
        if inline:
            return inline

        company_id = raw.get("data", {}).get("id") if isinstance(raw.get("data"), dict) else None
        if company_id is None:
            return ""

        return self._fetch_company_name(str(company_id))

    def _fetch_company_name(self, company_id: str) -> str:
        if company_id in self._company_cache:
            return self._company_cache[company_id]

        path = f"{COMPANY_PATH}/{urllib.parse.quote(company_id, safe='')}"
        status, body = self.scraper.get(HOST, path)
        if status != 200:
            print(f"  [Company {company_id}] HTTP {status}")
            self._company_cache[company_id] = ""
            return ""

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._company_cache[company_id] = ""
            return ""

        name = payload.get("data", {}).get("attributes", {}).get("name", "") or ""
        self._company_cache[company_id] = name
        return name
