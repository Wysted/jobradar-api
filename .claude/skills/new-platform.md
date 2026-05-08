---
name: new-platform
description: Genera scaffolding para una nueva plataforma de scraping de jobs
user_invocable: true
arguments:
  - name: platform_name
    description: Nombre de la plataforma (ej. indeed, getonboard)
    required: true
---

# Skill: Scaffolding de nueva plataforma

Genera los archivos necesarios para agregar soporte de scraping a una nueva plataforma de ofertas laborales.

## Instrucciones

El argumento recibido es el nombre de la plataforma (en minusculas, sin espacios). Ejemplo: `indeed`, `getonboard`.

Antes de generar, investiga la plataforma objetivo:
1. Visita el sitio web de la plataforma para entender su estructura
2. Determina si expone una **API JSON publica** o si requiere **scraping HTML**
3. Identifica los filtros disponibles (tipo contrato, ubicacion, fecha, etc.)
4. Esto define cual patron de servicio usar (ver seccion Servicios)

Usa como referencia estos archivos segun el tipo:
- **HTML scraping**: `services/linkedin.py`, `services/chiletrabajos.py`
- **JSON API**: `services/getonbrd.py`, `services/bne.py`
- **Models**: `models/linkedin.py` (enums str), `models/bne.py` (enums int, constantes)
- **Routers**: `routers/bne.py` (patron completo con DB)

## Archivos a generar

### 1. `models/{platform_name}.py`

```python
from enum import Enum  # o IntEnum segun la plataforma

from pydantic import BaseModel, Field

# Definir enums especificos de {platform_name} basados en los filtros reales
# Usar str+Enum para valores string, IntEnum para valores numericos
# Ejemplo:
# class TimeFilter(str, Enum):
#     LAST_24H = "1"
#     LAST_7D = "7"

# Si la plataforma usa IDs fijos para ubicaciones, definir como dict constante:
# LOCATION_IDS: dict[str, str] = { "santiago": "123", ... }


class {PlatformName}SearchParams(BaseModel):
    keywords: str = ""
    location: str = ""
    max_pages: int = Field(default=1, ge=1, le=10)
    # fetch_details: bool = True  # Solo si el listado NO incluye descripcion
    # Agregar filtros especificos descubiertos en la investigacion
```

### 2. `services/{platform_name}.py`

Hay dos patrones segun el tipo de plataforma:

#### Patron A: HTML scraping (como LinkedIn/ChileTrabajos)

```python
import asyncio
import html as html_lib
import re
import urllib.parse

from sqlalchemy.ext.asyncio import AsyncSession

from helpers.http_scraper import HttpScraper
from models.common import Job, JobResponse
from models.{platform_name} import {PlatformName}SearchParams
from services.job_store import upsert_jobs

REQUEST_DELAY = 1.5


class {PlatformName}Service:

    def __init__(self, scraper: HttpScraper | None = None, db: AsyncSession | None = None):
        self.scraper = scraper or HttpScraper()
        self.db = db

    async def search(self, params: {PlatformName}SearchParams) -> JobResponse:
        all_jobs: list[dict] = []
        seen_ids: set[str] = set()

        print(f'[{PlatformName}] Buscando: "{params.keywords}"')

        for page in range(params.max_pages):
            jobs = self._search_page(params, page)
            if not jobs:
                break
            new_jobs = [j for j in jobs if j["job_id"] not in seen_ids]
            for j in new_jobs:
                seen_ids.add(j["job_id"])
            all_jobs.extend(new_jobs)
            print(f"  Pagina {page}: {len(new_jobs)} nuevos ({len(jobs)} total)")
            if len(jobs) < PAGE_SIZE:
                break

        if params.fetch_details:
            print(f"[{PlatformName}] Obteniendo detalles de {len(all_jobs)} jobs...")
            for i, job in enumerate(all_jobs):
                desc = self._get_description(job["url"])
                if desc:
                    job["description"] = desc
                if i < len(all_jobs) - 1:
                    await asyncio.sleep(REQUEST_DELAY)

        print(f"[{PlatformName}] Total: {len(all_jobs)} ofertas")

        response_jobs = [
            Job(
                platform="{platform_name}",
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
            stored = await upsert_jobs(self.db, response_jobs, platform_name="{platform_name}")
            print(f"[{PlatformName}] {stored} jobs nuevos guardados en DB")

        return JobResponse(source="{platform_name}", total=len(response_jobs), jobs=response_jobs)

    def _search_page(self, params, page: int) -> list[dict]:
        # TODO: Construir URL y query params
        # status, body = self.scraper.get(HOST, path)
        # return self._parse_cards(body)
        pass

    def _parse_cards(self, html: str) -> list[dict]:
        # TODO: Implementar parsing HTML del listado de {platform_name}
        pass

    def _get_description(self, job_url: str) -> str:
        # TODO: Implementar parsing HTML de detalle de {platform_name}
        pass
```

