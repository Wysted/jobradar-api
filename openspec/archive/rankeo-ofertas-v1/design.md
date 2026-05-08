# Design: Rankeo de Ofertas v1

## 1. Architecture Overview

```
                    ┌───────────────┐
                    │      n8n      │  (external scheduler / 12:00, 14:00)
                    └───────┬───────┘
                            │ POST /internal/scrape/run      (X-Internal-Api-Key)
                            │ POST /internal/rank/run
                            │ POST /internal/embed/backfill
                            │ GET  /internal/runs/{id}       (polling)
                            ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │                         FastAPI app                              │
   │                                                                  │
   │  routers/internal/*   ──►  create pipeline_run (status=queued)   │
   │       │                    return 202 + {run_id,kind,status}     │
   │       │                    (409 if same kind already running)    │
   │       │                                                          │
   │       ▼                                                          │
   │  BackgroundTasks  ──►  services/ranking/pipeline.py              │
   │                                                                  │
   │    run_ranking(run_id):                                          │
   │      ├─ profile_service.ensure_embedding(profile)                │
   │      ├─ L1 sql_filters.build_candidates(profile) ──► jobs        │
   │      ├─ L2 vector_search.top_k(profile.emb, candidates) ──► K    │
   │      ├─ L3 llm_reranker.score_each(profile, k_jobs)              │
   │      │      └─► upsert job_scores                                │
   │      └─ pipeline_runs.complete(run_id, stats)                    │
   └──────────────────────────────────────────────────────────────────┘
                            │
                            ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │   Postgres (pgvector/pg16)                                       │
   │     jobs (+ embedding vector(1536), embedded_at, embedding_model)│
   │     user_profiles   job_scores   pipeline_runs                   │
   └──────────────────────────────────────────────────────────────────┘
                            ▲
   ┌─────────────────┐      │  GET /api/v1/jobs/ranked        (X-Api-Key)
   │   Personal      │──────┘  POST /api/v1/jobs/{id}/view
   │   client        │         PUT/GET /api/v1/profile
   └─────────────────┘
```

Layers: routers → services → models (SQLAlchemy). Async throughout. Session-per-request via `database.get_db`. `BackgroundTasks` executes pipeline work inside the container; `pipeline_runs` is the source of truth for status.

## 2. Module / File Layout

```
scraping_masive/
├── main.py                                # modified: register new routers
├── config.py                              # modified: OPENAI_*, INTERNAL_API_KEY, API_KEY, TOP_K, MIN_SCORE
├── requirements.txt                       # +openai, +pgvector, +tenacity
├── docker-compose.yml                     # already aligned (verified)
├── .env.example                           # modified: OPENAI_*, INTERNAL_API_KEY, API_KEY
│
├── dependencies/
│   ├── __init__.py
│   ├── auth.py                            # require_internal_key, require_api_key
│   └── openai_client.py                   # shared AsyncOpenAI singleton
│
├── models/
│   ├── profile.py                         # Pydantic ProfileIn/ProfileOut, nested models
│   ├── ranking.py                         # Pydantic RankedJob, RankedJobsResponse, LLMScoreOutput
│   ├── pipeline_run.py                    # Pydantic RunOut, TriggerResponse, ConflictResponse
│   └── database/
│       ├── profile.py                     # UserProfile model
│       ├── job.py                         # modified: +embedding, +embedded_at, +embedding_model
│       ├── job_score.py                   # JobScore model
│       └── pipeline_run.py                # PipelineRun model
│
├── services/
│   ├── embedding.py                       # serialize_job_for_embedding, serialize_profile_for_embedding, embed_texts
│   ├── profile_service.py                 # upsert_profile, compute_hash, ensure_embedding
│   ├── pipeline_runs.py                   # create, transition, complete, fail, get, latest
│   └── ranking/
│       ├── __init__.py
│       ├── pipeline.py                    # run_ranking orchestrator
│       ├── sql_filters.py                 # build_candidates_stmt(profile) -> Select
│       ├── vector_search.py               # top_k(profile_emb, candidate_ids, k)
│       ├── llm_reranker.py                # score_job(profile, job) -> LLMScoreOutput
│       └── prompts/
│           └── rerank_v1.md               # versioned prompt (system + user)
│
├── routers/
│   ├── profile.py                         # PUT/GET /api/v1/profile
│   ├── ranking.py                         # GET /api/v1/jobs/ranked, POST /api/v1/jobs/{id}/view
│   └── internal/
│       ├── __init__.py                    # aggregate APIRouter(prefix="/internal")
│       ├── scrape.py                      # POST /scrape/run
│       ├── rank.py                        # POST /rank/run
│       ├── embed.py                       # POST /embed/backfill
│       └── runs.py                        # GET /runs/{id}, GET /runs/latest
│
└── database/migrations/versions/
    ├── 003_enable_pgvector_extension.py
    ├── 004_add_jobs_embedding_columns.py
    ├── 005_create_user_profiles.py
    ├── 006_create_pipeline_runs.py
    └── 007_create_job_scores.py
```

