# Backend — scraping_jobs

API en FastAPI que recolecta ofertas laborales de portales de empleo (orientado a Chile), las compara contra un perfil de usuario mediante embeddings y un re-ranking con LLM, y genera CVs en PDF adaptados a cada oferta.

El frontend vive en un repositorio separado: `<URL del repo frontend>`.

## Funcionalidades

- **Scrapers** (`PLATFORM_RUNNERS`): LinkedIn, ChileTrabajos, GetOnBrd y BNE.
- **Pipeline de ranking**: filtro SQL, top-K por similitud vectorial (pgvector) y re-ranking con LLM por lotes. Guarda un puntaje 0-100, áreas/tecnologías en común, habilidades faltantes, alertas y ángulos de pitch.
- **Pipeline de refresh**: una sola corrida encadena scrape, embeddings pendientes y ranking. Usa los ajustes de búsqueda persistidos (queries, ubicación, páginas, plataformas).
- **Seguimiento de ofertas**: marcar como vista y estado `new | interested | applied | discarded`.
- **Generador de CVs**: ingesta un CV en PDF al perfil y produce PDFs dirigidos por oferta (individual o por lote).
- **Script de evaluación JEV**: compara el puntaje del re-ranking con TypeSafe JEV (`scripts/jev/`). No está integrado al pipeline.
- Las corridas largas se ejecutan en segundo plano con una máquina de estados (`pipeline_runs`); no puede haber dos corridas incompatibles a la vez (responde `409`).

## Stack y requisitos

- Python 3.13, FastAPI, SQLAlchemy 2.0 async (asyncpg), Alembic, pgvector, ReportLab, tenacity.
- PostgreSQL 16 con la extensión `pgvector` (imagen `pgvector/pgvector:pg16` en el compose).
- Docker y Docker Compose (camino principal).
- `OPENAI_API_KEY` (embeddings, ranking, CVs e ingesta).
- Opcional: `TYPESAFE_API_KEY`, solo para `scripts/jev/`.

## Instalación

### Con Docker Compose

```bash
cp .env.example .env
# Edita .env: API_KEY, INTERNAL_API_KEY y OPENAI_API_KEY como mínimo
docker compose up --build
```

`entrypoint.sh` ejecuta `alembic upgrade head` y luego arranca `uvicorn main:app` (con `--reload`) en `PORT` (por defecto 8000). El código se monta en el contenedor (`.:/app`), y `output/` y `logs/` se persisten en el host.

Documentación interactiva: `http://localhost:8000/docs`.

### Local (sin Docker)

