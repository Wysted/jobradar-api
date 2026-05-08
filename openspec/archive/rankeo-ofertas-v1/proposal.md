# Proposal: Rankeo de Ofertas v1 (Stage 2 del pipeline)

## Intent

Stage 2 del pipeline (scraping → **ranking** → CV → submission) no existe. Hoy las 4 plataformas scrapean ofertas pero nada las ordena contra el perfil del dueno. Este cambio implementa rankeo automatizado batch de ofertas contra un perfil unico estructurado, produciendo score 0-100 + analisis estructurado (matched, missing, pitch_angles) disenado para alimentar stage 3 (CV) sin rework.

## Scope

### In Scope
- Schema de perfil estructurado JSONB (user unico): `areas` + `technologies` (taxonomia 2 niveles), `seniority` 1-5, soft_skills, roles_target/excluded, industries_preferred/excluded, location, work_mode, salary, languages, contract_types, company_size_pref, deal_breakers, headline
- Pipeline de rankeo 3 capas:
  - L1: filtros duros SQL (deal_breakers, location, work_mode, salary floor, contract_type, excluded industries/roles/languages)
  - L2: similitud coseno pgvector sobre embeddings OpenAI `text-embedding-3-small` (1536 dims) → top-K (default 50)
  - L3: rerank LLM `gpt-4o-mini` con `response_format` JSON Schema → `{ score_0_100, matched_areas, matched_techs, missing_skills, red_flags, pitch_angles, reasoning }`
- Ejecucion async + tracking: tabla `pipeline_runs (id, kind, status, started_at, finished_at, stats JSONB, error)`
- Endpoints internos (n8n): `POST /internal/scrape/run`, `POST /internal/rank/run`, `GET /internal/runs/{id}`, `GET /internal/runs/latest`
- Endpoints publicos (consumo personal): `PUT/GET /api/v1/profile`, `GET /api/v1/jobs/ranked` (params: min_score, limit, platform, only_new)
- Autenticacion: `INTERNAL_API_KEY` (`X-Internal-Api-Key`, protege `/internal/*`) + `API_KEY` (`X-Api-Key`, protege `/api/v1/*`)
- Re-embedding de perfil por cambio de hash (sha256 del payload serializado)
- Flujo de backfill para embebidos historicos (one-shot)
- Limpieza docker-compose: eliminar `DEEPSEEK_API_KEY`, agregar `OPENAI_API_KEY`, `OPENAI_EMBEDDING_MODEL`, `OPENAI_RERANK_MODEL`

### Out of Scope
- Nuevos scrapers (los 4 actuales quedan intactos)
- Stage 3 (generacion de CV)
- Stage 4 (envio de CV)
- Multi-usuario (perfil unico)
- UI / frontend
- OAuth/JWT (API keys son suficientes para app personal single-user)
- Monitoreo/alerting detallado (n8n maneja notificaciones externamente)

## Capabilities

### New Capabilities
- `user-profile`: CRUD del perfil unico estructurado + re-embedding por hash.
- `offer-embedding`: Generacion y almacenamiento de embeddings pgvector para ofertas.
- `offer-ranking`: Pipeline 3 capas (filtros SQL, similitud vectorial, rerank LLM) + persistencia de score y analisis.
- `pipeline-runs`: Tracking async (kind, status, stats) de jobs batch (scrape/rank/backfill).
- `internal-api`: Endpoints `/internal/*` protegidos por `X-Internal-Api-Key` para orquestacion n8n (trigger + poll).
- `public-api`: Endpoints `/api/v1/*` protegidos por `X-Api-Key` para consulta personal del perfil y ofertas ranqueadas.

### Modified Capabilities
- None (no hay specs previos en `openspec/specs/`; los scrapers existentes no cambian comportamiento).

## Approach