Rationale: no `scheduler.py` — n8n is the only scheduler. `routers/internal/` is a package (multiple files, shared `prefix="/internal"` + auth dep). Prompts live as Markdown beside the code that loads them for easy diff review and versioning.

## 3. Database Migrations (explicit DDL)

### 003 — enable pgvector

```python
def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

def downgrade():
    # Do NOT drop the extension on downgrade: other tables may depend on it.
    pass
```

### 004 — jobs embedding columns

```python
def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")  # defensive
    op.add_column("jobs", sa.Column("embedding", Vector(1536), nullable=True))
    op.add_column("jobs", sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("embedding_model", sa.String(100), nullable=True))
    op.execute(
        "CREATE INDEX idx_jobs_embedding_hnsw ON jobs "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64);"
    )
    op.create_index("idx_jobs_embedded_at_null", "jobs", ["embedded_at"],
                    postgresql_where=sa.text("embedded_at IS NULL"))

def downgrade():
    op.drop_index("idx_jobs_embedded_at_null", table_name="jobs")
    op.drop_index("idx_jobs_embedding_hnsw", table_name="jobs")
    op.drop_column("jobs", "embedding_model")
    op.drop_column("jobs", "embedded_at")
    op.drop_column("jobs", "embedding")
```

### 005 — user_profiles

```sql
CREATE TABLE user_profiles (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payload             JSONB       NOT NULL,          -- full semantic profile
    embedding_hash      TEXT        NOT NULL,          -- sha256 of canonical payload
    embedding           vector(1536),
    embedded_hash       TEXT,                          -- hash that produced current embedding
    embedding_model     TEXT,
    embedded_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- single-row guard (single-user v1):
CREATE UNIQUE INDEX ux_user_profiles_singleton ON user_profiles ((true));
```

`embedded_hash` is the hash that corresponds to `embedding`. Stale detection: `embedding IS NULL OR embedded_hash <> embedding_hash`.

### 006 — pipeline_runs

```sql
CREATE TABLE pipeline_runs (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    kind         TEXT        NOT NULL CHECK (kind IN ('scrape','rank','backfill')),
    status       TEXT        NOT NULL CHECK (status IN ('queued','running','completed','failed')),
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    stats        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    error        TEXT
);
CREATE INDEX idx_pipeline_runs_kind_started ON pipeline_runs (kind, started_at DESC);
-- Concurrency guard: at most one active run (queued OR running) per kind.
CREATE UNIQUE INDEX ux_pipeline_runs_active_per_kind
    ON pipeline_runs (kind)
    WHERE status IN ('queued','running');
```

### 007 — job_scores

```sql
CREATE TABLE job_scores (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID        NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    profile_id      UUID        NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    vector_score    REAL        NOT NULL,
    llm_score       INTEGER     NOT NULL CHECK (llm_score BETWEEN 0 AND 100),
    matched_areas   JSONB       NOT NULL DEFAULT '[]'::jsonb,
    matched_techs   JSONB       NOT NULL DEFAULT '[]'::jsonb,
    missing_skills  JSONB       NOT NULL DEFAULT '[]'::jsonb,
    red_flags       JSONB       NOT NULL DEFAULT '[]'::jsonb,
    pitch_angles    JSONB       NOT NULL DEFAULT '[]'::jsonb,
    reasoning       TEXT        NOT NULL DEFAULT '',
    llm_model       TEXT        NOT NULL,
    scored_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    viewed_at       TIMESTAMPTZ,                -- only_new mechanism (Q3)
    CONSTRAINT ux_job_scores_job UNIQUE (job_id)   -- one score per job (v1 single profile)
);
CREATE INDEX idx_job_scores_score_desc ON job_scores (llm_score DESC, scored_at DESC);
CREATE INDEX idx_job_scores_viewed_null ON job_scores (job_id)
    WHERE viewed_at IS NULL;
```

