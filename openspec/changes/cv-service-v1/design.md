# Design: CV Service v1

## Technical Approach

Migrar scripts/cv_generator al patrón service-first usado por ranking (services/ranking/pipeline.py + routers/internal.py). Batch ejecuta vía FastAPI `BackgroundTasks` con concurrencia intra-batch por `asyncio.gather` + `Semaphore`. AsyncOpenAI singleton ya existente. Retry tenacity unificado en `services/retry.py`. Digest invalidation sincronizada al upsert de perfil, regeneración LAZY en primer consumidor.

## Architecture Decisions

| Decisión | Elegido | Alternativa | Razón |
|----------|---------|-------------|-------|
| Async execution | FastAPI BackgroundTasks | Celery/arq/asyncio.create_task | Consistencia con rank/scrape/backfill; zero infra nueva |
| Concurrencia intra-batch | asyncio.gather + Semaphore(5) | Thread pool | Client OpenAI ya async; cada worker abre su session |
| Digest regen | Lazy sync | Eager async | ~1s aceptable; un código path menos |
| Retry ubicación | services/retry.py compartido | Duplicar en cada módulo | DRY; embedding+reranker ya tienen policy idéntica |
| Token count | tiktoken | Manual approx | Exacto; agregar dep (ligero ~2MB) |
| RUT validation | Módulo 11 local en validators.py | libs 3rd party | Algoritmo trivial, evitar dep |
| Router mount | Nuevo routers/internal_cv.py | Extender routers/internal.py | Aislamiento; archivo grande evita |

## Data Flow — batch endpoint

```
 n8n/client
    │ POST /internal/cv/batch-run {min_score, limit, force}
    ▼
 routers/internal_cv.py
    │ create_run(kind=cv_batch) → 202 {run_id}
    │ BackgroundTasks.add_task(run_cv_batch, run_id, body)
    ▼
 services/cv_generator/orchestrator.run_cv_batch
    │ async_session → transition(RUNNING)
    │ SELECT pending score_ids (L1)
    ▼
 asyncio.gather(worker(sid) for sid in pending)
 Semaphore(CV_BATCH_CONCURRENCY=5)
    │
    ├─▶ worker(score_id):  ← each with its own async_session
    │     load_profile + digest (lazy regen via digester)
    │     adapter LLM (AsyncOpenAI + retry)
    │     renderer.generate_pdf (two-pass)
    │     UPDATE job_scores SET cv_generated_at, cv_path, cv_model
    │     flush stats increment
    │
    ▼
 transition(COMPLETED, stats)   OR   rollback + transition(FAILED, error)
```

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `services/cv_generator/__init__.py` | Create | Package marker |
| `services/cv_generator/adapter.py` | Create | `adapt_profile_to_job(client, digest, job, score) -> dict` (AsyncOpenAI) |
| `services/cv_generator/digester.py` | Create | `refresh_digest(client, payload) -> dict` |
| `services/cv_generator/ingestor.py` | Create | `ingest_cv_text(client, text, known_links) -> dict` |
| `services/cv_generator/renderer.py` | Create | Move `generate_pdf(header, content, path)` desde scripts/cv_generator/generate_cv.py (sync, ReportLab) |
| `services/cv_generator/filename.py` | Create | `build_filename(profile, job) -> str`; `slugify(text)`; fail-fast si rut/nombre null |
| `services/cv_generator/validators.py` | Create | `validate_rut(rut) -> str` (normaliza + check digit mod-11); `normalize_rut(raw) -> str` |
| `services/cv_generator/orchestrator.py` | Create | `run_cv_batch(run_id, min_score, limit, force)`; `generate_one(score_id)` |
| `services/cv_generator/token_check.py` | Create | `measure_prefix_tokens(model, system, sample_digest) -> int`; logea warning si <1024 |
| `services/retry.py` | Create | `openai_retry_policy` dict; reexport tenacity decorator |
| `services/embedding.py` | Modify | Usar `services.retry.openai_retry_policy` |
| `services/ranking/llm_reranker.py` | Modify | Usar `services.retry.openai_retry_policy` |
| `routers/internal_cv.py` | Create | POST /internal/cv/batch-run, POST /internal/cv/generate |
| `main.py` | Modify | `app.include_router(internal_cv_router)` |
| `models/database/pipeline_run.py` | Modify | `KIND_CV_BATCH = "cv_batch"` + `VALID_KINDS` |
| `models/database/profile.py` | Modify | `rut: Mapped[str \| None]` column |
| `models/profile.py` | Modify | `rut: str \| None` en ProfileIn/Out con validator RUT |
| `database/migrations/versions/010_add_rut_to_user_profiles.py` | Create | ALTER TABLE ADD rut + UNIQUE index |
| `services/profile_service.py` | Modify | En `upsert_profile`: si `embedding_hash` cambia → `existing.payload["digest"] = None` |
| `config.py` | Modify | `CV_BATCH_CONCURRENCY=5`, `CV_OUTPUT_DIR="/app/output/cv/"` |
| `docker-compose.yml` | Modify | propagar CV_BATCH_CONCURRENCY, CV_OUTPUT_DIR |
| `scripts/cv_generator/batch_generate.py` | Modify | Thin wrapper: argparse → `asyncio.run(orchestrator.run_cv_batch(...))` |
| `scripts/cv_generator/generate_targeted_cv.py` | Modify | Thin wrapper: → `generate_one(score_id)` |
| `scripts/cv_generator/ingestion/{ingest_cv,refresh_digest}.py` | Modify | Thin wrappers → `services.cv_generator.{ingestor,digester}` |
| `scripts/cv_generator/{llm_adapter,generate_cv,profile_loader}.py` + `ingestion/llm_*.py` | Delete | Logic moved; kept only if imported elsewhere (check grep) |
| `requirements.txt` | Modify | Agregar `tiktoken` |

