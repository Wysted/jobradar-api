# public-api Specification

## Purpose

Expone endpoints bajo `/api/v1/*` para consumo personal del perfil y de las ofertas rankeadas. Todos los endpoints requieren el header `X-Api-Key`.

## Requirements

### Requirement: R1 — Autenticación con X-Api-Key

El sistema MUST proteger todos los endpoints `/api/v1/*` con la dependency `require_api_key` que valida el header `X-Api-Key`. Requests sin el header o con key inválida MUST retornar `status.HTTP_401_UNAUTHORIZED`.

#### Scenario: Request sin header retorna 401

- GIVEN cualquier endpoint `/api/v1/*`
- WHEN se llama sin el header `X-Api-Key`
- THEN el sistema retorna `status.HTTP_401_UNAUTHORIZED`

### Requirement: R2 — GET /api/v1/profile

El sistema MUST retornar el perfil activo. Si no existe perfil, MUST retornar `status.HTTP_404_NOT_FOUND`. (Implementación delegada a user-profile R4.)

#### Scenario: Perfil existente es retornado

- GIVEN existe un perfil
- WHEN se llama `GET /api/v1/profile` con key válida
- THEN retorna `status.HTTP_200_OK` con el perfil completo

### Requirement: R3 — PUT /api/v1/profile

El sistema MUST aceptar el perfil completo y retornar `status.HTTP_201_CREATED` si es la primera creación, o `status.HTTP_200_OK` si actualiza uno existente. Payload inválido MUST retornar `status.HTTP_422_UNPROCESSABLE_ENTITY`. (Implementación delegada a user-profile R1/R2.)

#### Scenario: Primera creación del perfil

- GIVEN no existe perfil en el sistema
- WHEN se llama `PUT /api/v1/profile` con payload válido
- THEN retorna `status.HTTP_201_CREATED`

#### Scenario: Actualización de perfil existente

- GIVEN existe un perfil
- WHEN se llama `PUT /api/v1/profile` con payload válido
- THEN retorna `status.HTTP_200_OK`

#### Scenario: Payload inválido retorna 422

- GIVEN cualquier estado del sistema
- WHEN se llama `PUT /api/v1/profile` con campo `seniority = 10`
- THEN retorna `status.HTTP_422_UNPROCESSABLE_ENTITY` con detalle del campo fallido

### Requirement: R4 — GET /api/v1/jobs/ranked

El sistema MUST retornar ofertas rankeadas ordenadas por `llm_score DESC, scored_at DESC`. MUST soportar los siguientes query params:

| Param | Tipo | Default | Restricción |
|-------|------|---------|-------------|
| `min_score` | int | 60 | 0-100 |
| `limit` | int | 50 | max 200 |
| `platform` | string | null | opcional |
| `only_new` | bool | false | ver nota |

`only_new` es una open question — su mecanismo de "consumo" se define en sdd-design.

#### Scenario: Listado básico con defaults

- GIVEN existen 80 jobs con `llm_score >= 60` en `job_scores`
- WHEN se llama `GET /api/v1/jobs/ranked`
- THEN retorna `status.HTTP_200_OK` con `{"total": 80, "items": [...]}`
- AND `items` contiene hasta 50 ofertas ordenadas por `llm_score DESC`

#### Scenario: Filtro por plataforma

- GIVEN existen jobs de "linkedin" y "chiletrabajos"
- WHEN se llama `GET /api/v1/jobs/ranked?platform=linkedin`
- THEN retorna solo jobs cuya oferta pertenece a la plataforma `"linkedin"`

#### Scenario: Filtro min_score

- GIVEN existen jobs con scores 40, 65, 80, 95
- WHEN se llama `GET /api/v1/jobs/ranked?min_score=70`
- THEN retorna solo los jobs con `llm_score >= 70` (scores 80 y 95)

#### Scenario: Limit respetado

- GIVEN existen 200 jobs rankeados
- WHEN se llama `GET /api/v1/jobs/ranked?limit=10`
- THEN retorna `{"total": 200, "items": [...]}`
- AND `items` contiene exactamente 10 elementos

#### Scenario: Sin ofertas rankeadas aún

- GIVEN `job_scores` está vacío
- WHEN se llama `GET /api/v1/jobs/ranked`
- THEN retorna `status.HTTP_200_OK` con `{"total": 0, "items": []}`

#### Scenario: limit excede máximo permitido

- GIVEN cualquier estado del sistema
- WHEN se llama `GET /api/v1/jobs/ranked?limit=500`
- THEN retorna `status.HTTP_422_UNPROCESSABLE_ENTITY`

## HTTP Status Codes

| Endpoint | Condición | Status |
|----------|-----------|--------|
| Todos `/api/v1/*` | Sin/mala key | `status.HTTP_401_UNAUTHORIZED` |
| `GET /api/v1/profile` | Perfil existe | `status.HTTP_200_OK` |
| `GET /api/v1/profile` | Sin perfil | `status.HTTP_404_NOT_FOUND` |
| `PUT /api/v1/profile` | Creado | `status.HTTP_201_CREATED` |
| `PUT /api/v1/profile` | Actualizado | `status.HTTP_200_OK` |
| `PUT /api/v1/profile` | Payload inválido | `status.HTTP_422_UNPROCESSABLE_ENTITY` |
| `GET /api/v1/jobs/ranked` | OK (puede ser vacío) | `status.HTTP_200_OK` |
| `GET /api/v1/jobs/ranked` | Param inválido | `status.HTTP_422_UNPROCESSABLE_ENTITY` |

## Response Shape — GET /api/v1/jobs/ranked

```json
{
  "total": 0,
  "items": [
    {
      "job_id": "uuid",
      "title": "string",
      "company": "string",
      "platform": "string",
      "url": "string",
      "published_at": "timestamptz",
      "llm_score": 85,
      "vector_score": 0.92,
      "matched_areas": [{"name": "Backend", "profile_sen": 4, "job_sen": 3}],
      "matched_techs": [{"name": "Python", "profile_sen": 4, "job_sen": 3}],
      "missing_skills": ["Kubernetes"],
      "red_flags": ["Requires on-site relocation"],
      "pitch_angles": ["Strong async Python background matches stack"],
      "reasoning": "string",
      "scored_at": "timestamptz"
    }
  ]
}
```

## Open Question

- Mecanismo de `only_new`: ¿campo `consumed_at` en `job_scores`? ¿flag booleano? Diferido a sdd-design.

## Out of Scope

- Creación/edición de ofertas via esta API (solo lectura de jobs)
- Autenticación OAuth/JWT
- Paginación con cursor (solo limit en v1)
- Export a CSV/PDF
- Webhooks de notificación