## 4. Profile Serialization for Embedding

`serialize_profile_for_embedding(payload: dict) -> str` — deterministic, human-readable, optimized for semantic similarity (not the same representation used for the hash).

**Template (sections omitted when list/obj empty):**

```
Headline: {headline}
Total experience: {total_years} years.
Areas: {Backend (seniority 4), DevOps (seniority 3), ...}       # sorted by name
Technologies: {Python/Backend (4), Go/Backend (3), ...}          # sorted by (area_name, name)
Soft skills: {communication, ownership, ...}                     # sorted
Target roles: {Senior Backend Engineer, SRE, ...}                # sorted
Excluded roles: {QA Manual, Helpdesk}
Preferred industries: {fintech, health}
Excluded industries: {gambling, defense}
Location: {Buenos Aires, AR; remote_ok=true}
Work mode: {remote, hybrid}                                      # sorted
Salary floor: {USD 4000}
Languages: {Spanish (native), English (C1)}                      # sorted by name
Contract types: {full-time, contractor}                          # sorted
Company size preference: {50-200, 200-1000}                      # sorted
Deal breakers: {onsite-only, no-english-required}                # sorted
```

**Example input → output:**

Input: `{"headline":"Senior Backend","total_years":8,"areas":[{"name":"Backend","seniority":4}],...}`
Output: `"Headline: Senior Backend\nTotal experience: 8 years.\nAreas: Backend (seniority 4)\n..."`

Sorting every list alphabetically (by `name` for objects) is critical: the OpenAI embedding endpoint is stable for byte-identical input, so deterministic order = reproducible embeddings = no spurious re-embeds.

## 5. Profile Hash Algorithm

```python
EMBEDDING_AFFECTING_FIELDS = [
    "headline", "total_years",
    "areas", "technologies", "soft_skills",
    "roles_target", "roles_excluded",
    "industries_preferred", "industries_excluded",
    "location", "work_mode", "salary",
    "languages", "contract_types", "company_size_pref",
    "deal_breakers",
]

def compute_profile_hash(payload: dict) -> str:
    subset = {k: payload.get(k) for k in EMBEDDING_AFFECTING_FIELDS}
    canonical = json.dumps(subset, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

**Why every semantic field, not just areas/techs** (addresses proposal Risk — "Cambio de perfil sin re-embedding"): if `work_mode`, `deal_breakers`, or `location` change, semantic similarity vs. jobs shifts. Hashing only areas/techs would skip legitimate re-embeds. `json.dumps(..., sort_keys=True, separators=(",", ":"))` produces one canonical form: no whitespace drift, deterministic key order at any nesting depth.

Fields excluded from hash (do not affect embedding): `id`, `created_at`, `updated_at`, `embedding_hash`, `embedded_hash`.

## 6. Offer Serialization for Embedding

```python
def serialize_job_for_embedding(job: JobRecord) -> str:
    return (
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Location: {job.location}\n"
        f"Tags: {', '.join(sorted(job.tags or []))}\n"
        f"Description:\n{(job.description or '').strip()[:8000]}"
    )
