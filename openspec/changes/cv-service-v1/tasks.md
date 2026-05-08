# Tasks: CV Service v1

## Phase 1: Shared infra

- [x] 1.1 Create `services/retry.py` with `openai_retry_policy` dict (tenacity + OpenAI exception types).
- [x] 1.2 Refactor `services/embedding.py` to import `openai_retry_policy` from `services.retry` (drop local copy).
- [x] 1.3 Refactor `services/ranking/llm_reranker.py` to import `openai_retry_policy` from `services.retry`.
- [x] 1.4 Add `tiktoken` to `requirements.txt`.

## Phase 2: Models + migration

- [x] 2.1 Add `KIND_CV_BATCH = "cv_batch"` to `models/database/pipeline_run.py` and include in `VALID_KINDS`.
- [x] 2.2 Add `rut: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)` to `models/database/profile.py`.
- [x] 2.3 Add `rut: str | None = None` to `models/profile.py::ProfileIn` and `ProfileOut` with `@field_validator` calling `validate_rut`.
- [x] 2.4 Create `database/migrations/versions/010_add_rut_to_user_profiles.py`: ADD COLUMN rut TEXT NULL + UNIQUE constraint `uq_user_profiles_rut`. Downgrade reverses.

## Phase 3: CV generator service (core)

- [x] 3.1 Create `services/cv_generator/__init__.py` empty.
- [x] 3.2 Create `services/cv_generator/validators.py` with `normalize_rut(raw)` and `validate_rut(rut)` (mod-11).
- [x] 3.3 Create `services/cv_generator/filename.py` with `slugify(text)` (NFKD + ascii + regex) and `build_filename(profile, job)` (fail-fast if rut/nombre missing).
- [x] 3.4 Create `services/cv_generator/renderer.py` moving `generate_pdf`, `build_styles`, `build_story`, `_two_col`, `_section`, `_bullets`, `_contact_line` from `scripts/cv_generator/generate_cv.py`.
- [x] 3.5 Create `services/cv_generator/adapter.py` with async `adapt_profile_to_job(client, digest, job, score)` using `AsyncOpenAI` + `@retry(**openai_retry_policy)` + `_merge_skill_categories`.
- [x] 3.6 Create `services/cv_generator/digester.py` with async `refresh_digest(client, payload)` + persistence helper.
- [x] 3.7 Create `services/cv_generator/ingestor.py` with async `ingest_cv_text(client, text, known_links)`.
- [x] 3.8 Create `services/cv_generator/token_check.py` with `measure_prefix_tokens(model, system, digest_sample)` using tiktoken.
- [x] 3.9 Create `services/cv_generator/orchestrator.py` with `run_cv_batch(run_id, min_score, limit, force)` (gather+Semaphore+per-worker session+stats flush) and `generate_one(score_id, session=None)`.

## Phase 4: Profile service hook

- [x] 4.1 Modify `services/profile_service.py::upsert_profile`: when `embedding_hash` changes on existing profile, set `existing.payload["digest"] = None` before commit.

## Phase 5: Routes + wiring

- [x] 5.1 Create `routers/internal_cv.py` with `POST /internal/cv/batch-run`, `POST /internal/cv/generate`; auth via `require_internal_key`.
- [x] 5.2 Modify `main.py`: `app.include_router(internal_cv_router)`.
- [x] 5.3 Add Pydantic models `CVBatchRequest`, `CVGenerateRequest`, `CVGenerateResponse` in `models/pipeline_run.py` or new `models/cv.py`.

## Phase 6: Config

- [x] 6.1 Add `CV_BATCH_CONCURRENCY: int = int(os.environ.get(..., "5"))` and `CV_OUTPUT_DIR: Path = Path(...)` to `config.py`.
- [x] 6.2 Modify `docker-compose.yml`: propagate `CV_BATCH_CONCURRENCY`, `CV_OUTPUT_DIR`, add volume `./output:/app/output`.
- [x] 6.3 Register startup prompt-cache token check in FastAPI `lifespan` in `main.py`; log warning if <1024 tokens.

## Phase 7: Scripts rewrite (thin wrappers)

- [x] 7.1 Rewrite `scripts/cv_generator/batch_generate.py` as thin argparse wrapper → `asyncio.run(orchestrator.run_cv_batch(...))`.
- [x] 7.2 Rewrite `scripts/cv_generator/generate_targeted_cv.py` as thin wrapper → `generate_one(score_id)`.
- [x] 7.3 Rewrite `scripts/cv_generator/ingestion/ingest_cv.py` → call `services.cv_generator.ingestor`.
- [x] 7.4 Rewrite `scripts/cv_generator/ingestion/refresh_digest.py` → call `services.cv_generator.digester`.

## Phase 8: Cleanup

- [x] 8.1 Delete `scripts/cv_generator/llm_adapter.py`, `generate_cv.py`, `profile_loader.py`, `ingestion/llm_adapter.py`, `ingestion/llm_ingestor.py`, `ingestion/llm_digester.py` after verifying zero external refs via grep.
- [x] 8.2 Run migration: `alembic upgrade head`. Verify column + UNIQUE present.
- [x] 8.3 Grep `services/cv_generator/` for any import from `services.ranking.*` — MUST be empty (spec R9).

## Phase 9: Smoke test

- [ ] 9.1 PUT profile with valid RUT; verify persistencia + digest null post-hash-change.
- [ ] 9.2 Trigger `POST /internal/cv/batch-run {min_score: 75, limit: 5}`; verify 202 + run_id.
- [ ] 9.3 Poll `GET /internal/runs/{id}` until completed; verify 5 PDFs in `/app/output/cv/` with expected filename format.
- [ ] 9.4 Verify log at startup shows prefix >=1024 tokens.
- [ ] 9.5 Run single endpoint: `POST /internal/cv/generate {score_id}`; verify 200 + file exists.
- [ ] 9.6 Concurrent test: trigger 2x batch-run — second MUST get 409.