## Interfaces / Contracts

```python
# services/retry.py
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential, wait_random
from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

_RETRY = (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)

openai_retry_policy = dict(
    retry=retry_if_exception_type(_RETRY),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=30) + wait_random(0, 1),
    reraise=True,
)

# services/cv_generator/orchestrator.py
async def run_cv_batch(run_id: UUID, min_score: int, limit: int, force: bool) -> dict: ...
async def generate_one(score_id: UUID, *, session: AsyncSession | None = None) -> Path: ...

# services/cv_generator/validators.py
def normalize_rut(raw: str) -> str: ...  # "12.345.678-9" → "12345678-9"
def validate_rut(rut: str) -> str: ...   # raises ValueError si check digit falla

# services/cv_generator/filename.py
def build_filename(profile: UserProfile, job: JobRecord) -> str: ...

# routers/internal_cv.py
@router.post("/cv/batch-run", status_code=202, response_model=TriggerResponse)
async def trigger_cv_batch(body: CVBatchRequest, background_tasks: BackgroundTasks, db): ...

@router.post("/cv/generate", status_code=200, response_model=CVGenerateResponse)
async def generate_cv_single(body: CVGenerateRequest, db): ...
```

## Migration 010 DDL

```python
def upgrade():
    op.add_column("user_profiles", sa.Column("rut", sa.Text(), nullable=True))
    op.create_unique_constraint("uq_user_profiles_rut", "user_profiles", ["rut"])
    op.execute("COMMENT ON COLUMN user_profiles.rut IS 'Chilean RUT normalized: dddddddd-d';")

def downgrade():
    op.drop_constraint("uq_user_profiles_rut", "user_profiles", type_="unique")
    op.drop_column("user_profiles", "rut")
```

## RUT validation (módulo 11)

```python
def validate_rut(rut: str) -> str:
    body, dv = rut.split("-")
    digits = list(map(int, body[::-1]))
    factors = [2, 3, 4, 5, 6, 7] * 3
    total = sum(d * f for d, f in zip(digits, factors))
    expected = 11 - (total % 11)
    expected_dv = {11: "0", 10: "K"}.get(expected, str(expected))
    if expected_dv != dv.upper():
        raise ValueError(f"invalid RUT check digit: {rut}")
    return f"{body}-{expected_dv}"
```

## Filename slugify

```python
import unicodedata, re
def slugify(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9\-]+", "_", ascii_text).strip("_")[:80]
# RUT-safe: keep dash. Used for nombre/company/cargo, NOT for rut (rut already normalized).
```

## Token count validation

Lifespan startup hook (FastAPI `lifespan`) ejecuta `measure_prefix_tokens()` con digest sample. tiktoken `encoding_for_model("gpt-4o-mini")`. Log `WARNING` si <1024 + flag en config `_CV_PROMPT_CACHE_ENABLED`.

## Scripts rewrite contract

Cada script = thin wrapper: parse argparse → llamar UNA función de `services.cv_generator.*` → imprimir resultado. Zero business logic, zero DB access directo, zero OpenAI calls directas. Zero duplicación de `_to_asyncpg_dsn`, `_parse_jsonb`, `_safe_filename`.

## Testing Strategy

| Layer | Qué testear | Approach |
|-------|-------------|----------|
| Unit | validate_rut, normalize_rut, slugify, build_filename | pytest (agregar pytest a requirements si se implementa) |
| Unit | retry policy se aplica al adapter | mock cliente → raise APITimeoutError → verificar 5 attempts |
| Integration | POST /internal/cv/batch-run con 2 pending → 202 + PipelineRun completed | TestClient + fixture DB |
| Integration | Digest invalidation: PUT profile con cambio → `digest = None` persistido | TestClient |
| Manual smoke | Full batch 40 CVs + verify PDFs + prompt caching | Script manual con `--dry-run` y `--verify-cache` flags |

TDD desactivado → tests opcionales. Dejar hooks pytest en scripts/ para cuando se active.

## Migration / Rollout

Migration 010 aplicable en prod sin downtime (ADD COLUMN NULL es instantáneo en PG). UNIQUE constraint sobre columna poblada con NULL es libre. No se requiere backfill.

Rollout: (1) merge PR con migration + service + routes; (2) run `alembic upgrade head`; (3) n8n apunta al nuevo endpoint; (4) scripts CLI siguen funcionando durante transición. Rollback: `alembic downgrade -1` + revert code.

## Open Questions

- [ ] `scripts/cv_generator/generate_cv.py` tiene lógica `main()` con `sample_data` para demo — ¿preservar para dev o borrar? Default: preservar comentado.
- [ ] Tests: ¿activar pytest ahora como parte de este change o en change separado? Default: dejar solo smoke manual. Unit tests opcionales.
- [ ] `CV_OUTPUT_DIR=/app/output/cv/` ¿volume mount en docker-compose? Sí — agregar `./output:/app/output` al volumes del service api.
