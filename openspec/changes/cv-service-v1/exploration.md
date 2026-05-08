# Exploration: cv-service-v1

## Current State

### CV generator lives entirely in `scripts/cv_generator/` (CLI-only, no API integration):
- `llm_adapter.py`, `ingestion/llm_ingestor.py`, `ingestion/llm_digester.py` — 3 LLM stages (sync `OpenAI()` client).
- `generate_cv.py` — ReportLab PDF renderer with two-pass page fit.
- `batch_generate.py` — sequential `for ... await` batch; marks `job_scores.cv_generated_at`/`cv_path`/`cv_model` for idempotency.
- `profile_loader.py` — raw asyncpg conn (not SQLAlchemy session). Also has `get_digest()` that raises if `payload.digest` is missing.
- Env vars centralized in `config.py` (OPENAI_CV_MODEL, OPENAI_INGEST_MODEL, OPENAI_DIGEST_MODEL), propagated via docker-compose.

### PipelineRun lifecycle (reused by ranking, scrape, backfill):
- `models/database/pipeline_run.py`: `KIND_{SCRAPE,RANK,BACKFILL}` consts + `VALID_KINDS` set. `kind` column is `Text` (no DB enum) — adding `KIND_CV_BATCH = "cv_batch"` requires ONLY updating the Python const + `VALID_KINDS`, NO Alembic migration.
- FSM: `queued → running → completed|failed`. `ActiveRunConflict` raised if another run for same `kind` is `queued|running` (enforced by partial unique index from migration 008).
- `services/pipeline_runs.py` provides `create_run()`, `transition()`, `get_run()`, `list_runs()`.
- Pattern (ranking/pipeline.py): open `async_session()`, fetch run, `transition(RUNNING)`, do work, flush stats periodically, `transition(COMPLETED)`. On exception: `session.rollback()` FIRST, then `transition(FAILED, error=str(exc))`.

### Internal router (routers/internal.py):
- `APIRouter(prefix="/internal", dependencies=[Depends(require_internal_key)])`.
- Auth: `require_internal_key` (header `X-Internal-Key` against `config.INTERNAL_API_KEY`).
- All trigger endpoints return `202 Accepted` + `TriggerResponse{run_id, kind, status}`. `409 Conflict` with `ConflictResponse{active_run_id, kind}` when an active run exists.
- Background execution: **`BackgroundTasks.add_task(service_fn, run.id, ...)`** — FastAPI built-in. Runs in-process AFTER response is sent.
- `GET /internal/runs/{run_id}`, `/runs/latest`, `/runs?kind=...` already exist and are generic over `kind` — CV routes do NOT need their own `/runs/*` endpoints, they can reuse.

### Profile upsert hook (services/profile_service.py):
- `upsert_profile(session, profile_in)` is the SINGLE write path (called from PUT /api/v1/profile). Returns `(profile, created_bool)`.
- Already has semantic-hash logic for embedding invalidation — the digest hook is symmetric: invalidate `payload.digest` when `embedding_hash` changes.
- **Hook point**: after `session.commit()` inside `upsert_profile`, add a post-commit call to `invalidate_digest(profile)` that clears `payload["digest"]` when the semantic hash changed.
- Regeneration itself should NOT run inside the PUT (adds LLM latency to the request). Options: (a) lazy-regenerate on next CV call if `digest is None`; (b) kick off a `BackgroundTasks.add_task(refresh_digest, profile.id)`.

### Existing LLM infra to reuse:
- `dependencies/openai_client.py`: `get_openai_client()` returns singleton `AsyncOpenAI` (already async). Use this.
- Retry pattern (embedding.py, llm_reranker.py): identical tenacity policy `(APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)` + `stop_after_attempt(5)` + `wait_exponential(1, 2, 30) + wait_random(0, 1)`. Refactor into `services/retry.py` to share across CV + ranking + embedding.

## Affected Areas

- `services/cv_generator/` (NEW) — adapter, digester, ingestor, renderer, orchestrator, utils, filename.
- `routers/internal_cv.py` (NEW) OR extend `routers/internal.py` — `POST /internal/cv/batch-run`, `POST /internal/cv/generate`, optional `GET /internal/cv/digest/refresh`.
- `models/database/pipeline_run.py` — add `KIND_CV_BATCH = "cv_batch"` to `VALID_KINDS`.
- `models/database/profile.py` — add `rut: Mapped[str | None]` column.
- `database/migrations/versions/010_add_rut_to_user_profiles.py` (NEW) — Alembic migration for `rut` column + UNIQUE partial index (where not null).
- `services/profile_service.py` — digest-invalidation hook in `upsert_profile`.
- `services/retry.py` (NEW) — shared tenacity policy.
- `config.py` — optional: `CV_BATCH_CONCURRENCY` (default 5), `CV_OUTPUT_DIR` (default `scripts/cv_generator/output/batch`).
- `scripts/cv_generator/{batch_generate,generate_targeted_cv,ingestion/*}.py` — rewrite as thin CLI wrappers (`argparse` → call `services.cv_generator.*`).
- `docker-compose.yml` — already updated (CV env vars present).
- Router wiring in `main.py` / `routers/__init__.py` — register new internal CV router.

## Approaches

### Approach 1 — FastAPI `BackgroundTasks` (in-process)
Reuses the exact pattern already used by `/internal/rank/run`, `/internal/scrape/run`, `/internal/embed/backfill`. Zero new infrastructure.