```

Fields in fixed order: title, company, location, tags (sorted), description truncated at 8000 chars (well within `text-embedding-3-small` 8191-token budget after UTF-8 expansion). Platform and posted_date intentionally excluded — they don't carry semantic matching signal, only operational/filter signal.

## 7. Ranking Pipeline Orchestration (pseudocode)

```python
async def run_ranking(run_id: UUID) -> None:
    async with async_session() as db:
        await pipeline_runs.transition(db, run_id, "running")
        t0 = monotonic()
        try:
            profile = await profile_service.get_active(db)
            if profile is None:
                raise RuntimeError("no active profile; run PUT /api/v1/profile first")

            # Re-embed if stale (hash drift or never embedded).
            profile = await profile_service.ensure_embedding(db, profile)

            # L1 — SQL filters.
            candidate_ids = await sql_filters.build_candidate_ids(db, profile)
            stats = {"candidates_after_filter": len(candidate_ids), "topk_size": 0,
                     "llm_calls": 0, "tokens_in": 0, "tokens_out": 0,
                     "new_scores": 0, "updated_scores": 0}
            await pipeline_runs.patch_stats(db, run_id, stats)
            if not candidate_ids:
                await pipeline_runs.complete(db, run_id, stats, duration_s=monotonic()-t0)
                return

            # L2 — pgvector top-K.
            top_k = await vector_search.top_k(
                db, profile.embedding, candidate_ids, k=config.RANK_TOP_K,
            )
            stats["topk_size"] = len(top_k)
            if not top_k:
                await pipeline_runs.complete(db, run_id, stats, duration_s=monotonic()-t0)
                return

            # L3 — LLM rerank, one call per job, incremental upsert + stats update.
            for row in top_k:  # row = (job, vector_score)
                try:
                    result = await llm_reranker.score_job(profile, row.job)  # retries inside
                except Exception as exc:
                    logger.exception("llm rerank failed job=%s: %s", row.job.id, exc)
                    continue
                upsert = await job_scores_repo.upsert(
                    db, job_id=row.job.id, profile_id=profile.id,
                    vector_score=row.vector_score, llm_output=result,
                    llm_model=config.OPENAI_RERANK_MODEL,
                )
                stats["llm_calls"] += 1
                stats["tokens_in"] += result.usage.prompt_tokens
                stats["tokens_out"] += result.usage.completion_tokens
                stats["new_scores" if upsert.inserted else "updated_scores"] += 1
                if stats["llm_calls"] % 10 == 0:
                    await pipeline_runs.patch_stats(db, run_id, stats)

            stats["duration_s"] = round(monotonic() - t0, 2)
            await pipeline_runs.complete(db, run_id, stats)
        except Exception as exc:
            logger.exception("ranking pipeline failed run=%s", run_id)
            await pipeline_runs.fail(db, run_id, error=str(exc))
```

Notes: each L3 LLM call commits its own upsert (partial results survive process death). Stats flushed every 10 calls to keep `/internal/runs/{id}` useful for n8n polling.

## 8. LLM Prompt v1 (`services/ranking/prompts/rerank_v1.md`)

```markdown
## System

You are a ranking engine that evaluates how well a job offer matches a candidate's professional profile.
You MUST output ONLY a JSON object matching the provided schema — no prose, no markdown, no explanation outside the `reasoning` field.
Scores must be calibrated: 90+ only for outstanding fit, 70-89 strong fit, 50-69 borderline, <50 weak.
Use the candidate's language for `reasoning` (Spanish if the profile is Spanish).

## User

Profile:
```json
{profile_json}
```

Offer:
```json
{offer_json}
```

