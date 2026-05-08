# Proposal: CV Service v1

## Intent

Migrar el pipeline de generación de CVs desde `scripts/cv_generator/` (CLI standalone, sync OpenAI, batch secuencial, sin tracking) hacia `services/cv_generator/` integrado al API FastAPI, con `AsyncOpenAI`, concurrencia controlada, retry, PipelineRun tracking y triggers internos. Habilita automatización vía n8n y reduce costo LLM por prompt caching verificado.

## Scope

### In Scope
- `services/cv_generator/` con submódulos: adapter, digester, ingestor, renderer, orchestrator, filename, utils.
- `routers/internal_cv.py`: `POST /internal/cv/batch-run`, `POST /internal/cv/generate` (single por `score_id`).
- `services/retry.py`: policy tenacity compartida (refactor desde embedding.py + llm_reranker.py).
- `KIND_CV_BATCH = "cv_batch"` en `VALID_KINDS`. Stats schema `{min_score, total_pending, processed, succeeded, failed, skipped_duplicate, llm_tokens_in, llm_tokens_out, duration_s}`.
- Migration 010: `user_profiles.rut TEXT NULL UNIQUE`.
- Hook digest invalidation en `upsert_profile` cuando `embedding_hash` cambia; regen LAZY síncrona en CV call.
- Filename determinista `{nombre}_{rut}_{company}_{cargo}.pdf`. Fail-fast si `rut` falta.
- Concurrencia: `asyncio.gather` + `Semaphore(config.CV_BATCH_CONCURRENCY=5)`. Cada task abre su propia session.
- Startup check: token-count prefix (system+digest). Log warning si < 1024; padear byte-stable con bloque rules extendido.
- Config: `CV_BATCH_CONCURRENCY`, `CV_OUTPUT_DIR` (default `/app/output/cv/`).
- Scripts `scripts/cv_generator/*.py` → thin CLI wrappers (argparse → call service; cero business logic).

### Out of Scope
- Worker externo (Celery/RQ/arq).
- Cambios al PDF format/layout.
- Cambios al ranker, rerank prompt o pipelines ranking.
- Migración de `cv_path` values históricos.
- Frontend / UI.

## Capabilities

### New Capabilities
- `cv-generation`: Pipeline end-to-end de generación de CVs adaptados por oferta (adapter + digester + ingestor + renderer + orchestrator + filename rules + endpoints internos + PipelineRun tracking).

### Modified Capabilities
- `user-profile`: Agrega columna `rut` UNIQUE + hook de invalidación de `payload.digest` al upsert cuando cambia `embedding_hash`.

## Approach

FastAPI `BackgroundTasks` dispara `run_cv_batch(run_id, min_score, limit, force)` que abre `async_session`, transition a RUNNING, arma lista pendientes, ejecuta `asyncio.gather(*[worker(sid)])` con `Semaphore(5)`. Cada worker: sesión propia → adapter LLM (AsyncOpenAI + retry) → renderer PDF → UPDATE `job_scores`. Orchestrator acumula stats, transition COMPLETED|FAILED. Single endpoint reusa worker core con 1 elemento.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `services/cv_generator/` | New | 7 módulos (adapter/digester/ingestor/renderer/orchestrator/filename/utils) |
| `routers/internal_cv.py` | New | 2 POST + registro en main.py |
| `services/retry.py` | New | Policy compartida |
| `models/database/pipeline_run.py` | Modified | `KIND_CV_BATCH` const |
| `models/database/profile.py` + migration 010 | Modified+New | columna `rut` UNIQUE |
| `services/profile_service.py` | Modified | digest invalidation hook |
| `config.py` | Modified | `CV_BATCH_CONCURRENCY`, `CV_OUTPUT_DIR` |
| `scripts/cv_generator/*.py` | Modified | Rewrite como thin wrappers |
| `services/embedding.py`, `services/ranking/llm_reranker.py` | Modified | usar `services/retry.py` |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Prompt caching no activa (<1024 tokens) | Med | Startup token count + padding byte-stable |
| Session compartida entre gather tasks | High | Cada worker abre async_session() propia — test explícito |
| RUT faltante rompe filename | Med | Fail-fast en orchestrator; doc en API response 400 |
| Scripts-wrappers drift silencioso | Low | Zero business logic en scripts/; imports-only lint en review |
| Digest lazy regen agrega latencia en single CV | Low | Aceptable — gen-time es ~1s |
| Refactor retry rompe embedding/ranker | Med | Tests manuales pre/post refactor; rollback simple |

## Rollback Plan

Cambios aislados por commit. Revert en orden: scripts, services, routers, retry refactor, hook profile, migration 010 (downgrade). `KIND_CV_BATCH` const no requiere rollback DB. Ranker intacto en todo momento.

## Dependencies

- OpenAI API key vigente con acceso a gpt-4o-mini.
- Postgres + Alembic operativo.
- Tenacity ya en deps.

## Success Criteria

- [ ] `POST /internal/cv/batch-run` responde 202 + `run_id` en <500ms
- [ ] `GET /internal/runs/{id}` muestra stats cv_batch correctos
- [ ] Batch de 40 CVs completa en <3min (vs >10min actual secuencial)
- [ ] Zero imports cruzados entre `services/cv_generator/` y `services/ranking/`
- [ ] Migration 010 aplica y rollbackea limpio
- [ ] CLI scripts siguen funcionando tras rewrite (smoke test manual)
- [ ] Prompt cache hits visibles en logs OpenAI (prefix >=1024 tokens verificado)
