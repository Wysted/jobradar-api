# Archive: rankeo-ofertas-v1

**Archived**: 2026-04-17
**Status**: completed

## Summary
Pipeline de rankeo automatizado 3-capas (filtros SQL + similitud pgvector + rerank LLM) que ordena ofertas batch contra un perfil único estructurado. Implementa endpoints `/api/v1/profile` y `/api/v1/jobs/ranked` para consumo personal, y `/internal/*` para orquestación n8n. Single-user v1 con tracking async de jobs batch en tabla `pipeline_runs`.

## Phases
- Phase 0 Setup & Dependencies — done
- Phase 1 Database Migrations 003–007 — done
- Phase 2 Shared Infrastructure (models, deps) — done
- Phase 3 Services (embedding, profile, ranking pipeline, pipeline_runs) — done
- Phase 4 Public Routers (profile, ranked jobs, view) — done
- Phase 5 Internal Routers (runs trigger/status, embed backfill) — done
- Phase 6 Scrape Refactor (unified run() contract, moved to /internal/jobs/*) — done
- Phase 7 Manual Verification (8 scenarios E1-E8) — done

## Post-review Fixes (5 gaps)

| Gap | File | Fix |
|-----|------|-----|
| 1 | models/pipeline_run.py | ConflictResponse → `{active_run_id, kind}` para 409 |
| 2 | services/profile_service.py | embedding_hash=None en primera creación, se calcula on-demand en ensure_profile_embedded |
| 3 | routers/internal.py | `GET /internal/runs/latest` declarado ANTES de `/runs/{run_id}` para evitar capture por path-param |
| 4 | routers/internal.py + services/scrape_orchestrator.py | max_pages agregado a ScrapeRunRequest, forwarded a PLATFORM_RUNNERS |
| 5 | dependencies/auth.py | `Header(default=None)` + check manual → 401 en lugar de 422 FastAPI |

## Key Files (Estado Final)

### Migrations (5 nuevas)
- `alembic/versions/003_*.py` — extension vector, tabla user_profiles (singleton unique index)
- `alembic/versions/004_*.py` — tabla job_scores (upsert pattern, indexes)
- `alembic/versions/005_*.py` — job: add embedding columns + HNSW index
- `alembic/versions/006_*.py` — tabla pipeline_runs (FSM, unique partial index por kind)
- `alembic/versions/007_*.py` — refinements y partial indexes

### Services
- `services/embedding.py` — serialize_profile_for_embedding, serialize_job_for_embedding, embed_texts con retry exponencial
- `services/profile_service.py` — upsert, hash sha256, ensure_embedding
- `services/pipeline_runs.py` — create/transition/complete/fail/get/latest FSM
- `services/ranking/pipeline.py` — orquestación 3 capas (L1+L2+L3)
- `services/ranking/sql_filters.py` — predicados SQL parametrizados para L1
- `services/ranking/vector_search.py` — similitud coseno top-K con pgvector
- `services/ranking/llm_reranker.py` — gpt-4o-mini con JSON Schema estricto
- `services/ranking/prompts/rerank_v1.md` — prompt versionado

### Models (ORM + Pydantic)
- `models/database/profile.py` — ORM user_profiles
- `models/database/job_score.py` — ORM job_scores
- `models/database/pipeline_run.py` — ORM pipeline_runs
- `models/database/job.py` — modificado (add embedding cols)
- `models/profile.py` — Pydantic UserProfile schema
- `models/ranking.py` — Pydantic ranking output schemas
- `models/pipeline_run.py` — Pydantic PipelineRun, ConflictResponse

### Routers (Public + Internal)
- `routers/profile.py` — GET/PUT /api/v1/profile con X-Api-Key
- `routers/ranking.py` — GET /api/v1/jobs/ranked con params (min_score, limit, platform, only_new)
- `routers/internal.py` — POST scrape/run, POST rank/run, POST embed/backfill, GET runs/{id}, GET runs/latest con X-Internal-Api-Key
- `main.py` — registrados todos los routers

### Dependencies + Config
- `dependencies/auth.py` — verify_internal_api_key, verify_api_key con Header 401
- `dependencies/openai_client.py` — AsyncOpenAI singleton
- `config.py` — nuevas env vars (OPENAI_API_KEY, OPENAI_EMBEDDING_MODEL, OPENAI_RERANK_MODEL, INTERNAL_API_KEY, API_KEY, RANK_TOP_K, RANK_MIN_SCORE)
- `requirements.txt` — agregados openai, pgvector, tenacity
- `docker-compose.yml` — reemplazado DEEPSEEK_API_KEY por OPENAI_*, INTERNAL_API_KEY, API_KEY

### Verificación Manual
- `openspec/archive/rankeo-ofertas-v1/manual-verification.md` — 8 escenarios (E1–E8), SQL snippets, rollback docs, gaps documentados

## Verification
Manual only — no test runner. Ver `manual-verification.md` para 8 escenarios E1-E8. Ejecutar via:
```bash
docker compose up -d --build
docker compose exec api alembic upgrade head
# Luego curl con X-Api-Key y X-Internal-Api-Key
```

## Artifacts Archived
- `proposal.md` — intent, scope, risks, rollback plan
- `design.md` — architecture, DDL decisions, hash profile, SQL filters, LLM prompt
- `tasks.md` — 7 phases, 38 tasks breakdown
- `manual-verification.md` — 8 scenarios, SQL inspection, dry-run sequence
- `specs/` directory — 6 capability specs (user-profile, offer-embedding, offer-ranking, pipeline-runs, internal-api, public-api)

## Cost + Performance
- **OpenAI estimate**: ≈ $1.08/month a 200 ofertas/day (text-embedding-3-small + gpt-4o-mini)
- **pgvector index**: HNSW (m=16, ef_construction=64, hnsw.ef_search=40)
- **Retry strategy**: tenacity con exponential backoff hasta 5 intentos

## Next Steps
- **Stage 3**: Generación de CV desde top-ranked jobs (future change)
- **Possible v2**: Multi-usuario, PATCH profile, custom platform params, UI

## Known Limitations (v1)
- Single-user (perfil único)
- Sin OAuth/JWT (API keys suficientes para app personal)
- Monitoreo/alerting externo vía n8n
- Embeddings históricos requieren backfill one-shot

## Session Info
- Created: 2026-04-18
- Mode: Interactive SDD + openspec
- Model assignments: opus (plan) + sonnet (implement)
- Test strategy: Manual verification (no TDD)