- **Orquestacion externa via n8n** (ya existe con `INTERNAL_API_KEY`). Nada de APScheduler ni cron interno.
- **Async + polling**: endpoints retornan `202 + run_id`, n8n hace GET para status. Evita timeouts HTTP en jobs largos (scrape ~1h).
- **FastAPI BackgroundTasks** dispara el trabajo dentro del container; `pipeline_runs` persiste estado.
- **OpenAI por sobre DeepSeek**: elegido por structured outputs con JSON Schema garantizado (el output alimenta stage 3, integridad del schema es no-negociable).
- **pgvector**: ya disponible en imagen `pgvector/pg16` (actualmente sin usar). Tipo de indice (ivfflat vs hnsw) se decide en sdd-design.
- **Batches desacoplados**: scrape y rank son jobs independientes. n8n orquesta: scrape 12:00 → poll → rank 14:00 → poll → notify.
- **Re-embedding de perfil**: al cambiar hash sha256, siguiente ranking run regenera embedding antes de correr L2.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `models/` | New | `UserProfile`, `OfferEmbedding`, `OfferRanking`, `PipelineRun` |
| `database/migrations` (Alembic) | New | Extension `vector`, tablas nuevas, columnas de embedding en `offers` (o tabla separada) |
| `routers/` | New | `internal.py` (scrape/run, rank/run, runs/{id}, runs/latest), `api_v1.py` (profile, jobs/ranked) |
| `services/` | New | `profile_service`, `embedding_service` (OpenAI), `ranking_service` (L1+L2+L3), `pipeline_run_service` |
| `dependencies/` | New | `verify_internal_api_key`, `verify_api_key` (dos guards distintos) |
| `main.py` | Modified | Registrar routers nuevos (internal, api_v1) |
| `docker-compose.yml` | Modified | Reemplazar `DEEPSEEK_API_KEY` por `OPENAI_API_KEY`, `OPENAI_EMBEDDING_MODEL`, `OPENAI_RERANK_MODEL` |
| `.env.example` | Modified | Mismo cambio de variables + `INTERNAL_API_KEY`, `API_KEY` |
| `requirements.txt` | Modified | `openai`, `pgvector`, `tenacity` (retry) |
| `routers/linkedin|chiletrabajos|getonbrd|bne` | Unchanged | Contrato intacto |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Drift del prompt LLM → scores inconsistentes | Med | JSON Schema estricto (`response_format`) + prompt versionado en archivo |
| Eleccion de indice pgvector (ivfflat vs hnsw) sub-optima | Med | Diferir a sdd-design; benchmark rapido con volumen real (200 ofertas/dia) |
| Rate limits OpenAI en batch grande | Low | Retry exponencial (tenacity) + backfill incremental |
| Costo OpenAI supera budget | Low | Target <$5 USD/mes; `gpt-4o-mini` + `text-embedding-3-small` proyectan ~$1/mes a 200 ofertas/dia |
| n8n caido → pipeline silencioso | Low | Aceptable para v1 personal; n8n ya tiene health checks propios |
| Cambio de perfil sin re-embedding | Med | Hash sha256 en tabla `user_profiles`; comparacion en cada rank run |
| Migracion Alembic pgvector falla | Med | Rollback plan documentado; probar en stack local antes de prod |

## Rollback Plan

1. `alembic downgrade -1` revierte migraciones nuevas (tablas `user_profiles`, `offer_embeddings`, `offer_rankings`, `pipeline_runs` + extension `vector` si no hay otros consumidores).
2. Remover routers `internal` y `api_v1` de `main.py` (un solo commit revert).
3. Restaurar `DEEPSEEK_API_KEY` en `docker-compose.yml` / `.env.example` (aunque este cambio es cosmetico — la variable no se usa hoy).
4. Los 4 scrapers existentes quedan intactos; el pipeline v0 (solo scraping) sigue funcional post-rollback.

## Dependencies

- OpenAI API key activa con acceso a `text-embedding-3-small` y `gpt-4o-mini`.
- Instancia n8n ya operativa en la infraestructura del usuario (con `INTERNAL_API_KEY` configurada).
- Imagen `pgvector/pg16` ya presente en `docker-compose.yml` (confirmado).
- Libs Python nuevas: `openai`, `pgvector` (SQLAlchemy adapter), `tenacity`.

## Success Criteria

- [ ] `GET /api/v1/jobs/ranked` retorna ofertas ordenadas por `llm_score DESC` con analisis completo (score, matched_areas, matched_techs, missing_skills, pitch_angles).
- [ ] n8n dispara `POST /internal/rank/run` y puede pollear `GET /internal/runs/{id}` hasta completion sin timeout HTTP.
- [ ] Cambio en `PUT /api/v1/profile` dispara re-embedding en el siguiente rank run (hash sha256 cambia → regenera).
- [ ] Costo mensual OpenAI proyectado <$5 USD a 200 ofertas/dia.
- [ ] Backfill de ofertas historicas corre end-to-end en un solo job sin errores.
- [ ] `pipeline_runs` refleja estado consistente (pending → running → success/failed) para todos los jobs.

## Open Questions

- Tipo de indice pgvector (ivfflat vs hnsw) y parametros (`lists`, `m`, `ef_construction`) — **diferido a sdd-design**.
- Embeddings de ofertas: columna en tabla `offers` vs tabla separada `offer_embeddings` — **diferido a sdd-design** (impacta migracion y query pattern).
- Estrategia de concurrencia si llega un segundo `POST /internal/rank/run` mientras otro esta running: rechazar con 409 vs encolar — **diferido a sdd-design**.