Evaluate the match. Rules:
- `matched_areas`/`matched_techs`: only items present in BOTH profile and offer; include seniorities.
- `missing_skills`: skills required by the offer that the profile lacks.
- `red_flags`: any explicit blocker (location mismatch, contract, deal_breaker match, salary).
- `pitch_angles`: 1-3 concrete arguments the candidate could use to apply.
- `score_0_100`: holistic integer, consistent with the above.
- `reasoning`: 2-4 sentences justifying the score.
```

`{profile_json}`: compact JSON of the profile's embedding-affecting fields (same subset used for the hash, sorted keys). `{offer_json}`: `{"title","company","location","tags","description"}`. Prompt filename embeds version — a new prompt ships as `rerank_v2.md`; `llm_reranker` pins the version.

## 9. LLM JSON Schema (strict)

```python
RERANK_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "RerankResult",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["score_0_100", "matched_areas", "matched_techs",
                         "missing_skills", "red_flags", "pitch_angles", "reasoning"],
            "properties": {
                "score_0_100": {"type": "integer", "minimum": 0, "maximum": 100},
                "matched_areas": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "profile_sen", "job_sen"],
                        "properties": {
                            "name": {"type": "string"},
                            "profile_sen": {"type": "integer", "minimum": 1, "maximum": 5},
                            "job_sen": {"type": "integer", "minimum": 1, "maximum": 5},
                        },
                    },
                },
                "matched_techs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "profile_sen", "job_sen"],
                        "properties": {
                            "name": {"type": "string"},
                            "profile_sen": {"type": "integer", "minimum": 1, "maximum": 5},
                            "job_sen": {"type": "integer", "minimum": 1, "maximum": 5},
                        },
                    },
                },
                "missing_skills": {"type": "array", "items": {"type": "string"}},
                "red_flags":     {"type": "array", "items": {"type": "string"}},
                "pitch_angles":  {"type": "array", "items": {"type": "string"}},
                "reasoning":     {"type": "string"},
            },
        },
    },
}
```

`strict: true` + `additionalProperties: false` on every object = OpenAI guarantees conformance or raises; we retry once, otherwise skip the offer (spec R3).

## 10. Layer 1 — SQL Filter Mapping

Assumptions: `jobs.description` holds free text; `jobs.location` is a loosely-structured string; `jobs.tags` is `text[]`. No normalized columns for salary / contract / work_mode exist yet — the filter engine applies what it can via text predicates; fields not representable in SQL become soft filters applied in L3 via the prompt.

| Profile field | SQL clause (parameterized, appended with AND) |
|---|---|
| `deal_breakers[]` | `AND NOT EXISTS (SELECT 1 FROM unnest(:deal_breakers::text[]) db WHERE lower(j.description) LIKE '%' \|\| lower(db) \|\| '%')` |
| `roles_excluded[]` | `AND NOT (lower(j.title) LIKE ANY (:roles_excluded_patterns))` — patterns built as `'%<role>%'` |
| `industries_excluded[]` | `AND NOT EXISTS (SELECT 1 FROM unnest(:industries_excluded::text[]) ix WHERE lower(j.company) LIKE '%' \|\| lower(ix) \|\| '%' OR lower(j.description) LIKE '%' \|\| lower(ix) \|\| '%')` |
| `location.remote_ok = false` AND `work_mode` excludes `remote` | `AND NOT (lower(j.description) LIKE '%remote%' AND lower(j.location) NOT LIKE '%' \|\| lower(:city) \|\| '%')` |
| `work_mode = ["remote"]` (remote-only) | `AND (lower(j.description) LIKE '%remote%' OR lower(j.title) LIKE '%remote%' OR lower(j.location) LIKE '%remote%')` |
| `location.city` present AND `remote_ok = false` | `AND (lower(j.location) LIKE '%' \|\| lower(:city) \|\| '%' OR lower(j.description) LIKE '%remote%')` |
| `languages[]` includes "English" required by profile, but excludes it | *(soft — handled in L3 via prompt)* |
| `salary.min` | *(soft — few listings publish salary; handled in L3 `red_flags`)* |
| `contract_types[]` (exclude set) | `AND NOT (lower(j.description) LIKE ANY (:contract_exclude_patterns))` — only when profile explicitly excludes |
| Always | `AND j.embedding IS NOT NULL AND j.embedded_at IS NOT NULL` |

Base SQL:

```sql
SELECT j.id
FROM jobs j
JOIN platforms p ON p.id = j.platform_id
WHERE j.embedding IS NOT NULL
  AND j.embedded_at IS NOT NULL
  -- <appended clauses>
ORDER BY j.scraped_at DESC
LIMIT 2000;  -- hard cap before L2
```

`sql_filters.build_candidate_ids(db, profile)` constructs the statement programmatically from the profile — only fields set on the profile contribute clauses. Patterns always use `lower()` both sides; no index on description but volume (<5k rows) keeps this fine.

## 11. Concurrency Model for pipeline_runs

**Decision: unique partial index** `ux_pipeline_runs_active_per_kind ON pipeline_runs(kind) WHERE status IN ('queued','running')` (see migration 006).

Flow inside `pipeline_runs.create(db, kind)`:

```python
try:
    row = PipelineRun(kind=kind, status="queued")
    db.add(row)
    await db.flush()      # triggers unique constraint
    await db.commit()
    background_tasks.add_task(run_pipeline, row.id)
    return row
except IntegrityError:
    await db.rollback()
    active = await db.execute(
        select(PipelineRun).where(
            PipelineRun.kind == kind,
            PipelineRun.status.in_(["queued", "running"]),
        )
    )
    existing = active.scalar_one()
    raise ConflictError(run_id=existing.id, kind=kind, status=existing.status)
