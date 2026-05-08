import html
import json
import urllib.parse
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from helpers.http_scraper import HttpScraper
from models.bne import BNESearchParams
from models.common import Job, JobResponse
from services.job_store import upsert_jobs

HOST = "www.bne.cl"
SEARCH_PATH = "/data/ofertas/buscarListas"
PAGE_SIZE = 10


async def run(
    session: AsyncSession,
    *,
    query: str | None = None,
    max_pages: int = 1,
    **kwargs,
) -> dict:
    from models.bne import BNESearchParams
    service = BNEService()
    params = BNESearchParams(keywords=query or "", max_pages=max_pages)
    result = await service.search(params)
    jobs_inserted = await upsert_jobs(session, result.jobs, platform_name="bne")
    return {
        "platform": "bne",
        "jobs_found": result.total,
        "jobs_inserted": jobs_inserted,
        "errors": [],
    }


class BNEService:
    """Servicio de scraping de ofertas de la Bolsa Nacional de Empleo."""

    def __init__(self, scraper: HttpScraper | None = None, db: AsyncSession | None = None):
        self.scraper = scraper or HttpScraper(headers={
            "user-agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
            ),
            "accept": "application/json, text/javascript, */*; q=0.01",
            "x-requested-with": "XMLHttpRequest",
            "referer": "https://www.bne.cl/ofertas",
        })
        self.db = db

    async def search(self, params: BNESearchParams) -> JobResponse:
        all_jobs: list[dict] = []
        seen_ids: set[str] = set()

        print(f'[BNE] Buscando: "{params.keywords}"')

        for page in range(1, params.max_pages + 1):
            print(f"[BNE] Scrapeando página {page}...")
            jobs, total_pages = self._search_page(params, page)

            if not jobs:
                print("[BNE] Sin resultados, deteniendo paginación")
                break

            new_jobs = [j for j in jobs if j["job_id"] not in seen_ids]
            for j in new_jobs:
                seen_ids.add(j["job_id"])

            all_jobs.extend(new_jobs)
            print(f"  Página {page}/{total_pages}: {len(new_jobs)} nuevos ({len(jobs)} total)")

            if page >= total_pages:
                break

        print(f"[BNE] Total: {len(all_jobs)} ofertas")

        response_jobs = [
            Job(
                platform="bne",
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
            stored = await upsert_jobs(self.db, response_jobs, platform_name="bne")
            print(f"[BNE] {stored} jobs nuevos guardados en DB")

        return JobResponse(source="bne", total=len(response_jobs), jobs=response_jobs)

    def _search_page(self, params: BNESearchParams, page: int) -> tuple[list[dict], int]:
        query = self._build_query(params, page)
        path = f"{SEARCH_PATH}?{query}"

        print(f"[BNE] GET https://{HOST}{path}")
        status, body = self.scraper.get(HOST, path)
        print(f"[BNE] Respuesta HTTP: {status}, body length: {len(body)} chars")

        if status != 200:
            print(f"  [BNE] Error HTTP: {status}")
            print(f"  Body: {body[:500]}")
            return [], 0

        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            print(f"  [BNE] Error al parsear JSON: {e}")
            return [], 0

        return self._parse_results(data)

    def _build_query(self, params: BNESearchParams, page: int) -> str:
        parts: list[str] = ["mostrar=empleo"]

        # Comas separan tags (AND), sin comas se envía como frase única
        if params.keywords:
            if "," in params.keywords:
                for kw in params.keywords.split(","):
                    kw = kw.strip()
                    if kw:
                        parts.append(f"textoLibre={urllib.parse.quote(kw, safe='')}")
            else:
                parts.append(f"textoLibre={urllib.parse.quote(params.keywords, safe='')}")

        for rid in params.region_ids:
            parts.append(f"idRegion={rid}")
        for ws in params.work_schedule:
            parts.append(f"idTipoJornada={ws}")
        for ct in params.contract_type:
            parts.append(f"idTipoContrato={ct}")
        for el in params.education_level:
            parts.append(f"idNivelEducacional={el}")
        for eg in params.employment_group:
            parts.append(f"idGrupoEmpleo={eg}")

        if params.date_filter:
            date_from = datetime.now() - timedelta(days=params.date_filter)
            parts.append(f"fechaIniPublicacion={urllib.parse.quote(date_from.strftime('%d/%m/%Y'), safe='')}")

        parts.append(f"numPaginaRecuperar={page}")
        parts.append(f"numResultadosPorPagina={PAGE_SIZE}")
        parts.append("clasificarYPaginar=true")

        return "&".join(parts)

    def _parse_results(self, data: dict) -> tuple[list[dict], int]:
        pagina = data.get("paginaOfertas", {})
        items = pagina.get("resultados", [])
        total_pages = pagina.get("numPaginasTotal", 0)

        jobs: list[dict] = []
        for item in items:
            job_id = str(item.get("id", ""))
            title = html.unescape(item.get("titulo", ""))

            if not job_id or not title:
                continue

            company = html.unescape(item.get("empresa", ""))
            region = item.get("region", "")
            comuna = item.get("comuna", "")
            location = f"{comuna}, {region}" if comuna and region else region or comuna
            posted_date = item.get("fecha", "")
            description = html.unescape(item.get("descripcion", ""))
            codigo = item.get("codigo", job_id)
            url = f"https://{HOST}/ofertas/{codigo}"

            jobs.append({
                "job_id": job_id,
                "title": title,
                "company": company,
                "location": location,
                "posted_date": str(posted_date),
                "url": url,
                "description": description,
            })

        print(f"[BNE] _parse_results: {len(jobs)} jobs válidos de {len(items)} items")
        return jobs, total_pages
