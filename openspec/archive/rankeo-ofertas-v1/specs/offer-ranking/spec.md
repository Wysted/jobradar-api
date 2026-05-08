# offer-ranking Specification

## Purpose

Implementa el pipeline de rankeo en 3 capas (filtros SQL, similitud vectorial, rerank LLM) que produce scores 0-100 y análisis estructurado para cada oferta candidata, persistiendo resultados en `job_scores`.

## Requirements

### Requirement: R1 — Capa 1: Filtros SQL duros

El sistema MUST traducir los campos del perfil `deal_breakers`, `location`, `work_mode`, `salary.min`, `contract_types`, `industries_excluded` y `languages` en cláusulas WHERE que filtran el conjunto de ofertas candidatas. Solo las ofertas que pasen todos los filtros activos llegan a la capa 2.

#### Scenario: Pipeline completo con candidatos

- GIVEN existe perfil con `work_mode: ["remote"]` y existen 100 jobs, 40 con `work_mode = "remote"`
- WHEN se ejecuta el pipeline de ranking
- THEN la capa 1 retorna exactamente 40 candidatos
- AND la capa 2 recibe solo esos 40

#### Scenario: Capa 1 retorna conjunto vacío

- GIVEN el perfil tiene filtros que no matchean ningún job
- WHEN se ejecuta el pipeline de ranking
- THEN la capa 1 retorna 0 candidatos
- AND el pipeline termina con stats `candidates_after_filter: 0`, sin llamadas a OpenAI
- AND el `pipeline_run` queda en `completed` (no `failed`)

### Requirement: R2 — Capa 2: Similitud coseno vectorial

El sistema MUST calcular la similitud coseno entre el embedding del perfil y los embeddings del conjunto candidato, retornando los top-K (default 50) por mayor similitud. Si el `embedding_hash` del perfil cambió desde el último ranking, el sistema MUST regenerar el embedding del perfil antes de correr L2.

#### Scenario: Re-embedding de perfil por cambio de hash

- GIVEN el perfil tiene `embedding_hash = "abc"` y el embedding almacenado corresponde a hash `"old"`
- WHEN se inicia un ranking run
- THEN el sistema detecta la diferencia de hash y regenera el embedding del perfil
- AND continúa con el nuevo embedding en L2

#### Scenario: Top-K con menos candidatos que K

- GIVEN la capa 1 retorna 20 candidatos y K=50
- WHEN se ejecuta la capa 2
- THEN retorna los 20 candidatos ordenados por similitud (no falla por cantidad < K)

#### Scenario: Similitud cero para todos los candidatos

- GIVEN todos los candidatos tienen similitud coseno = 0 con el perfil
- WHEN se ejecuta la capa 2
- THEN el pipeline termina sin llamar a la capa 3
- AND el `pipeline_run` queda en `completed` con `topk_size: 0`

### Requirement: R3 — Capa 3: Rerank LLM

El sistema MUST llamar a `gpt-4o-mini` con `response_format: json_schema` (strict mode) para cada oferta en el top-K. La respuesta MUST ser validada contra el schema estricto definido abajo. Si el LLM retorna un schema inválido, el sistema MUST reintentar una vez; si falla de nuevo, MUST loguear el error y omitir esa oferta del resultado final.

#### Scenario: LLM retorna schema válido

- GIVEN una oferta en top-K
- WHEN el LLM responde con el JSON schema correcto
- THEN el sistema persiste el score y análisis en `job_scores`
- AND `scored_at` se actualiza

#### Scenario: LLM retorna schema inválido en primer intento

- GIVEN el LLM responde con JSON que no cumple el schema en el primer intento
- WHEN el sistema reintenta
- THEN si el segundo intento es válido, se persiste normalmente

#### Scenario: LLM falla dos veces

- GIVEN el LLM responde con schema inválido en ambos intentos
- WHEN se agotan los reintentos
- THEN la oferta se omite del resultado (no se persiste en `job_scores`)
- AND el error se loguea con el `job_id`

### Requirement: R4 — Persistencia en job_scores (upsert)

El sistema MUST persistir los resultados en `job_scores` con semántica upsert: si existe una fila para `(job_id, profile_id)`, se actualiza; si no existe, se inserta. El campo `scored_at` MUST actualizarse en cada re-ranking.

#### Scenario: Re-ranking actualiza fila existente

- GIVEN existe una fila en `job_scores` para `job_id=X` con `llm_score=70`
- WHEN se corre un nuevo ranking run que incluye el mismo job
- THEN la fila existente se actualiza con el nuevo score
- AND `scored_at` refleja el timestamp del nuevo run

### Requirement: R5 — Schema de output LLM (estricto)

El output del LLM MUST conformar exactamente al siguiente schema JSON:

```json
{
  "score_0_100": "int (0-100)",
  "matched_areas": [{"name": "str", "profile_sen": "int 1-5", "job_sen": "int 1-5"}],
  "matched_techs": [{"name": "str", "profile_sen": "int 1-5", "job_sen": "int 1-5"}],
  "missing_skills": ["str"],
  "red_flags": ["str"],
  "pitch_angles": ["str"],
  "reasoning": "str"
}
```

## Tabla job_scores

| Columna | Tipo | Notas |
|---------|------|-------|
| `id` | uuid PK | |
| `job_id` | uuid FK unique | Un score por job |
| `profile_id` | uuid FK | |
| `vector_score` | float | Similitud coseno L2 |
| `llm_score` | int | 0-100 de L3 |
| `matched_areas` | jsonb | Array de objetos |
| `matched_techs` | jsonb | Array de objetos |
| `missing_skills` | jsonb | Array de strings |
| `red_flags` | jsonb | Array de strings |
| `pitch_angles` | jsonb | Array de strings |
| `reasoning` | text | |
| `llm_model` | text | Nombre del modelo usado |
| `scored_at` | timestamptz | |

## HTTP Status Codes

| Operación | Condición | Status |
|-----------|-----------|--------|
| `POST /internal/rank/run` | Run encolado | `status.HTTP_202_ACCEPTED` |
| `POST /internal/rank/run` | Otro rank ya corriendo | `status.HTTP_409_CONFLICT` |

## Out of Scope

- Ranking en tiempo real (solo batch)
- Múltiples perfiles
- Ajuste de pesos entre capas (L1/L2/L3)
- Explicabilidad del score más allá del campo `reasoning`
- Cacheado de resultados del LLM entre runs