```

Alternatives considered:
- `SELECT ... FOR UPDATE` on a gate row → needs a dedicated table, extra round-trip, breaks under failed commits.
- Postgres advisory lock (`pg_try_advisory_xact_lock`) → invisible in `psql` inspection; harder to debug when "stuck running" orphans occur.
- Unique partial index → atomic at the DB level, self-documenting in `\d pipeline_runs`, survives app restart. Orphans (status left at `running` after process crash) need a manual recovery query; acceptable for single-user v1 (document in ops notes).

## 12. Cost Estimation

Prices (OpenAI, 2026):
- `text-embedding-3-small`: $0.02 / 1M tokens.
- `gpt-4o-mini`: $0.15 / 1M input, $0.60 / 1M output tokens.

Assumptions: 200 offers/day scraped; job serialization ≈ 1.5k tokens each; profile emb once per day (negligible, one call of ~500 tokens). L3 runs over `top_k=50` offers/day. Per-rerank: 2k in + 500 out.

| Item | Tokens/day | $/day | $/month (30d) |
|---|---|---|---|
| Offer embeddings | 200 × 1500 = 300k | $0.006 | $0.18 |
| Profile embedding | ~500 | ~$0 | ~$0 |
| LLM rerank (input) | 50 × 2000 = 100k | $0.015 | $0.45 |
| LLM rerank (output) | 50 × 500 = 25k | $0.015 | $0.45 |
| **Total** | | **≈ $0.036** | **≈ $1.08** |

Backfill one-time (e.g., 2000 historical offers): ~3M tokens embeddings + 50 × 2500 × 2 = $0.06 emb + $0.15 rerank ≈ $0.20 one-off. Well within the <$5/month target. Budget alert threshold: $3/month → log warn in pipeline_runs stats; manual intervention.

## 13. Open Questions — Resolution

| # | Question | Decision | Justification |
|---|----------|----------|---------------|
| Q1 | pgvector index: `ivfflat` vs `hnsw` | **hnsw (m=16, ef_construction=64)**; set `hnsw.ef_search = 40` at session level before each L2 query | Better recall at this volume, no rebuild needed after inserts (unlike `ivfflat` which degrades without periodic `REINDEX`), build cost irrelevant at 200 rows/day. |
| Q2 | Offer embedding storage | **Column on `jobs`** (`embedding`, `embedded_at`, `embedding_model`) | Single-user, single-model project; YAGNI on multi-model. Join avoided. If the model changes: one Alembic migration with `UPDATE jobs SET embedding = NULL` + rerun backfill. |
| Q3 | `only_new` mechanism | **`job_scores.viewed_at TIMESTAMPTZ NULL`** + `POST /api/v1/jobs/{job_id}/view` sets `viewed_at = now()`; `?only_new=true` filters `viewed_at IS NULL`. Partial index on `viewed_at IS NULL`. | Server-side truth; works cross-device; explicit API verb; trivial index; no coupling to client-local state. |
| Q4 | OpenAI retry strategy | **tenacity**: `retry=retry_if_exception_type((RateLimitError, APIConnectionError, APITimeoutError, InternalServerError))`, `stop=stop_after_attempt(5)`, `wait=wait_exponential(multiplier=2, min=2, max=60) + wait_random(0, 2)`. Embedding chunks: whole-chunk retry; after 5 failed attempts, split chunk in half recursively; at size 1, log + mark job stays `embedded_at=null`. LLM rerank per job: same retry policy; on final failure skip the job (spec R3 allows one schema retry on top of the transport retries). | Exponential + jitter is the canonical pattern for OpenAI 429s. Recursive chunk split isolates the poison pill without abandoning the healthy majority; bounded total wait (<5 min per chunk worst case). |

## 14. Risks — Updated

| Risk | Likelihood | Mitigation |
|---|---|---|
| LLM prompt drift → score inconsistency | Med | Prompt versioned in `prompts/rerank_v1.md`; `llm_model` persisted on every score; changing prompt ships as v2. |
| HNSW index build stalls Alembic | Low | 200 rows/day → index build <1s at any realistic cutover volume. |
| Profile hash field mismatch | Med (addressed) | `EMBEDDING_AFFECTING_FIELDS` is a single named constant imported by both `profile_service.compute_hash` and the hash test. |
| Partial L3 failure leaves mixed-model scores in `job_scores` | Low | `llm_model` column captures which model produced each row; re-rank is cheap ($0.45/mo) — a full re-run is acceptable when changing the rerank model. |
| Orphan `running` pipeline_runs on container crash | Med | Ops recovery SQL: `UPDATE pipeline_runs SET status='failed', error='orphan recovery', finished_at=now() WHERE status='running' AND started_at < now() - interval '2 hours';` — document in README. |
| L1 text-filter false negatives (e.g., Spanish vs English `"remote"`) | Med | Patterns include `remoto`, `remote`, `teletrabajo`; `red_flags` in L3 catches cases L1 couldn't filter. |
| Unique partial index blocks legitimate re-queue after crash | Low | Orphan recovery SQL above clears `running`; if `queued` orphans appear, same query with `status='queued'`. |
| OpenAI API key leaked via logs | Med | `dependencies/openai_client.py` is the single AsyncOpenAI construction site; no key in request/response logs. |
