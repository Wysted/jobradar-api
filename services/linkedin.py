import asyncio
import html as html_lib
import re
import urllib.parse

from sqlalchemy.ext.asyncio import AsyncSession

from helpers.http_scraper import HttpScraper
from models.common import Job, JobResponse
from models.linkedin import LinkedInSearchParams
from services.job_store import upsert_jobs

GEO_IDS = {
    "chile": "104621616",
    "argentina": "100446943",
    "colombia": "100876405",
    "mexico": "103323778",
    "peru": "102927786",
    "spain": "105646813",
    "united states": "103644278",
    "remote": "",
}

# Country → list of location markers accepted in the scraped card.
# Matching is case-insensitive substring on the `location` field.
COUNTRY_MARKERS: dict[str, list[str]] = {
    "chile": [
        "chile",
        "santiago", "valparaíso", "valparaiso", "viña del mar", "vina del mar",
        "concepción", "concepcion", "iquique", "antofagasta", "temuco",
        "rancagua", "coquimbo", "la serena", "puerto montt", "arica",
        "punta arenas", "talca", "chillán", "chillan", "osorno", "curicó",
        "curico", "calama", "copiapó", "copiapo",
    ],
    "argentina": [
        "argentina", "buenos aires", "córdoba", "cordoba", "rosario",
        "mendoza", "la plata", "mar del plata", "tucumán", "tucuman",
    ],
    "colombia": ["colombia", "bogotá", "bogota", "medellín", "medellin", "cali", "barranquilla", "cartagena"],
    "mexico": ["méxico", "mexico", "ciudad de méxico", "cdmx", "guadalajara", "monterrey", "puebla"],
    "peru": ["perú", "peru", "lima", "arequipa", "trujillo", "cusco"],
    "spain": ["españa", "espana", "spain", "madrid", "barcelona", "valencia", "sevilla", "bilbao"],
}

# LATAM-wide markers that override country filter for legitimate cross-border remote roles.
_LATAM_MARKERS = ("latam", "latinoamérica", "latinoamerica", "latin america", "américa latina", "america latina")

REQUEST_DELAY = 1.5


def _matches_country(job_location: str, country: str) -> bool:
    """Check whether a scraped LinkedIn card belongs to the target country.

    Accepts:
    - Any marker from COUNTRY_MARKERS[country] (city or country name).
    - Remote-tagged cards that explicitly mention LATAM / the target country.

    Rejects bare "Remote" (no country signal) to keep results localized.
    """
    if not country:
        return True
    loc = job_location.lower().strip()
    if not loc:
        return False
    markers = COUNTRY_MARKERS.get(country.lower(), [country.lower()])
    if any(m in loc for m in markers):
        return True
    if "remote" in loc and any(m in loc for m in _LATAM_MARKERS):
        return True
    return False


async def run(
    session: AsyncSession,
    *,
    query: str | None = None,
    max_pages: int = 1,
    **kwargs,
) -> dict:
    from models.linkedin import LinkedInSearchParams
    service = LinkedInService()
    params = LinkedInSearchParams(
        keywords=query or "",
        max_pages=max_pages,
        location=kwargs.get("location") or "",
    )
    result = await service.search(params)
    jobs_inserted = await upsert_jobs(session, result.jobs, platform_name="linkedin")
    return {
        "platform": "linkedin",
        "jobs_found": result.total,
        "jobs_inserted": jobs_inserted,
        "errors": [],
    }


