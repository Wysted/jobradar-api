# Manual Verification — rankeo-ofertas-v1

## Pre-requisitos

- `.env` con: `INTERNAL_API_KEY`, `API_KEY`, `OPENAI_API_KEY`, `DATABASE_URL`
- Docker + docker compose instalados
- `curl` + `jq` para inspección de responses

## Setup

```bash
docker compose up -d --build
docker compose logs api --tail 50
# verificar líneas: "Running database migrations..." y "Starting Scraping Masive API"

docker compose exec db psql -U postgres -d scraping_masive -c "\dt"
# debe listar: alembic_version, jobs, job_scores, pipeline_runs, platforms, user_profiles

docker compose exec db psql -U postgres -d scraping_masive -c "\d jobs"
# verificar columnas: embedding vector(1536), embedded_at, embedding_model

docker compose exec db psql -U postgres -d scraping_masive \
  -c "SELECT extname FROM pg_extension WHERE extname='vector';"
# debe devolver: vector

docker compose exec api alembic upgrade head
# si las migraciones no corrieron al inicio, ejecutar manualmente
```

## Escenarios

### E1 — Auth: INTERNAL vs API key están separadas

```bash
# Sin header → 422 (FastAPI: campo requerido faltante)
curl -s -o /dev/null -w "%{http_code}" \
  -X GET http://localhost:8000/api/v1/profile

# Con X-Api-Key inválida → 401
curl -s -o /dev/null -w "%{http_code}" \
  -X GET http://localhost:8000/api/v1/profile \
  -H "X-Api-Key: wrong"

# Con X-Api-Key correcta pero sin perfil creado → 404
curl -s http://localhost:8000/api/v1/profile \
  -H "X-Api-Key: $API_KEY" | jq .

# X-Internal-Api-Key correcta en /internal → 202
curl -s -X POST http://localhost:8000/internal/rank/run \
  -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"only_new": true}' | jq .

# X-Api-Key en /internal → 422 (campo x_internal_api_key requerido faltante)
curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://localhost:8000/internal/rank/run \
  -H "X-Api-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"only_new": true}'
```

**Confirma**: `API_KEY` abre `/api/v1/*`, `INTERNAL_API_KEY` abre `/internal/*`. Cruzar keys devuelve 422 (campo requerido faltante), no 401.

> **Nota**: FastAPI extrae ambos headers como dependencias required distintas. Usar `X-Api-Key` en un endpoint `/internal/*` no devuelve 401 sino 422 porque el campo `x_internal_api_key` está ausente del request.

### E2 — Profile upsert + hash

```bash
# PUT perfil inicial → 201 + embedding_hash poblado (sha256, no null)
curl -s -X PUT http://localhost:8000/api/v1/profile \
  -H "X-Api-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "headline": "Backend Engineer",
    "total_years": 8.5,
    "areas": [{"name": "Backend", "seniority": 4}],
    "technologies": [{"name": "Python", "area_name": "Backend", "seniority": 4}],
    "soft_skills": ["comunicación", "autonomía"],
    "roles_target": ["Tech Lead", "Backend Engineer"],
    "roles_excluded": [],
    "industries_preferred": [],
    "industries_excluded": [],
    "location": {
      "city": "Santiago",
      "country": "Chile",
      "willing_to_relocate": false
    },
    "work_mode": ["remote", "hybrid"],
    "salary": {
      "currency": "CLP",
      "min_monthly": 3000000,
      "expected_monthly": 4000000
    },
    "languages": [{"name": "Spanish", "level": "native"}, {"name": "English", "level": "B2"}],
    "contract_types": ["full-time"],
    "company_size_pref": [],
    "deal_breakers": []
  }' | jq '{status: .id, embedding_hash: .embedding_hash}'
# HTTP 201; embedding_hash es un sha256 hex de 64 chars (no null)

# PUT mismo payload byte-a-byte → 200 + MISMO embedding_hash
# (copiar y pegar el mismo curl; el hash debe ser idéntico)

# PUT con headline distinto → 200 + NUEVO embedding_hash
curl -s -X PUT http://localhost:8000/api/v1/profile \
  -H "X-Api-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{ ... mismo payload pero "headline": "Senior Backend Engineer" ... }' \
  | jq .embedding_hash
# debe ser distinto al anterior

# GET → 200 con todos los campos incluido embedding_hash
curl -s http://localhost:8000/api/v1/profile \
  -H "X-Api-Key: $API_KEY" | jq '{id, embedding_hash, total_years, headline}'
```

