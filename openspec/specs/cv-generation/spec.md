# cv-generation Specification

## Purpose

Pipeline de generación de CVs adaptados por oferta laboral rankeada. Orquesta adapter LLM + renderer PDF + persistencia + tracking PipelineRun. Ejecuta en modo batch (múltiples ofertas rankeadas) o single (una oferta por `score_id`).

## Requirements

### Requirement: R1 — Batch trigger endpoint

El sistema MUST exponer `POST /internal/cv/batch-run` autenticado con `X-Internal-Key`. Acepta `{min_score: int, limit: int, force: bool}`. MUST crear un `PipelineRun` con `kind="cv_batch"` y devolver `202 Accepted` con `{run_id, kind, status}`. MUST rechazar con `409 Conflict` + `{active_run_id, kind}` si ya existe otro run `queued|running` del mismo kind.

#### Scenario: Batch trigger exitoso
- GIVEN no hay runs activos de kind `cv_batch`
- WHEN se envía `POST /internal/cv/batch-run {min_score: 60, limit: 100, force: false}`
- THEN el sistema responde `202` con `run_id` válido
- AND el PipelineRun queda en status `queued` con trigger `n8n`

#### Scenario: Batch conflict
- GIVEN existe un PipelineRun `cv_batch` en status `running`
- WHEN se envía otro `POST /internal/cv/batch-run`
- THEN el sistema responde `409` con `active_run_id` del existente

### Requirement: R2 — Single generation endpoint

El sistema MUST exponer `POST /internal/cv/generate {score_id: uuid}` que procesa un único `job_scores.id`. MUST responder `200 OK` con `{cv_path, model, duration_s}` o `404` si `score_id` no existe, `400` si el perfil carece de RUT.

#### Scenario: Generación single exitosa
- GIVEN existe `job_scores.id = X` con perfil que tiene RUT
- WHEN se envía `POST /internal/cv/generate {score_id: X}`
- THEN el sistema genera el PDF, persiste `cv_generated_at`/`cv_path`/`cv_model`
- AND responde `200` con la ruta del PDF

#### Scenario: Perfil sin RUT
- GIVEN el perfil activo tiene `rut = null`
- WHEN se envía `POST /internal/cv/generate`
- THEN el sistema responde `400` con mensaje `"profile.rut required for CV filename"`

### Requirement: R3 — Batch orchestration

El sistema MUST seleccionar `job_scores` donde `llm_score >= min_score` AND (`cv_generated_at IS NULL` OR `force = true`), limitado a `limit` ordenados por `llm_score DESC`. MUST procesar con concurrencia configurable (`CV_BATCH_CONCURRENCY`, default 5) usando `asyncio.gather` + semáforo. Cada worker MUST abrir su propia sesión DB. En fallo de un worker, MUST incrementar `failed` y continuar con los demás.

#### Scenario: Batch con fallos parciales
- GIVEN hay 10 pendientes, 2 fallan en LLM tras retries
- WHEN el batch corre
- THEN 8 quedan con `cv_generated_at` poblado
- AND PipelineRun stats reporta `processed=10, succeeded=8, failed=2`
- AND el run transita a `completed` (no `failed`)

### Requirement: R4 — Per-CV pipeline

El sistema MUST ejecutar por cada CV: (1) cargar perfil; (2) si `payload.digest is null`, regenerarlo síncronamente vía digester LLM; (3) llamar adapter LLM con digest + job + score; (4) renderizar PDF con two-pass page fit (max 2 páginas); (5) `UPDATE job_scores SET cv_generated_at=now(), cv_path, cv_model`.

#### Scenario: Digest lazy regen
- GIVEN perfil tiene `payload.digest = null`
- WHEN se procesa un CV
- THEN el sistema llama al digester, persiste el digest y continúa con el adapter

### Requirement: R5 — Filename determinista

El nombre del PDF MUST ser `{nombre_slug}_{rut}_{company_slug}_{cargo_oferta_slug}.pdf`. Los slugs MUST remover acentos y reemplazar no-alfanuméricos por `_`. Si el perfil carece de `rut` o `nombre`, el sistema MUST fail-fast con error.

#### Scenario: Filename generado
- GIVEN perfil `{nombre: "Juan Pérez", rut: "12345678-9"}`, job `{company: "SKY Airline", title: "Dev Fullstack"}`
- WHEN se genera el CV
- THEN el filename es `Juan_Perez_12345678-9_SKY_Airline_Dev_Fullstack.pdf`

### Requirement: R6 — Stats schema

El PipelineRun `cv_batch` MUST poblar `stats` con: `min_score`, `total_pending`, `processed`, `succeeded`, `failed`, `skipped_duplicate`, `llm_tokens_in`, `llm_tokens_out`, `duration_s`. Stats MUST actualizarse incrementalmente durante el run (flush cada N items).

### Requirement: R7 — Retry selectivo

Las llamadas LLM (adapter, digester, ingestor) MUST reintentar sólo `(APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)` hasta 5 attempts con `wait_exponential(min=2, max=30) + wait_random(0,1)`. Errores 4xx (BadRequest, Unauthorized) MUST NOT reintentar.

### Requirement: R8 — Prompt cache prefix

El prefix LLM (system prompt + digest block) MUST superar 1024 tokens para habilitar OpenAI prompt caching. Al startup el sistema MUST medir el prefix y loggear warning si es menor. El prefix MUST ser byte-estable entre calls (ninguna interpolación runtime).

#### Scenario: Prefix suficiente
- GIVEN system+digest suma 1450 tokens
- WHEN el sistema inicia
- THEN log `INFO: cv adapter prefix 1450 tokens (caching enabled)`

### Requirement: R9 — Aislamiento del ranker

El código de `services/cv_generator/` MUST NOT importar nada de `services/ranking/`. Verificable vía grep estático. Violaciones MUST bloquear merge.

#### Scenario: Import cruzado bloqueado
- GIVEN alguien agrega `from services.ranking.llm_reranker import ...` en `services/cv_generator/adapter.py`
- WHEN se corre el check de imports
- THEN el check falla con mensaje explícito