class LinkedInService:
    """Servicio de scraping de ofertas LinkedIn (HTTP público, sin auth)."""

    def __init__(self, scraper: HttpScraper | None = None, db: AsyncSession | None = None):
        self.scraper = scraper or HttpScraper()
        self.db = db

    async def search(self, params: LinkedInSearchParams) -> JobResponse:
        all_jobs: list[dict] = []
        seen_ids: set[str] = set()

        if not params.geo_id and params.location:
            params.geo_id = GEO_IDS.get(params.location.lower(), "")

        print(f'[LinkedIn] Buscando: "{params.keywords}" en {params.location or "Global"}')

        country_filter = (params.location or "").strip().lower()
        dropped_by_country = 0

        for page in range(params.max_pages):
            jobs = self._search_page(params, page)

            if not jobs:
                break

            new_jobs = [j for j in jobs if j["job_id"] not in seen_ids]
            for j in new_jobs:
                seen_ids.add(j["job_id"])

            # Post-scrape country filter: LinkedIn's geoId is a hint, not a guarantee.
            # We filter by location markers before paying for description fetches.
            if country_filter:
                kept = [j for j in new_jobs if _matches_country(j["location"], country_filter)]
                dropped_by_country += len(new_jobs) - len(kept)
                new_jobs = kept

            all_jobs.extend(new_jobs)
            print(f"  Página {page}: {len(new_jobs)} nuevos ({len(jobs)} total)")

            if len(jobs) < 20:
                break

        if country_filter and dropped_by_country:
            print(f"[LinkedIn] Descartados {dropped_by_country} jobs fuera de {country_filter}")

        if params.fetch_details:
            print(f"[LinkedIn] Obteniendo detalles de {len(all_jobs)} jobs...")
            for i, job in enumerate(all_jobs):
                desc = self._get_description(job["url"])
                if desc:
                    job["description"] = desc
                if i < len(all_jobs) - 1:
                    await asyncio.sleep(REQUEST_DELAY)

        print(f"[LinkedIn] Total: {len(all_jobs)} ofertas")

        response_jobs = [
            Job(
                platform="linkedin",
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
            stored = await upsert_jobs(self.db, response_jobs, platform_name="linkedin")
            print(f"[LinkedIn] {stored} jobs nuevos guardados en DB")

        return JobResponse(source="linkedin", total=len(response_jobs), jobs=response_jobs)

    def _search_page(
        self,
        search_params: LinkedInSearchParams,
        page: int,
    ) -> list[dict]:
        params: dict[str, str] = {
            "keywords": search_params.keywords,
            "position": "1",
            "pageNum": str(page),
        }
        if search_params.location:
            params["location"] = search_params.location
        if search_params.geo_id:
            params["geoId"] = search_params.geo_id
        if search_params.time_range:
            params["f_TPR"] = search_params.time_range.value
        if search_params.job_type:
            params["f_JT"] = ",".join(jt.value for jt in search_params.job_type)
        if search_params.experience:
            params["f_E"] = ",".join(e.value for e in search_params.experience)
        if search_params.work_mode:
            params["f_WT"] = ",".join(w.value for w in search_params.work_mode)
        if search_params.easy_apply:
            params["f_AL"] = "true"
        if search_params.sort_by:
            params["sortBy"] = search_params.sort_by.value
        if search_params.company_ids:
            params["f_C"] = ",".join(search_params.company_ids)
        if search_params.distance is not None:
            params["distance"] = str(search_params.distance)

        query = urllib.parse.urlencode(params)
        status, body = self.scraper.get("www.linkedin.com", f"/jobs/search?{query}")

        if status != 200:
            print(f"  [Search] Error: {status}")
            return []

        return self._parse_cards(body)

    def _parse_cards(self, html: str) -> list[dict]:
        jobs = []
        card_re = re.compile(
            r'data-entity-urn="urn:li:jobPosting:(\d+)"(.*?)'
            r'(?=data-entity-urn="urn:li:jobPosting:|\Z)',
            re.DOTALL,
        )

        for match in card_re.finditer(html):
            job_id = match.group(1)
            card = match.group(2)

            url_m = re.search(r'href="(https?://[^"]*jobs/view/[^"]*)"', card)
            url = html_lib.unescape(url_m.group(1)).split("?")[0] if url_m else ""

            title_m = re.search(
                r'base-search-card__title[^>]*>\s*(.*?)\s*</h3>', card, re.DOTALL
            )
            title = title_m.group(1).strip() if title_m else ""

            company_m = re.search(
                r'base-search-card__subtitle.*?>\s*(?:<a[^>]*>)?\s*(.*?)\s*(?:</a>)?\s*</h4>',
                card,
                re.DOTALL,
            )
            company = company_m.group(1).strip() if company_m else ""

            loc_m = re.search(
                r'job-search-card__location[^>]*>\s*(.*?)\s*</span>', card, re.DOTALL
            )
            location = loc_m.group(1).strip() if loc_m else ""

            date_m = re.search(r'<time[^>]*datetime="([^"]*)"', card)
            posted_date = date_m.group(1) if date_m else ""

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
        host = parsed.hostname or "www.linkedin.com"
        status, body = self.scraper.get(host, parsed.path)

        if status != 200:
            return ""

        # Extraer descripción completa del contenedor show-more-less-html
        desc_m = re.search(
            r'show-more-less-html__markup[^>]*>\s*(.*?)\s*</div>', body, re.DOTALL
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