> **Nota real vs spec E2**: La primera respuesta incluye `embedding_hash` con valor (sha256), NO null. El hash se calcula en `upsert_profile()` antes de persistir (`profile_service.py:148`). El spec decía "embedding_hash=null" — eso es incorrecto.

### E3 — Embedding backfill

```bash
# Verificar jobs sin embedding
docker compose exec db psql -U postgres -d scraping_masive \
  -c "SELECT count(*) FROM jobs WHERE embedding IS NULL;"

# Trigger backfill (batch_size default es 100; 50 es válido)
curl -s -X POST http://localhost:8000/internal/embed/backfill \
  -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"batch_size": 50}' | jq .
# → 202 + {run_id, kind: "backfill", status: "queued"}

RUN_ID="<run_id del response anterior>"

# Poll hasta status=completed
curl -s http://localhost:8000/internal/runs/$RUN_ID \
  -H "X-Internal-Api-Key: $INTERNAL_API_KEY" | jq '{status, stats}'

# Verificar embeddings generados
docker compose exec db psql -U postgres -d scraping_masive \
  -c "SELECT count(*) FROM jobs WHERE embedding IS NOT NULL;"
docker compose exec db psql -U postgres -d scraping_masive \
  -c "SELECT count(*), embedding_model FROM jobs WHERE embedded_at IS NOT NULL GROUP BY embedding_model;"
# embedding_model debe ser 'text-embedding-3-small'
```

### E4 — Ranking flow end-to-end

```bash
# Trigger ranking
curl -s -X POST http://localhost:8000/internal/rank/run \
  -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"only_new": true}' | jq .
# → 202 + {run_id, kind: "rank", status: "queued"}

RUN_ID="<run_id>"

# Poll run (repetir hasta status=completed)
curl -s http://localhost:8000/internal/runs/$RUN_ID \
  -H "X-Internal-Api-Key: $INTERNAL_API_KEY" | jq '{status, stats}'
# stats debe incluir: candidates_after_filter, topk_size, llm_calls, new_scores, duration_s

# Listar jobs rankeados
curl -s "http://localhost:8000/api/v1/jobs/ranked?only_new=true&limit=20" \
  -H "X-Api-Key: $API_KEY" | jq '{total, first: .jobs[0]}'
# cada item incluye: job_id, profile_id, vector_score, llm_score,
#   matched_areas, matched_techs, missing_skills, red_flags, pitch_angles,
#   reasoning, llm_model, scored_at, viewed_at (null), title, company, location, url, platform, tags

FIRST_JOB_ID=$(curl -s "http://localhost:8000/api/v1/jobs/ranked?only_new=true&limit=1" \
  -H "X-Api-Key: $API_KEY" | jq -r '.jobs[0].job_id')

# Marcar como visto → 204
curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://localhost:8000/api/v1/jobs/$FIRST_JOB_ID/view \
  -H "X-Api-Key: $API_KEY"

# Verificar que ya no aparece con only_new=true
curl -s "http://localhost:8000/api/v1/jobs/ranked?only_new=true&limit=20" \
  -H "X-Api-Key: $API_KEY" | jq "[.jobs[] | select(.job_id == \"$FIRST_JOB_ID\")] | length"
# debe devolver 0

# Sin only_new sí aparece (viewed_at ya poblado)
curl -s "http://localhost:8000/api/v1/jobs/ranked?only_new=false&limit=20" \
  -H "X-Api-Key: $API_KEY" | jq "[.jobs[] | select(.job_id == \"$FIRST_JOB_ID\")] | .[] .viewed_at"
# debe mostrar un timestamp
```

