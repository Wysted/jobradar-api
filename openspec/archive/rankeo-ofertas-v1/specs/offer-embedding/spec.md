# offer-embedding Specification

## Purpose

Genera y almacena embeddings vectoriales (OpenAI `text-embedding-3-small`, 1536 dims) para las ofertas de trabajo, habilitando búsqueda por similitud coseno en el pipeline de rankeo.

## Requirements

### Requirement: R1 — Columnas de embedding en jobs

El sistema MUST agregar `embedding vector(1536)` y `embedded_at timestamptz` (nullable) a la tabla `jobs`. Un valor `null` en `embedded_at` indica que la oferta aún no fue embebida.

#### Scenario: Job nuevo no tiene embedding

- GIVEN se inserta un nuevo job via scraper
- WHEN se consulta `jobs.embedded_at` para ese job
- THEN `embedded_at` es `null`

### Requirement: R2 — Serialización determinista de jobs

El sistema MUST exponer una función `serialize_job_for_embedding(job) -> str` cuya salida sea determinista para el mismo input. El formato exacto (campos incluidos, orden) se define en sdd-design.

#### Scenario: Mismo job produce mismo string

- GIVEN un objeto job con campos fijos
- WHEN se llama `serialize_job_for_embedding` dos veces
- THEN el resultado es idéntico en ambas llamadas

### Requirement: R3 — Backfill idempotente

El sistema MUST procesar únicamente jobs donde `embedded_at IS NULL`. Jobs ya embebidos MUST ser ignorados. El backfill MUST ser re-ejecutable sin efectos secundarios.

#### Scenario: Backfill saltea jobs ya embebidos

- GIVEN existen 10 jobs, 6 con `embedded_at` poblado y 4 con `embedded_at = null`
- WHEN se ejecuta el backfill
- THEN solo se procesan 4 jobs
- AND los 6 ya embebidos no se modifican

#### Scenario: Backfill en base limpia procesa todos

- GIVEN todos los jobs tienen `embedded_at = null`
- WHEN se ejecuta el backfill
- THEN todos los jobs reciben embedding y `embedded_at` se actualiza

### Requirement: R4 — Procesamiento por chunks

El sistema MUST procesar los jobs en chunks de tamaño configurable (default 50) para respetar rate limits de OpenAI. El tamaño del chunk SHOULD ser configurable via query param `chunk_size` en el endpoint de backfill.

#### Scenario: Backfill con 120 jobs usa múltiples chunks

- GIVEN existen 120 jobs sin embedding y chunk_size=50
- WHEN se ejecuta el backfill
- THEN se realizan 3 llamadas a la API de OpenAI (50 + 50 + 20)
- AND todos los jobs quedan con `embedded_at` poblado

### Requirement: R5 — Retry con backoff exponencial en errores transitorios

El sistema MUST reintentar la llamada a OpenAI con backoff exponencial ante errores 429 (rate limit) y 5xx (errores transitorios). El número máximo de reintentos y el backoff inicial se definen en sdd-design.

#### Scenario: Error 429 dispara retry

- GIVEN la API de OpenAI responde 429 en el primer intento
- WHEN el sistema reintenta con backoff
- THEN en el segundo intento con respuesta 200, el embedding se persiste correctamente

#### Scenario: Falla permanente después de máximos reintentos

- GIVEN la API de OpenAI responde 429 en todos los intentos hasta el máximo
- WHEN se agotan los reintentos
- THEN el chunk falla, el error se loguea, y el job continúa siendo `embedded_at = null`

### Requirement: R6 — Fallo parcial dentro de un chunk

El sistema SHOULD registrar qué jobs fallaron dentro de un chunk sin abortar el resto del backfill. Jobs fallidos MUST quedar con `embedded_at = null` para ser reintentados en la próxima ejecución.

#### Scenario: Error en un job no aborta el chunk

- GIVEN un chunk de 50 jobs donde el job #30 produce un error no recuperable
- WHEN el sistema procesa el chunk
- THEN los otros 49 jobs son embebidos exitosamente
- AND el job #30 permanece con `embedded_at = null`

## HTTP Status Codes

| Operación | Condición | Status |
|-----------|-----------|--------|
| `POST /internal/embed/backfill` | Backfill encolado | `status.HTTP_202_ACCEPTED` |
| `POST /internal/embed/backfill` | Backfill ya corriendo | `status.HTTP_409_CONFLICT` |

## Response Shape (202)

```json
{
  "run_id": "uuid",
  "kind": "backfill",
  "status": "queued"
}
```

## Out of Scope

- Embedding del perfil de usuario (se calcula inline en el ranking run, no en backfill)
- Embedding en tiempo real al insertar un job (solo batch)
- Soporte a modelos de embedding distintos de `text-embedding-3-small`
- Re-embedding de jobs ya embebidos cuando cambia el modelo
