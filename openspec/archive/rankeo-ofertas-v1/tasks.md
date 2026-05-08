# Tasks: Rankeo de Ofertas v1

## Phase 0 — Setup & Dependencies

- [x] 0.1 `requirements.txt`: add `openai`, `tenacity`, `pgvector` (SQLAlchemy adapter)
- [x] 0.2 `config.py`: add `OPENAI_API_KEY`, `OPENAI_EMBEDDING_MODEL`, `OPENAI_RERANK_MODEL`, `INTERNAL_API_KEY`, `API_KEY`, `RANK_TOP_K` (default 50), `RANK_MIN_SCORE` (default 0)
- [ ] 0.3 `.env.example`: document all 6 new vars above; remove `DEEPSEEK_API_KEY` reference
  > Note (user action required): pegar las nuevas variables en `.env` y `.env.example` manualmente — el harness no puede acceder a esas rutas. Variables a agregar: `OPENAI_API_KEY`, `OPENAI_EMBEDDING_MODEL`, `OPENAI_RERANK_MODEL`, `INTERNAL_API_KEY`, `API_KEY`, `RANK_TOP_K` (default 50), `RANK_MIN_SCORE` (default 0).
- [x] 0.4 `docker-compose.yml`: verify env vars present (already done by orchestrator — confirm no drift)
  > Verificado: `INTERNAL_API_KEY`, `API_KEY`, `OPENAI_API_KEY`, `OPENAI_EMBEDDING_MODEL` (default text-embedding-3-small), `OPENAI_RERANK_MODEL` (default gpt-4o-mini) están presentes. Sin drift.

## Phase 1 — Database Migrations

- [x] 1.1 `database/migrations/versions/003_enable_pgvector_extension.py`: `CREATE EXTENSION IF NOT EXISTS vector;` (no-op downgrade)
- [x] 1.2 `database/migrations/versions/004_add_jobs_embedding_columns.py`: columns `embedding vector(1536)`, `embedded_at`, `embedding_model`; HNSW index `(m=16, ef_construction=64)`; partial index `WHERE embedded_at IS NULL`
- [x] 1.3 `database/migrations/versions/005_create_user_profiles.py`: table per design §3 DDL + `ux_user_profiles_singleton` unique index
- [x] 1.4 `database/migrations/versions/006_create_pipeline_runs.py`: table per design §3 DDL + `ux_pipeline_runs_active_per_kind` unique partial index
- [x] 1.5 `database/migrations/versions/007_create_job_scores.py`: table per design §3 DDL + both indexes; FK to `jobs` and `user_profiles`
- [ ] 1.6 Run `alembic upgrade head` inside Docker container; verify all 5 new tables/columns visible in psql
  > Note (user action required — pending execution): `docker compose exec api alembic upgrade head`. Verificar con `\d+ jobs`, `\d user_profiles`, `\d pipeline_runs`, `\d job_scores`.

## Phase 2 — Shared Infrastructure

- [x] 2.1 `dependencies/auth.py`: `require_internal_key(x_internal_api_key: str = Header(...))` → 401 on mismatch; `require_api_key(x_api_key: str = Header(...))` → 401 on mismatch
- [x] 2.2 `dependencies/openai_client.py`: `get_openai_client() -> AsyncOpenAI` constructed once from `config.OPENAI_API_KEY`; no key in logs
- [x] 2.3 `models/database/profile.py`: SQLAlchemy `UserProfile` ORM model (all columns from 005 DDL)
- [x] 2.4 `models/database/pipeline_run.py`: SQLAlchemy `PipelineRun` ORM model + `KIND_VALUES`, `STATUS_VALUES` constants + valid-transition dict
- [x] 2.5 `models/database/job_score.py`: SQLAlchemy `JobScore` ORM model (all columns from 007 DDL)
- [x] 2.6 `models/database/job.py`: add `embedding`, `embedded_at`, `embedding_model` columns to existing `Job` model
- [x] 2.7 `models/profile.py`: Pydantic `TechnologyIn`, `AreaIn`, `LocationIn`, `SalaryIn`, `ProfileIn` (with validators: `seniority` 1-5, `salary.min >= 0`), `ProfileOut`
- [x] 2.8 `models/ranking.py`: Pydantic `LLMScoreOutput` (matching JSON Schema in design §9), `RankedJob`, `RankedJobsResponse`
- [x] 2.9 `models/pipeline_run.py`: Pydantic `TriggerResponse` (202 body), `ConflictResponse` (409 body), `RunOut`

## Phase 3 — Services