### E5 — Concurrency: 409 en run activo

```bash
# Primer trigger → 202 + run_id A
curl -s -X POST http://localhost:8000/internal/rank/run \
  -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"only_new": true}' | jq .run_id

# Inmediatamente segundo trigger (mientras A está queued/running) → 409
curl -s -X POST http://localhost:8000/internal/rank/run \
  -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"only_new": true}' | jq .
# → 409 + {run_id: "<id de A>", kind: "rank", status: "active"}

# Esperar a que A complete
curl -s http://localhost:8000/internal/runs/<run_id_A> \
  -H "X-Internal-Api-Key: $INTERNAL_API_KEY" | jq .status
# → "completed" o "failed"

# Nuevo trigger post-compleción → 202 + run_id distinto
curl -s -X POST http://localhost:8000/internal/rank/run \
  -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"only_new": true}' | jq .run_id
# debe ser diferente de run_id A
```

> **Nota real vs spec E5**: El body 409 tiene shape `{run_id, kind, status}` — NO `{active_run_id}`. Ver `models/pipeline_run.py:ConflictResponse`.

### E6 — Scrape orchestration

```bash
# Trigger scrape (plataformas específicas)
curl -s -X POST http://localhost:8000/internal/scrape/run \
  -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"platforms": ["linkedin"], "query": "python developer"}' | jq .
# → 202 + {run_id, kind: "scrape", status: "queued"}

RUN_ID="<run_id>"

# Poll hasta completed
curl -s http://localhost:8000/internal/runs/$RUN_ID \
  -H "X-Internal-Api-Key: $INTERNAL_API_KEY" | jq '{status, stats}'
# stats.per_platform.linkedin.jobs_found, stats.per_platform.linkedin.jobs_inserted
# stats.total_inserted = suma de jobs_inserted de todos los platforms

# Verificar jobs nuevos
docker compose exec db psql -U postgres -d scraping_masive \
  -c "SELECT p.name, count(*) FROM jobs j JOIN platforms p ON j.platform_id=p.id GROUP BY p.name;"

# Concurrencia: segundo scrape durante activo → 409
# (mismo patrón que E5 pero kind="scrape")
curl -s -X POST http://localhost:8000/internal/scrape/run \
  -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"platforms": ["linkedin"], "query": "python developer"}' | jq .
# → 409 + {run_id: "<id activo>", kind: "scrape", status: "active"}
```

> **Nota**: `ScrapeRunRequest` no tiene campo `max_pages`. El orchestrator fuerza `max_pages=1` para todos los platforms en run masivo (`scrape_orchestrator.py:37`). Para más páginas usar E7 (scrape directo).

### E7 — Scrape directo (endpoint individual)

```bash
# Con X-Internal-Api-Key → 200 + {platform, jobs_found, jobs_inserted, errors}
curl -s "http://localhost:8000/internal/jobs/linkedin?query=python&max_pages=1" \
  -H "X-Internal-Api-Key: $INTERNAL_API_KEY" | jq .

# Sin header → 422 (x_internal_api_key requerido)
curl -s -o /dev/null -w "%{http_code}" \
  "http://localhost:8000/internal/jobs/linkedin?query=python"

# Con X-Api-Key (header equivocado) → 422
curl -s -o /dev/null -w "%{http_code}" \
  "http://localhost:8000/internal/jobs/linkedin?query=python" \
  -H "X-Api-Key: $API_KEY"

# Platform inválida → 422
curl -s -o /dev/null -w "%{http_code}" \
  "http://localhost:8000/internal/jobs/indeed?query=python" \
  -H "X-Internal-Api-Key: $INTERNAL_API_KEY"
```

