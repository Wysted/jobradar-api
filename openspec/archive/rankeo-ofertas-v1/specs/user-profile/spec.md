# user-profile Specification

## Purpose

Gestiona el perfil único estructurado del usuario que sirve de insumo para el pipeline de rankeo. Soporta upsert completo y dispara re-embedding via hash sha256 cuando el contenido cambia.

## Requirements

### Requirement: R1 — Estructura del perfil

El sistema MUST almacenar un único registro de perfil con los campos: `headline` (str), `total_years` (float), `areas[]` (objetos con `name` y `seniority` 1-5), `technologies[]` (objetos con `name`, `area_name`, `seniority` 1-5), `soft_skills[]` (str), `roles_target[]` (str), `roles_excluded[]` (str), `industries_preferred[]` (str), `industries_excluded[]` (str), `location{}` (`city`, `country`, `willing_to_relocate` bool), `work_mode[]` (str: remote/hybrid/onsite), `salary{}` (`currency` str, `min_monthly` int, `expected_monthly` int|null), `languages[]` (objetos con `name` y `level` str), `contract_types[]` (str), `company_size_pref[]` (str), `deal_breakers[]` (str). La seniority MUST ser numérica 1-5 (1=exposure, 5=staff/principal). `willing_to_relocate` expresa disposición a mudarse físicamente (distinto de `work_mode` que define modalidad del trabajo). `expected_monthly` es el salario deseado usado por el rerank LLM para scoring; `min_monthly` es el piso duro usado por el filtro SQL de capa 1.

#### Scenario: Perfil con estructura válida es aceptado

- GIVEN no existe perfil en el sistema
- WHEN se envía `PUT /api/v1/profile` con payload completo y válido
- THEN el sistema persiste el perfil y retorna `status.HTTP_201_CREATED` con el perfil creado
- AND `embedding_hash` es `null` (pendiente de embebido)

#### Scenario: Campo seniority fuera de rango es rechazado

- GIVEN cualquier estado del sistema
- WHEN se envía `PUT /api/v1/profile` con `areas[0].seniority = 6`
- THEN el sistema retorna `status.HTTP_422_UNPROCESSABLE_ENTITY`
- AND el body incluye el campo que falló la validación

### Requirement: R2 — Semántica upsert (PUT reemplaza perfil completo)

El sistema MUST reemplazar el perfil completo en cada `PUT`. La operación MUST ser idempotente respecto al contenido. Las actualizaciones parciales se realizan mediante `PATCH` (ver R5).
(Previously: Incluía la cláusula "No se permite PATCH parcial en v1" — superseded by R5)

#### Scenario: Actualización con contenido distinto produce nuevo hash

- GIVEN existe un perfil con `embedding_hash = "abc123"`
- WHEN se envía `PUT /api/v1/profile` con payload diferente al actual
- THEN el sistema retorna `status.HTTP_200_OK` con el perfil actualizado
- AND `embedding_hash` es un nuevo sha256 distinto de `"abc123"`

#### Scenario: Actualización con contenido idéntico mantiene el mismo hash

- GIVEN existe un perfil con `embedding_hash = "abc123"`
- WHEN se envía `PUT /api/v1/profile` con payload byte-a-byte idéntico al almacenado
- THEN el sistema retorna `status.HTTP_200_OK`
- AND `embedding_hash` sigue siendo `"abc123"` (sin cambio)

### Requirement: R5 — PATCH parcial del perfil (replace-on-presence)

El sistema MUST exponer `PATCH /api/v1/profile` que acepta un subconjunto de los campos de `ProfileIn`. La operación MUST aplicar semántica replace-on-presence: solo los campos presentes en el body se reemplazan; los ausentes se preservan del perfil existente. El sistema MUST retornar `404 NOT FOUND` si no existe perfil previo (PATCH no crea). El sistema MUST retornar `200 OK` con el perfil resultante en caso de éxito. Los campos de tipo lista MUST reemplazarse en su totalidad cuando están presentes (no append). El sistema MUST revalidar el perfil merged con las invariantes de `ProfileIn` antes de persistir.

#### Scenario: PATCH de campo escalar actualiza solo ese campo

- GIVEN existe un perfil con `total_years = 3.0` y `headline = "Backend Dev"`
- WHEN se envía `PATCH /api/v1/profile` con body `{"total_years": 5.0}`
- THEN el sistema retorna `status.HTTP_200_OK`
- AND `total_years` en el perfil resultante es `5.0`
- AND `headline` sigue siendo `"Backend Dev"` sin cambios

#### Scenario: PATCH de campo lista reemplaza la lista completa

