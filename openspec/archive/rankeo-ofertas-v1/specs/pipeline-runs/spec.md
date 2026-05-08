# pipeline-runs Specification

## Purpose

Registra y expone el estado de los jobs batch del sistema (scrape, rank, backfill), permitiendo polling externo (n8n) sin timeouts HTTP. Garantiza consistencia de estado y unicidad de concurrencia por kind.

## Requirements

### Requirement: R1 — Tabla pipeline_runs

El sistema MUST almacenar cada ejecución batch en `pipeline_runs` con los campos: `id uuid PK`, `kind text` (scrape|rank|backfill), `status text` (queued|running|completed|failed), `started_at timestamptz`, `finished_at timestamptz nullable`, `stats jsonb`, `error text nullable`.

#### Scenario: Run creado queda en queued

- GIVEN se dispara un nuevo run de tipo `rank`
- WHEN el endpoint retorna 202
- THEN existe una fila en `pipeline_runs` con `status = "queued"` y `finished_at = null`

### Requirement: R2 — Transiciones de estado válidas

El sistema MUST aplicar las siguientes transiciones: `queued → running → completed` y `queued → running → failed`. Cualquier otra transición MUST ser rechazada internamente con error. `finished_at` MUST poblarse cuando el status pasa a `completed` o `failed`.

#### Scenario: Transición válida queued → running → completed

- GIVEN un run en `queued`
- WHEN el worker lo toma y termina exitosamente
- THEN el status es `completed` y `finished_at` está poblado

#### Scenario: Transición inválida es rechazada

- GIVEN un run en `completed`
- WHEN algo intenta moverlo a `running`
- THEN la transición es rechazada y el run permanece en `completed`

### Requirement: R3 — Regla de concurrencia única por kind

El sistema MUST rechazar con `status.HTTP_409_CONFLICT` cualquier intento de iniciar un run del mismo `kind` si ya existe uno en estado `running`. El body del 409 MUST incluir el `run_id` del run activo. Esta regla aplica independientemente para cada kind.

#### Scenario: Segundo rank mientras uno está corriendo devuelve 409

- GIVEN existe un run de `kind = "rank"` con `status = "running"`
- WHEN se llama `POST /internal/rank/run`
- THEN el sistema retorna `status.HTTP_409_CONFLICT`
- AND el body incluye `{"run_id": "<id_del_run_activo>", "kind": "rank", "status": "running"}`

#### Scenario: Scrape y rank pueden correr en paralelo

- GIVEN existe un run de `kind = "scrape"` con `status = "running"`
- WHEN se llama `POST /internal/rank/run`
- THEN el sistema acepta la solicitud y retorna `status.HTTP_202_ACCEPTED`

### Requirement: R4 — Shapes estandarizadas de stats por kind

El sistema MUST persistir `stats` en el formato correcto según el kind al finalizar cada run:

**scrape:**
```json
{"platforms": {"linkedin": {"scraped": 0, "new": 0}, "chiletrabajos": {"scraped": 0, "new": 0}}, "total_new": 0, "duration_s": 0.0}
```

**rank:**
```json
{"candidates_after_filter": 0, "topk_size": 0, "llm_calls": 0, "tokens_in": 0, "tokens_out": 0, "new_scores": 0, "updated_scores": 0, "duration_s": 0.0}
```

**backfill:**
```json
{"jobs_processed": 0, "jobs_embedded": 0, "chunks": 0, "errors": 0, "duration_s": 0.0}
```

#### Scenario: Run completado tiene stats correctas

- GIVEN un run de `kind = "rank"` terminó exitosamente
- WHEN se consulta `GET /internal/runs/{run_id}`
- THEN `stats` contiene exactamente los campos del shape `rank` con valores numéricos correctos

#### Scenario: Run fallido tiene error poblado

- GIVEN un run terminó con excepción
- WHEN se consulta `GET /internal/runs/{run_id}`
- THEN `status = "failed"`, `error` contiene el mensaje de error, y `stats` puede estar vacío o parcial

### Requirement: R5 — Consulta de runs

El sistema MUST exponer `GET /internal/runs/{run_id}` que retorna el run completo. MUST retornar `status.HTTP_404_NOT_FOUND` si el `run_id` no existe. MUST exponer `GET /internal/runs/latest?kind=<kind>` que retorna el run más reciente del kind dado.

#### Scenario: Run existente es retornado

- GIVEN existe un run con id conocido
- WHEN se llama `GET /internal/runs/{run_id}`
- THEN retorna `status.HTTP_200_OK` con el run completo

#### Scenario: Run no encontrado

- GIVEN el run_id no existe
- WHEN se llama `GET /internal/runs/{run_id}`
- THEN retorna `status.HTTP_404_NOT_FOUND`

#### Scenario: Latest con kind inválido

- GIVEN cualquier estado del sistema
- WHEN se llama `GET /internal/runs/latest?kind=invalid`
- THEN retorna `status.HTTP_400_BAD_REQUEST`

#### Scenario: Latest sin runs de ese kind

- GIVEN nunca se ha corrido un run de `kind = "backfill"`
- WHEN se llama `GET /internal/runs/latest?kind=backfill`
- THEN retorna `status.HTTP_404_NOT_FOUND`

## HTTP Status Codes

| Operación | Condición | Status |
|-----------|-----------|--------|
| `GET /internal/runs/{id}` | Encontrado | `status.HTTP_200_OK` |
| `GET /internal/runs/{id}` | No encontrado | `status.HTTP_404_NOT_FOUND` |
| `GET /internal/runs/latest` | Encontrado | `status.HTTP_200_OK` |
| `GET /internal/runs/latest` | Sin runs del kind | `status.HTTP_404_NOT_FOUND` |
| `GET /internal/runs/latest` | Kind inválido | `status.HTTP_400_BAD_REQUEST` |
| Trigger de cualquier run | Conflicto de concurrencia | `status.HTTP_409_CONFLICT` |

## Out of Scope

- Listado paginado de runs históricos
- Cancelación de runs en progreso
- Retención/purga automática de runs antiguos
- Notificaciones push al completar (n8n hace polling)