- [x] 3.1 `services/pipeline_runs.py`: `create_run(session, kind, trigger)` raising `ActiveRunConflict` on `IntegrityError`; `transition(session, run, new_status, **fields)` with FSM guard; `get_run`, `list_runs`; `InvalidTransition` exception class
- [x] 3.2 `services/embedding.py`: `embed_texts(client, texts) -> list[list[float]]` with tenacity retry + recursive chunk halving on hard failure; `embed_one` convenience wrapper; `EMBED_DIM = 1536`
- [x] 3.3 `services/profile_service.py`: `serialize_profile(profile) -> str` (deterministic JSON, sort_keys); `compute_profile_hash(profile) -> str`; `upsert_profile(session, profile_in) -> tuple[UserProfile, bool]` (singleton upsert, hash-change detection); `get_profile`; `ensure_profile_embedded(session, client) -> UserProfile`
- [x] 3.4 `services/ranking/prompts/rerank_v1.md`: verbatim prompt from design §8 with `{profile_json}` and `{offer_json}` placeholders; versioned at top
- [x] 3.5 `services/ranking/sql_filters.py`: `build_candidate_query(profile, only_new) -> Select` — programmatically appends parameterized clauses per design §10; always requires `embedding IS NOT NULL`; caps at 2000 rows; returns Select (not executed)
- [x] 3.6 `services/ranking/vector_search.py`: `vector_topk(session, profile_embedding, candidate_ids, k) -> list[tuple[int, float]]` — sets `hnsw.ef_search = 40` at session level; cosine distance via pgvector `<=>` operator
- [x] 3.7 `services/ranking/llm_reranker.py`: `rerank_jobs(client, profile, jobs, *, min_score) -> list[LLMScoreOutput]` — loads `rerank_v1.md`, builds messages with `{profile_json}` + `{offer_json}`, calls model with strict `RERANK_SCHEMA`, tenacity retry
- [x] 3.8 `services/ranking/pipeline.py`: `run_ranking(run_id, *, only_new) -> dict` — full orchestration: transition queued→running, ensure_profile_embedded, L1 sql_filters, L2 vector_topk, L3 rerank_jobs, incremental upsert (pg_insert on_conflict_do_update), stats flushed every 10 calls, marks run failed on unhandled exception
- [x] 3.9 `services/scrape_orchestrator.py`: wraps calls to `linkedin`, `chiletrabajos`, `getonbrd`, `bne` services into a single `run_scrape(run_id, platforms, query)` coroutine; aggregates stats for `pipeline_runs`

## Phase 4 — Public Routers

- [x] 4.1 `routers/profile.py`: `GET /api/v1/profile` → 200 `ProfileOut` (404 if none); `PUT /api/v1/profile` → 200 `ProfileOut`; auth via `require_api_key`
- [x] 4.2 `routers/ranking.py`: `GET /api/v1/jobs/ranked` (params: `min_score`, `limit`, `platform`, `only_new`) → 200 `RankedJobsResponse` ordered `llm_score DESC`; `POST /api/v1/jobs/{job_id}/view` → 204; auth via `require_api_key`
- [x] 4.3 `main.py`: register `routers/profile.py` and `routers/ranking.py` with prefix `/api/v1`

## Phase 5 — Internal Routers

- [x] 5.1 `routers/internal.py`: `APIRouter(prefix="/internal", dependencies=[Depends(require_internal_key)])` (single-file, not subdirectory)
- [x] 5.2 `POST /internal/scrape/run` → 202 `TriggerResponse`; creates pipeline_run of kind `scrape`; dispatches `scrape_orchestrator.run_scrape` via `BackgroundTasks`
- [x] 5.3 `POST /internal/rank/run` → 202 `TriggerResponse`; creates pipeline_run of kind `rank`; dispatches `pipeline.run_ranking` via `BackgroundTasks`
- [x] 5.4 `POST /internal/embed/backfill` → 202 `TriggerResponse`; creates pipeline_run of kind `backfill`; dispatches `embedding_backfill.run_backfill` via `BackgroundTasks`; accepts `batch_size` (1-500, default 100) and `max_jobs` (optional) body params
- [x] 5.5 `GET /internal/runs/{run_id}` → 200 `RunOut` (404 if not found); `GET /internal/runs` → 200 `list[RunOut]` with `kind` and `limit` query params
- [x] 5.6 `main.py`: include `routers/internal.router`

## Phase 6 — Scrape Orchestration Refactor

- [x] 6.1 Audit existing `routers/{linkedin,chiletrabajos,getonbrd,bne}.py`: each delegates to `XxxService(db=db).search(XxxSearchParams(...))` — extractable with thin wrapper
- [x] 6.2 Refactor each of `services/{linkedin,chiletrabajos,getonbrd,bne}.py` to expose `async def run(session, *, query, max_pages) -> dict`; old public routers deleted; new `routers/internal_scrape.py` at `GET /internal/jobs/{platform}`
- [x] 6.3 `services/scrape_orchestrator.py`: refactored to use `PLATFORM_RUNNERS` dict; stats key `total_inserted` (jobs_inserted sum); marks FAILED only if ALL platforms error

> **Decision required (flag for user):** Keep existing `GET /api/v1/jobs/{platform}` public endpoints as direct-scrape debug endpoints OR move them under `/internal/*`. Document decision in this file before implementing 6.2. Default assumption: keep as-is (debug endpoints); they do not affect the pipeline.

## Phase 7 — Manual Verification

- [x] 7.1 Create `openspec/changes/rankeo-ofertas-v1/manual-verification.md` with curl scripts for every endpoint (auth header + payload + expected status code)
- [x] 7.2 Add SQL inspection snippets: check `user_profiles`, `pipeline_runs`, `job_scores` state after each step
- [x] 7.3 Document dry-run sequence: ingest profile → trigger backfill → trigger scrape → trigger rank → poll run → inspect `GET /api/v1/jobs/ranked`
- [x] 7.4 Add n8n workflow pseudocode: two HTTP-node chains (scrape + rank) with wait+poll loops and success/failure notification