### E8 — Validación Pydantic (perfil inválido)

```bash
# seniority fuera de rango (1-5) → 422
curl -s -o /dev/null -w "%{http_code}" \
  -X PUT http://localhost:8000/api/v1/profile \
  -H "X-Api-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "total_years": 5,
    "areas": [{"name": "Backend", "seniority": 6}],
    "location": {"city": "Santiago", "country": "Chile"}
  }'

# total_years negativo → 422
curl -s -o /dev/null -w "%{http_code}" \
  -X PUT http://localhost:8000/api/v1/profile \
  -H "X-Api-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"total_years": -1, "location": {"city": "Santiago", "country": "Chile"}}'

# Sin location (campo requerido) → 422
curl -s -o /dev/null -w "%{http_code}" \
  -X PUT http://localhost:8000/api/v1/profile \
  -H "X-Api-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"total_years": 5}'
```

## SQL — Inspección de estado post-ejecución

```sql
-- Tablas creadas (debe listar 6: alembic_version, jobs, job_scores, pipeline_runs, platforms, user_profiles)
\dt

-- Columnas embedding en jobs
\d jobs
-- buscar: embedding | vector(1536), embedded_at | timestamp with time zone, embedding_model | character varying

-- user_profiles singleton (max 1 row)
SELECT id, embedding_hash, embedded_hash,
       embedding IS NOT NULL AS has_embedding, embedding_model, created_at, updated_at
FROM user_profiles;

-- pipeline_runs: historial de runs
SELECT id, kind, status, started_at, finished_at, stats
FROM pipeline_runs
ORDER BY started_at DESC
LIMIT 10;

-- job_scores: verificar scored jobs
SELECT js.job_id, js.llm_score, js.vector_score, js.viewed_at, js.scored_at,
       j.title, j.company
FROM job_scores js
JOIN jobs j ON j.id = js.job_id
ORDER BY js.llm_score DESC
LIMIT 10;

-- Conteo por estado de embedding
SELECT
  count(*) FILTER (WHERE embedding IS NULL)     AS sin_embedding,
  count(*) FILTER (WHERE embedding IS NOT NULL) AS con_embedding,
  count(*) AS total
FROM jobs;
```

## Checks finales

### Tablas creadas
```sql
SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;
-- esperado: jobs, job_scores, pipeline_runs, platforms, user_profiles
```

### Índices críticos
```sql
SELECT indexname, tablename, indexdef
FROM pg_indexes
WHERE schemaname='public'
  AND indexname IN (
    'ux_user_profiles_singleton',
    'ux_pipeline_runs_active_per_kind',
    'ux_job_scores_job',
    'hnsw_jobs_embedding',
    'idx_job_scores_viewed_null'
  );
```

| Índice | Tabla | Tipo |
|--------|-------|------|
| `ux_user_profiles_singleton` | user_profiles | UNIQUE en `(true)` |
| `ux_pipeline_runs_active_per_kind` | pipeline_runs | UNIQUE PARTIAL en `(kind) WHERE status IN ('queued','running')` |
| `ux_job_scores_job` | job_scores | UNIQUE en `(job_id)` |
| `hnsw_jobs_embedding` | jobs | HNSW `vector_cosine_ops` m=16 ef_construction=64 |
| `idx_job_scores_viewed_null` | job_scores | PARTIAL `WHERE viewed_at IS NULL` |

> Verificar HNSW por separado (nombre puede variar):
```sql
SELECT indexname, indexdef FROM pg_indexes WHERE tablename='jobs' AND indexdef LIKE '%hnsw%';
```

### Endpoints públicos (/api/v1/*) — todos requieren X-Api-Key
| Método | Path | Esperado |
|--------|------|----------|
| GET | `/api/v1/profile` | 200 ProfileOut / 404 si no existe |
| PUT | `/api/v1/profile` | 201 (nuevo) / 200 (actualizado) |
| GET | `/api/v1/jobs/ranked` | 200 RankedJobsResponse |
| POST | `/api/v1/jobs/{id}/view` | 204 |