- GIVEN existe un perfil con `technologies = [{"name": "Python", "area_name": "backend", "seniority": 4}]`
- WHEN se envía `PATCH /api/v1/profile` con body `{"technologies": [{"name": "Go", "area_name": "backend", "seniority": 3}]}`
- THEN el sistema retorna `status.HTTP_200_OK`
- AND `technologies` en el perfil resultante contiene únicamente `[{"name": "Go", ...}]`
- AND los demás campos del perfil se preservan intactos

#### Scenario: PATCH sobre perfil inexistente retorna 404

- GIVEN no existe ningún perfil en el sistema
- WHEN se envía `PATCH /api/v1/profile` con cualquier body válido
- THEN el sistema retorna `status.HTTP_404_NOT_FOUND`
- AND no se crea ningún registro nuevo

#### Scenario: PATCH con cambio en campo semántico invalida embedding

- GIVEN existe un perfil con `embedding_hash = "abc123"` y `total_years = 3.0`
- WHEN se envía `PATCH /api/v1/profile` con body `{"total_years": 7.0}` (campo en `_EMBEDDING_AFFECTING_FIELDS`)
- THEN el sistema retorna `status.HTTP_200_OK`
- AND `embedding_hash` en el perfil resultante es un sha256 distinto de `"abc123"`

#### Scenario: PATCH sin cambio semántico preserva embedding_hash

- GIVEN existe un perfil con `embedding_hash = "abc123"` y `headline = "Dev"`
- WHEN se envía `PATCH /api/v1/profile` con body `{"headline": "Dev"}` (mismo valor)
- THEN el sistema retorna `status.HTTP_200_OK`
- AND `embedding_hash` sigue siendo `"abc123"`

### Requirement: R3 — Hash sha256 sobre serialización determinista

El sistema MUST calcular `embedding_hash` como sha256 del JSON serializado con claves ordenadas alfabéticamente y sin whitespace extra. El hash MUST cambiar si y solo si cambia el contenido semántico del perfil.

#### Scenario: Hash es reproducible con el mismo input

- GIVEN un payload de perfil fijo
- WHEN se calcula el hash dos veces con el mismo payload
- THEN los dos hashes son idénticos

### Requirement: R4 — Consulta del perfil

El sistema MUST exponer `GET /api/v1/profile` que retorna el perfil activo. Si no existe perfil, MUST retornar `status.HTTP_404_NOT_FOUND`.

#### Scenario: Perfil existente es retornado

- GIVEN existe un perfil en el sistema
- WHEN se llama `GET /api/v1/profile`
- THEN el sistema retorna `status.HTTP_200_OK` con el perfil completo incluyendo `embedding_hash`

#### Scenario: Sin perfil retorna 404

- GIVEN no existe ningún perfil
- WHEN se llama `GET /api/v1/profile`
- THEN el sistema retorna `status.HTTP_404_NOT_FOUND`

## Response Shape

```json
{
  "id": "uuid",
  "headline": "string",
  "total_years": 0.0,
  "areas": [{"name": "string", "seniority": 1}],
  "technologies": [{"name": "string", "area_name": "string", "seniority": 1}],
  "soft_skills": ["string"],
  "roles_target": ["string"],
  "roles_excluded": ["string"],
  "industries_preferred": ["string"],
  "industries_excluded": ["string"],
  "location": {"city": "string", "country": "string", "willing_to_relocate": false},
  "work_mode": ["remote"],
  "salary": {"currency": "USD", "min_monthly": 0, "expected_monthly": 0},
  "languages": [{"name": "string", "level": "string"}],
  "contract_types": ["string"],
  "company_size_pref": ["string"],
  "deal_breakers": ["string"],
  "embedding_hash": "sha256hex | null",
  "created_at": "timestamptz",
  "updated_at": "timestamptz"
}
```

## HTTP Status Codes

| Operación | Condición | Status |
|-----------|-----------|--------|
| `PUT` | Perfil creado por primera vez | `status.HTTP_201_CREATED` |
| `PUT` | Perfil actualizado | `status.HTTP_200_OK` |
| `PUT` | Payload inválido | `status.HTTP_422_UNPROCESSABLE_ENTITY` |
| `GET` | Perfil existe | `status.HTTP_200_OK` |
| `GET` | No existe perfil | `status.HTTP_404_NOT_FOUND` |
| `PATCH` | Perfil actualizado parcialmente | `status.HTTP_200_OK` |
| `PATCH` | No existe perfil | `status.HTTP_404_NOT_FOUND` |
| `PATCH` | Payload merged inválido | `status.HTTP_422_UNPROCESSABLE_ENTITY` |

## Out of Scope

- Múltiples usuarios (v1 es single-user)
- Historial de versiones del perfil
- Validación semántica de `area_name` en technologies (FK a areas no se valida en v1)
- Generación del embedding (responsabilidad de `offer-embedding` / ranking pipeline)