Requiere un PostgreSQL con pgvector ya disponible.

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Ajusta DATABASE_URL, CV_OUTPUT_DIR, TEMP_DIR y LOG_DIR a rutas locales
set -a && source .env && set +a
alembic upgrade head
uvicorn main:app --port 8000
```

Las migraciones se escriben a mano en `database/migrations/versions/` (`NNN_descripcion.py`).

## Configuración

Todas las variables están en `.env.example` y se leen en `config.py`. Los valores del ejemplo son solo placeholders.

### Base de datos y servidor

| Variable | Descripción | Requerida |
|---|---|---|
| `DATABASE_URL` | URL SQLAlchemy asyncpg. En compose se fija a `db:5432`. | Sí |
| `PORT` | Puerto de la API (default 8000). | No |
| `TEMP_DIR` / `LOG_DIR` | Directorios temporales y de logs. | No |

### Autenticación

| Variable | Descripción | Requerida |
|---|---|---|
| `API_KEY` | Clave para `/api/v1/*` (header `X-Api-Key`). | Sí |
| `INTERNAL_API_KEY` | Clave para `/internal/*` (header `X-Internal-Api-Key`). | Sí |

### OpenAI y ranking

| Variable | Descripción | Default |
|---|---|---|
| `OPENAI_API_KEY` | Clave de OpenAI. **Requerida.** | — |
| `OPENAI_EMBEDDING_MODEL` | Modelo de embeddings. | `text-embedding-3-small` |
| `OPENAI_RERANK_MODEL` | Modelo del re-ranking. | `gpt-4o-mini` |
| `RANK_TOP_K` | Ofertas que pasan del filtro vectorial al LLM. | `50` |
| `RANK_MIN_SCORE` | Puntaje mínimo configurado para el ranking. | `0` |

### Generador de CVs

| Variable | Descripción | Default |
|---|---|---|
| `OPENAI_CV_MODEL` | Modelo que redacta el CV dirigido. | `gpt-4o-mini` |
| `OPENAI_INGEST_MODEL` | Modelo que extrae el perfil desde un PDF. | `gpt-4o-mini` |
| `OPENAI_DIGEST_MODEL` | Modelo que genera el digest del perfil. | `gpt-4o-mini` |
| `CV_BATCH_CONCURRENCY` | CVs generados en paralelo en un lote. | `5` |
| `CV_OUTPUT_DIR` | Directorio de salida de los PDFs. | `/app/output/cv` |

### Refresh pipeline

Valores por defecto; los ajustes guardados con `PUT /api/v1/settings/search` los reemplazan.

| Variable | Descripción | Default |
|---|---|---|
| `REFRESH_QUERIES` | Queries separadas por coma. | `desarrollador full stack,desarrollador backend,desarrollador frontend` |
| `REFRESH_LOCATION` | Ubicación de la búsqueda. | `Chile` |
| `REFRESH_MAX_PAGES` | Páginas por query. | `2` |

### TypeSafe JEV (opcional, solo scripts/jev)

| Variable | Descripción | Default |
|---|---|---|
| `TYPESAFE_API_KEY` | Clave de TypeSafe. Sin ella el script no corre. | vacío |
| `JEV_MODEL` | Modelo JEV fijado. | `jev-1.13.0` |
| `JEV_PRICE_PER_MTOK_INPUT` | Precio por millón de tokens, para estimar costo. | `0.042` |
| `JEV_MAX_CONCURRENCY` | Peticiones concurrentes. | `20` |
| `JEV_DESCRIPTION_MAX_CHARS` / `JEV_DESCRIPTION_HEAD_CHARS` | Largo máximo del extracto de la oferta y caracteres iniciales conservados. | `2000` / `500` |
| `JEV_WEIGHT_TECH` / `_AREA` / `_ROLE` / `_SENIORITY` | Pesos del puntaje compuesto. | `0.40` / `0.20` / `0.20` / `0.20` |
| `JEV_BLOCKER_NOUL_THRESHOLD`, `JEV_CHOICE_BLOCKER_CONFIDENCE` | Umbrales de confianza para descartar sin LLM. | `0.85`, `0.80` |
| `JEV_DISCARD_SCORE`, `JEV_DISCARD_UPPER_QUANTILE` | Puntaje y cuantil de descarte. | `35`, `0.90` |
| `JEV_YEARS_GAP_TOLERANCE`, `JEV_YEARS_GAP_SENIORITY_FIT` | Tolerancia y penalización por brecha de años. | `1.0`, `0.25` |
| `JEV_LANGUAGE_GAP_BLOCKS` | Si la brecha de idioma bloquea. | `false` |

## Autenticación

| Prefijo | Header | Variable | Uso |
|---|---|---|---|
| `/api/v1/*` | `X-Api-Key` | `API_KEY` | Clientes (frontend, curl). Routers: profile, ranking, pipeline, settings. |
| `/internal/*` | `X-Internal-Api-Key` | `INTERNAL_API_KEY` | Automatizaciones (n8n, cron). Routers: internal, internal_scrape, internal_cv. |

Una clave ausente o inválida responde `401`.

## API

### Pública (`X-Api-Key`)

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/v1/profile` | Obtiene el perfil (`404` si no existe). |
| PUT | `/api/v1/profile` | Crea o reemplaza el perfil (`201` al crear, `200` al reemplazar). |
| PATCH | `/api/v1/profile` | Actualización parcial del perfil. |
| GET | `/api/v1/jobs/ranked` | Lista ofertas rankeadas. Query: `only_new`, `limit`, `offset`, `min_score`, `status`. |
| PUT | `/api/v1/jobs/{job_id}/status` | Cambia el estado (`new`, `interested`, `applied`, `discarded`). |
| POST | `/api/v1/jobs/{job_id}/view` | Marca la oferta como vista (`204`). |
| DELETE | `/api/v1/jobs/{job_id}/view` | Quita la marca de vista (`204`). |
| POST | `/api/v1/pipeline/refresh` | Lanza scrape + embeddings + ranking (`202`, `409` si hay una corrida activa). |
| GET | `/api/v1/pipeline/runs/latest` | Última corrida (`kind=refresh`). |
| GET | `/api/v1/pipeline/runs/{run_id}` | Estado de una corrida. |
| GET | `/api/v1/settings/search` | Ajustes de búsqueda y plataformas disponibles. |
| PUT | `/api/v1/settings/search` | Actualiza queries, ubicación, páginas y plataformas. |

### Interna (`X-Internal-Api-Key`)

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/internal/scrape/run` | Scrape en segundo plano. Body: `platforms`, `query`, `max_pages`, `location` (`202`/`409`). |
| POST | `/internal/embed/backfill` | Calcula embeddings pendientes. Body: `batch_size`, `max_jobs` (`202`/`409`). |
| POST | `/internal/rank/run` | Ejecuta el ranking. Body: `only_new`, `skip_already_scored` (`202`/`409`). |
| GET | `/internal/runs` | Lista corridas. Query: `kind`, `limit`. |
| GET | `/internal/runs/latest` | Última corrida (filtro opcional `kind`). |
| GET | `/internal/runs/{run_id}` | Estado de una corrida. |
| GET | `/internal/jobs/{platform}` | Scrape síncrono de una plataforma (`linkedin`, `chiletrabajos`, `getonbrd`, `bne`). Query: `query`, `max_pages`. |
| POST | `/internal/cv/batch-run` | Genera CVs por lote en segundo plano. Body: `min_score`, `limit`, `force` (`202`/`409`). |
| POST | `/internal/cv/generate` | Genera el CV de un puntaje. Body: `{"score_id": "<uuid>"}`. |

## Flujo de uso típico

```bash
API=http://localhost:8000
KEY=<tu API_KEY>
IKEY=<tu INTERNAL_API_KEY>

# 1. Cargar el perfil (ver models/profile.py para el esquema completo)
curl -X PUT $API/api/v1/profile -H "X-Api-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"total_years": 3, "location": {...}, "areas": [], "technologies": []}'

# 2. Ajustar la búsqueda (opcional)
curl -X PUT $API/api/v1/settings/search -H "X-Api-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"queries": ["desarrollador backend"], "location": "Chile", "max_pages": 2, "platforms": ["getonbrd", "chiletrabajos"]}'

# 3. Refresh: scrape + embeddings + ranking
curl -X POST $API/api/v1/pipeline/refresh -H "X-Api-Key: $KEY"
curl $API/api/v1/pipeline/runs/latest -H "X-Api-Key: $KEY"

# 4. Ver las ofertas rankeadas
curl "$API/api/v1/jobs/ranked?min_score=60&limit=20" -H "X-Api-Key: $KEY"

# 5. Marcar una oferta
curl -X PUT $API/api/v1/jobs/<job_id>/status -H "X-Api-Key: $KEY" \
  -H "Content-Type: application/json" -d '{"status": "interested"}'

# 6. Generar CVs dirigidos (lote o individual)
curl -X POST $API/internal/cv/batch-run -H "X-Internal-Api-Key: $IKEY" \
  -H "Content-Type: application/json" -d '{"min_score": 60, "limit": 20}'
curl -X POST $API/internal/cv/generate -H "X-Internal-Api-Key: $IKEY" \
  -H "Content-Type: application/json" -d '{"score_id": "<uuid>"}'
```

Los pasos del pipeline también pueden ejecutarse por separado con `/internal/scrape/run`, `/internal/embed/backfill` y `/internal/rank/run`.

## Scripts

Se ejecutan dentro del contenedor (`docker compose exec api ...`) desde la raíz del backend.

| Script | Descripción |
|---|---|
| `scripts/cv_generator/ingestion/ingest_cv.py` | Extrae un perfil desde un PDF con el LLM. Flags: `--pdf` (requerido), `--persist`, `--profile-id`, `--linkedin`, `--github`, `--portfolio`, `--output-json`. |
| `scripts/cv_generator/ingestion/refresh_digest.py` | Regenera el digest de un perfil. Flag: `--profile-id` (requerido). |
| `scripts/cv_generator/generate_targeted_cv.py` | Genera el CV de un puntaje. Flag: `--score-id` (requerido). |
| `scripts/cv_generator/batch_generate.py` | Genera CVs por lote. Flags: `--min-score` (60), `--limit` (100), `--force`, `--score-id`. |
| `scripts/jev/eval_rerank.py` | Evalúa TypeSafe JEV contra los puntajes del re-ranking. Flags: `--limit`, `--top-k`, `--dry-run`, `--reverse-choice-options`. Requiere `TYPESAFE_API_KEY`. |

```bash
docker compose exec api python scripts/cv_generator/ingestion/ingest_cv.py --pdf <ruta.pdf> --persist
docker compose exec api python scripts/cv_generator/batch_generate.py --min-score 70 --limit 10
docker compose exec api python scripts/jev/eval_rerank.py --dry-run --limit 20
```

Los CVs y demás artefactos generados (`output/`, `scripts/cv_generator/output/`) están en `.gitignore` porque contienen datos personales. No los subas al repositorio.

## Estructura

```
backend/
├── main.py                  # App FastAPI y registro de routers
├── config.py                # Variables de entorno
├── entrypoint.sh            # alembic upgrade head + uvicorn
├── docker-compose.yml       # db (pgvector) + api
├── routers/                 # Endpoints (profile, ranking, pipeline, settings, internal*)
├── services/                # Lógica: scrapers por plataforma, embeddings, refresh, ranking/, cv_generator/
├── models/                  # Esquemas Pydantic y modelos ORM (models/database/)
├── database/                # Engine, base y migraciones Alembic (migrations/versions/)
├── dependencies/            # Auth por API key y cliente OpenAI
├── helpers/                 # Cliente HTTP para scraping
├── scripts/                 # CLIs: cv_generator/ y jev/
└── openspec/                # Specs de comportamiento existente
```