### Endpoints internos (/internal/*) — todos requieren X-Internal-Api-Key
| Método | Path | Esperado |
|--------|------|----------|
| POST | `/internal/rank/run` | 202 TriggerResponse / 409 ConflictResponse |
| POST | `/internal/scrape/run` | 202 TriggerResponse / 409 ConflictResponse |
| POST | `/internal/embed/backfill` | 202 TriggerResponse / 409 ConflictResponse |
| GET | `/internal/runs/{run_id}` | 200 RunOut / 404 |
| GET | `/internal/runs` | 200 list[RunOut] (params: kind, limit) |
| GET | `/internal/jobs/{platform}` | 200 dict (params: query, max_pages) |

### Endpoints viejos — deben devolver 404
```bash
# Los routers linkedin/chiletrabajos/getonbrd/bne.py fueron eliminados en Phase 6
curl -s -o /dev/null -w "%{http_code}" \
  "http://localhost:8000/api/v1/jobs/linkedin?query=python" \
  -H "X-Api-Key: $API_KEY"
# → 404
```

## Rollback

```bash
# Destruir DB completamente
docker compose down -v

# Revertir solo las 3 últimas migraciones (005/006/007)
docker compose exec api alembic downgrade -3
# después de esto: user_profiles, pipeline_runs, job_scores eliminadas;
# jobs conserva columnas embedding (migration 004 no revertida)

# Revertir todo
docker compose exec api alembic downgrade base
```

## Gaps Encontrados

### GAP-1: Body 409 usa `run_id` no `active_run_id`
- **Archivo**: `models/pipeline_run.py:13` — `ConflictResponse`
- **Encontrado**: `{run_id, kind, status}` 
- **Spec E5 decía**: `{"active_run_id": "A"}`
- **Impacto**: Bajo — shape es más informativo. Verificar que el cliente n8n parsea `run_id`.

### GAP-2: Primera respuesta PUT /profile tiene `embedding_hash` poblado, no null
- **Archivo**: `services/profile_service.py:148-152` — `upsert_profile()`
- **Encontrado**: El hash se calcula y persiste en la misma llamada que crea el registro.
- **Spec E2 decía**: "Response: 201 + embedding_hash=null"
- **Impacto**: Bajo — comportamiento es correcto y más útil. Solo el escenario de test es inexacto.

### GAP-3: `GET /internal/runs/latest` no existe
- **Archivo**: `routers/internal.py:155-166`
- **Encontrado**: Solo existe `GET /internal/runs` con query param `?kind=rank&limit=1`
- **Spec internal-api** mencionaba un endpoint `/runs/latest?kind=`.
- **Workaround**: `GET /internal/runs?kind=rank&limit=1` devuelve la lista ordenada por `started_at DESC`.
- **Impacto**: Medio — si n8n usa `/runs/latest`, cambiarlo a `/runs?kind=rank&limit=1`.

### GAP-4: `ScrapeRunRequest` no tiene `max_pages`
- **Archivo**: `routers/internal.py:29-31` — `ScrapeRunRequest`
- **Encontrado**: El body acepta `platforms` y `query`, pero NO `max_pages`. El orchestrator hardcodea `max_pages=1`.
- **Impacto**: Bajo — para controlar páginas usar `GET /internal/jobs/{platform}?max_pages=N`.

### GAP-5: Auth faltante devuelve 422, no 401
- **Archivo**: `dependencies/auth.py:13-14` — `require_internal_key(x_internal_api_key: str = Header(...))`
- **Encontrado**: FastAPI declara el header como campo requerido; su ausencia causa 422 (Unprocessable Entity), no 401.
- **Spec E1 decía**: "curl sin X-Api-Key → 401/403"
- **Impacto**: Bajo — comportamiento es standard FastAPI. Ajustar expectativas de test (422, no 401).
