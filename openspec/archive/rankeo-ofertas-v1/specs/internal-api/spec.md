# internal-api Specification

## Purpose

Expone endpoints bajo `/internal/*` para orquestación por n8n. Todos los endpoints requieren el header `X-Internal-Api-Key` y retornan 202 con `run_id` para permitir polling asíncrono.

## Requirements

### Requirement: R1 — Autenticación con X-Internal-Api-Key

El sistema MUST proteger todos los endpoints `/internal/*` con la dependency `require_internal_key` que valida el header `X-Internal-Api-Key`. Requests sin el header o con key inválida MUST retornar `status.HTTP_401_UNAUTHORIZED`.

#### Scenario: Request sin header retorna 401

- GIVEN cualquier endpoint `/internal/*`
- WHEN se llama sin el header `X-Internal-Api-Key`
- THEN el sistema retorna `status.HTTP_401_UNAUTHORIZED`

#### Scenario: Request con key inválida retorna 401

- GIVEN el header `X-Internal-Api-Key: wrong-key`
- WHEN se llama cualquier endpoint `/internal/*`
- THEN el sistema retorna `status.HTTP_401_UNAUTHORIZED`

#### Scenario: Request con key válida pasa la autenticación

- GIVEN el header `X-Internal-Api-Key: <valid-key>`
- WHEN se llama cualquier endpoint `/internal/*`
- THEN la autenticación pasa y se procesa la lógica del endpoint

### Requirement: R2 — POST /internal/scrape/run

El sistema MUST aceptar `POST /internal/scrape/run` y retornar `status.HTTP_202_ACCEPTED` con el `run_id` del pipeline run creado. Si ya existe un scrape corriendo, MUST retornar `status.HTTP_409_CONFLICT` (delegado a pipeline-runs R3).

#### Scenario: Trigger de scrape exitoso

- GIVEN no hay scrape corriendo
- WHEN se llama `POST /internal/scrape/run` con key válida
- THEN retorna `status.HTTP_202_ACCEPTED` con `{run_id, kind: "scrape", status: "queued"}`
- AND el scrape comienza en background

#### Scenario: Scrape ya corriendo devuelve 409

- GIVEN hay un scrape con `status = "running"`
- WHEN se llama `POST /internal/scrape/run`
- THEN retorna `status.HTTP_409_CONFLICT` con `{run_id: "<activo>", kind: "scrape", status: "running"}`

### Requirement: R3 — POST /internal/rank/run

El sistema MUST aceptar `POST /internal/rank/run` y retornar `status.HTTP_202_ACCEPTED`. Si ya existe un rank corriendo, MUST retornar `status.HTTP_409_CONFLICT`.

#### Scenario: Trigger de ranking exitoso

- GIVEN no hay rank corriendo
- WHEN se llama `POST /internal/rank/run` con key válida
- THEN retorna `status.HTTP_202_ACCEPTED` con `{run_id, kind: "rank", status: "queued"}`

### Requirement: R4 — POST /internal/embed/backfill

El sistema MUST aceptar `POST /internal/embed/backfill` con query param opcional `chunk_size` (int, default 50). MUST retornar `status.HTTP_202_ACCEPTED`. Si ya existe un backfill corriendo, MUST retornar `status.HTTP_409_CONFLICT`.

#### Scenario: Backfill con chunk_size custom

- GIVEN no hay backfill corriendo
- WHEN se llama `POST /internal/embed/backfill?chunk_size=100`
- THEN retorna `status.HTTP_202_ACCEPTED`
- AND el backfill usa chunks de 100

### Requirement: R5 — GET /internal/runs/{run_id}

El sistema MUST retornar el run completo para un `run_id` válido. Si no existe, MUST retornar `status.HTTP_404_NOT_FOUND`. (Implementación delegada a pipeline-runs R5.)

#### Scenario: Polling de run activo

- GIVEN n8n tiene un `run_id` de un rank en progreso
- WHEN llama `GET /internal/runs/{run_id}` cada 30s
- THEN cada respuesta muestra el `status` actualizado hasta que sea `completed` o `failed`

### Requirement: R6 — GET /internal/runs/latest

El sistema MUST retornar el run más reciente por `kind` (query param requerido). Kind MUST ser uno de `scrape|rank|backfill`. Kind inválido MUST retornar `status.HTTP_400_BAD_REQUEST`. Sin runs del kind, MUST retornar `status.HTTP_404_NOT_FOUND`.

#### Scenario: Latest de kind válido con runs existentes

- GIVEN existen 3 runs de `kind = "scrape"`
- WHEN se llama `GET /internal/runs/latest?kind=scrape`
- THEN retorna el más reciente por `started_at DESC`

## HTTP Status Codes

| Endpoint | Condición | Status |
|----------|-----------|--------|
| Todos `/internal/*` | Sin/mala key | `status.HTTP_401_UNAUTHORIZED` |
| `POST /internal/scrape/run` | OK | `status.HTTP_202_ACCEPTED` |
| `POST /internal/rank/run` | OK | `status.HTTP_202_ACCEPTED` |
| `POST /internal/embed/backfill` | OK | `status.HTTP_202_ACCEPTED` |
| Cualquier trigger | Run mismo kind corriendo | `status.HTTP_409_CONFLICT` |
| `GET /internal/runs/{id}` | Encontrado | `status.HTTP_200_OK` |
| `GET /internal/runs/{id}` | No encontrado | `status.HTTP_404_NOT_FOUND` |
| `GET /internal/runs/latest` | OK | `status.HTTP_200_OK` |
| `GET /internal/runs/latest` | Sin runs del kind | `status.HTTP_404_NOT_FOUND` |
| `GET /internal/runs/latest` | Kind inválido | `status.HTTP_400_BAD_REQUEST` |

## Response Shapes

**202 (trigger):**
```json
{"run_id": "uuid", "kind": "scrape|rank|backfill", "status": "queued"}
```

**409 (conflicto):**
```json
{"run_id": "uuid", "kind": "rank", "status": "running"}
```

**200 (run completo):**
```json
{
  "id": "uuid",
  "kind": "rank",
  "status": "completed|running|queued|failed",
  "started_at": "timestamptz",
  "finished_at": "timestamptz|null",
  "stats": {},
  "error": "string|null"
}
```

## Out of Scope

- Autenticación OAuth/JWT (API key es suficiente para uso personal)
- Rate limiting de los endpoints internos
- Listado paginado de todos los runs
- Webhook de notificación al completar (n8n hace polling)