#### Patron B: JSON API (como GetOnBrd/BNE)

```python
import json
import urllib.parse

from sqlalchemy.ext.asyncio import AsyncSession

from helpers.http_scraper import HttpScraper
from models.common import Job, JobResponse
from models.{platform_name} import {PlatformName}SearchParams
from services.job_store import upsert_jobs

HOST = "api.{platform_name}.com"


class {PlatformName}Service:

    def __init__(self, scraper: HttpScraper | None = None, db: AsyncSession | None = None):
        self.scraper = scraper or HttpScraper(headers={
            "user-agent": "Mozilla/5.0",
            "accept": "application/json",
        })
        self.db = db

    async def search(self, params: {PlatformName}SearchParams) -> JobResponse:
        all_jobs: list[dict] = []
        seen_ids: set[str] = set()

        print(f'[{PlatformName}] Buscando: "{params.keywords}"')

        for page in range(params.max_pages):
            jobs = self._search_page(params, page)
            if not jobs:
                break
            new_jobs = [j for j in jobs if j["job_id"] not in seen_ids]
            for j in new_jobs:
                seen_ids.add(j["job_id"])
            all_jobs.extend(new_jobs)
            print(f"  Pagina {page}: {len(new_jobs)} nuevos ({len(jobs)} total)")
            if len(jobs) < PAGE_SIZE:
                break

        print(f"[{PlatformName}] Total: {len(all_jobs)} ofertas")

        response_jobs = [
            Job(
                platform="{platform_name}",
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
            stored = await upsert_jobs(self.db, response_jobs, platform_name="{platform_name}")
            print(f"[{PlatformName}] {stored} jobs nuevos guardados en DB")

        return JobResponse(source="{platform_name}", total=len(response_jobs), jobs=response_jobs)

    def _search_page(self, params, page: int) -> list[dict]:
        # TODO: Construir query params y hacer request
        # status, body = self.scraper.get(HOST, path)
        # data = json.loads(body)
        # return self._parse_response(data)
        pass

    def _parse_response(self, data: dict | list) -> list[dict]:
        # TODO: Implementar parsing de respuesta JSON de {platform_name}
        pass
```

### 3. `routers/{platform_name}.py`

```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.{platform_name} import {PlatformName}SearchParams
from models.common import JobResponse
from services.{platform_name} import {PlatformName}Service

router = APIRouter(prefix="/api/v1", tags=["{platform_name}"])


@router.get("/jobs/{platform_name}", response_model=JobResponse)
async def {platform_name}_jobs(
    keywords: str = Query(default="", description="Texto de busqueda."),
    location: str = Query(default="", description="Ubicacion."),
    max_pages: int = Query(default=1, ge=1, le=10, description="Maximo de paginas a scrapear."),
    # TODO: Agregar query params para cada filtro del SearchParams
    db: AsyncSession = Depends(get_db),
):
    params = {PlatformName}SearchParams(
        keywords=keywords,
        location=location,
        max_pages=max_pages,
    )
    service = {PlatformName}Service(db=db)
    return await service.search(params)
```

### 4. Migracion `database/migrations/versions/NNN_add_{platform_name}_platform.py`

Crear una migracion Alembic para registrar la plataforma en la tabla `platforms`. Seguir el patron de `002_add_bne_platform.py`:

```python
"""Add {platform_name} platform

Revision ID: {auto}
"""

from alembic import op

revision = "NNN"
down_revision = "<previous_revision>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("INSERT INTO platforms (name) VALUES ('{platform_name}')")


def downgrade() -> None:
    op.execute("DELETE FROM platforms WHERE name = '{platform_name}'")
```

### 5. Modificar `main.py`

Agregar:
```python
from routers.{platform_name} import router as {platform_name}_router
```
y:
```python
app.include_router({platform_name}_router)
```

## Importante

- NO crear clases base abstractas ni herencia
- Los `SearchParams` son especificos de cada plataforma
- Usar `Job` y `JobResponse` de `models.common`
- Siempre inyectar `db: AsyncSession` via `Depends(get_db)` en el router y pasarlo al service
- Siempre incluir `upsert_jobs` en el service para persistir en DB
- La plataforma DEBE existir en la tabla `platforms` (crear migracion)
- Dejar TODOs claros en parsing (HTML o JSON), URLs base, y filtros
- Seguir el mismo patron de logging con print()
- Elegir patron A (HTML) o B (JSON API) segun la investigacion de la plataforma
- Si la API JSON ya retorna descripcion, NO agregar `fetch_details` ni `_get_description`