- **Pros**:
  - Consistent with existing 3 internal endpoints.
  - No new deps (no Celery, no RQ, no subprocess lifecycle).
  - PipelineRun tracks status/stats for the orchestrator → `GET /internal/runs/{id}` works out of the box.
  - Idempotency via `ActiveRunConflict` on `kind="cv_batch"`.
- **Cons**:
  - If the API process is restarted mid-batch (deploy, crash, `docker compose restart`), the in-flight batch dies and the run stays in `running`. Requires a recovery sweep (same problem as current scrape/rank — not a CV-specific regression).
  - Batch shares the API event loop → heavy concurrency (>10 simultaneous OpenAI calls) could degrade p95 on normal API traffic.
- **Effort**: Low.

### Approach 2 — Detached `asyncio.create_task` + keepalive loop
Same as Approach 1 but fire-and-forget via `asyncio.create_task` instead of `BackgroundTasks`.

- **Pros**:
  - Survives the HTTP response lifecycle without FastAPI's constraints.
  - Task holds a reference to its `PipelineRun` and updates stats independently.
- **Cons**:
  - Silent failures if the task raises and nobody `await`s it — unless we explicitly `add_done_callback`.
  - No benefit over BackgroundTasks in this codebase; introduces divergence.
- **Effort**: Low but inconsistent with the rest.

### Approach 3 — External worker (RQ / Celery / arq)
Dedicated worker process pulling from a Redis queue.

- **Pros**:
  - Survives API restarts. Retry on failure. Scales horizontally.
  - Isolates LLM-heavy work from the API event loop.
- **Cons**:
  - New infra (Redis), new service in docker-compose, new deps, new deployment surface.
  - Overkill for 40 CVs/day with single-user scope.
- **Effort**: High.

## Recommendation

**Approach 1 — FastAPI BackgroundTasks.** Consistency with the existing internal endpoints is worth more than the theoretical robustness of Approach 3 at this scale. If API restarts become an operational pain later, swap the internal execution mechanism WITHOUT changing the HTTP contract (BackgroundTasks → arq, same `/internal/cv/batch-run` signature).

**Concurrency within a single batch run**: use `asyncio.gather` + `asyncio.Semaphore(config.CV_BATCH_CONCURRENCY)`. 5 concurrent OpenAI calls keeps us well under gpt-4o-mini rate limits while cutting batch time ~5x vs sequential. Each concurrent task has its own DB session from `async_session()` to avoid cross-session contention; the run-owning task flushes stats periodically.

**Prompt caching validation**: token-count the `system_prompt + digest_block` at startup. If < 1024 tokens, pad the system prompt with stable content (e.g., extended rule examples) to cross the threshold. Add a log line that reports the measured prefix token count so we can verify in prod.

**Digest auto-refresh**: add `payload.digest = None` invalidation inside `upsert_profile` when `embedding_hash` changes. Regeneration is **lazy** — next CV call sees `digest is None` and calls `services.cv_generator.digester.refresh_digest(profile)` before proceeding. No extra request-path latency, no background task to manage.

**PipelineRun stats schema for `cv_batch`**:
```
{
  "min_score": 60,
  "total_pending": 43,
  "processed": 20,
  "succeeded": 18,
  "failed": 2,
  "skipped_duplicate": 0,
  "llm_tokens_in": 0,
  "llm_tokens_out": 0,
  "duration_s": 45.2,
  "cache_hit_ratio": null  // optional, from OpenAI response usage fields
}
```

## Risks

- **`KIND_CV_BATCH` partial unique index**: migration 008 enforced uniqueness on `(kind)` where status in (queued, running). Adding a 4th kind value just works — the index is generic. Verify by reading migration 008 before proposing.
- **RUT column migration concurrency**: `UNIQUE` on a new nullable column on an existing populated table is safe (all rows get NULL, no conflicts). Add WITH `NULLS NOT DISTINCT`? NO — we want multiple NULLs allowed (most profiles won't have RUT filled yet). Default Postgres behavior (`NULLS DISTINCT`) is correct.
- **Digest invalidation false positives**: not all semantic changes affect the digest (e.g., salary preference doesn't). BUT erring on the side of refresh is cheap (~$0.0005 per regen with gpt-4o-mini) and safer. Accept over-invalidation.
- **Filename collisions still possible**: if same `rut` lacks `nombre` (edge case: ingest failed), the filename collapses. Guard: fail-fast with `ValueError("rut required for filename")` at orchestrator boundary.
- **Scripts-as-thin-wrappers drift**: if services change signatures, scripts break silently. Mitigate by making scripts import from `services.cv_generator` ONLY — zero business logic in `scripts/`.
- **Prompt-caching padding**: stable content to cross 1024 tokens must be genuinely stable (same bytes every call) — any runtime interpolation kills caching.
- **Session hygiene in concurrent batch**: each `asyncio.gather` task MUST open its own `async_session()`. Sharing a session across tasks causes `InvalidRequestError: This session is already in use`.

## Ready for Proposal

**Yes**. Next: `sdd-propose` with:
- Scope: migrate `scripts/cv_generator/` → `services/cv_generator/`, add `/internal/cv/*` routes, add RUT column + UNIQUE, add digest invalidation hook, add shared retry helper, rewrite scripts as thin CLIs.
- Non-goals: Celery/Redis worker, CV PDF format changes, ranker changes.
- Open questions for propose: (a) lazy vs eager digest regeneration; (b) whether `/internal/cv/generate` (single-score) is needed now or later; (c) CV output dir configurability.
