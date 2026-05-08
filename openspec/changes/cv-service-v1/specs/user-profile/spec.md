# Delta for user-profile

## ADDED Requirements

### Requirement: R-RUT — Columna RUT identificador nacional

El sistema MUST almacenar el RUT chileno del usuario en la columna `user_profiles.rut` de tipo `TEXT NULL UNIQUE`. El valor MUST estar normalizado como `"dddddddd-d"` (dígitos sin puntos, guion antes del dígito verificador, dígito verificador 0-9 o K). Múltiples filas con `rut = NULL` MUST ser permitidas. El RUT MUST ser validado a nivel service layer (formato + dígito verificador) antes de persistir.

#### Scenario: Upsert con RUT válido
- GIVEN no existe perfil con `rut = "12345678-9"`
- WHEN se envía `PUT /api/v1/profile` con `rut: "12345678-9"` (válido)
- THEN el sistema persiste el RUT normalizado en la columna
- AND responde `201 CREATED`

#### Scenario: RUT inválido rechazado
- GIVEN payload con `rut: "12.345.678-9"` (con puntos)
- WHEN se envía `PUT /api/v1/profile`
- THEN el sistema normaliza a `"12345678-9"` antes de persistir
- AND responde `201` o `200`

#### Scenario: RUT con dígito verificador incorrecto
- GIVEN payload con `rut: "12345678-0"` (checksum inválido)
- WHEN se envía `PUT /api/v1/profile`
- THEN el sistema responde `422 UNPROCESSABLE_ENTITY`
- AND el body detalla `"invalid RUT check digit"`

#### Scenario: RUT duplicado rechazado
- GIVEN existe perfil A con `rut = "12345678-9"`
- WHEN se crea perfil B con el mismo RUT
- THEN la operación falla con `409 CONFLICT` o `IntegrityError` capturado como `422`

### Requirement: R-DIGEST-INVAL — Invalidación del digest al cambiar perfil semántico

El sistema MUST setear `payload.digest = null` en `upsert_profile` cuando `embedding_hash` cambia (indicador de que el contenido semántico cambió). La regeneración del digest MUST ser LAZY: el próximo consumidor (CV generator) detecta `digest is null` y lo regenera síncronamente vía LLM digester.

#### Scenario: Cambio semántico invalida digest
- GIVEN perfil con `payload.digest = {...}` y `embedding_hash = "abc"`
- WHEN se envía `PUT /api/v1/profile` con payload que produce nuevo hash `"xyz"`
- THEN `payload.digest` queda `null` en la misma transacción
- AND `embedding_hash = "xyz"`

#### Scenario: Cambio NO semántico preserva digest
- GIVEN perfil con `payload.digest = {...}` y `embedding_hash = "abc"`
- WHEN se envía `PUT /api/v1/profile` con payload byte-idéntico
- THEN `payload.digest` se preserva intacto
- AND `embedding_hash` sigue siendo `"abc"`

#### Scenario: Regeneración lazy en batch CV
- GIVEN perfil con `payload.digest = null`
- WHEN el batch CV procesa el primer item
- THEN el sistema llama al digester LLM, persiste el digest
- AND los items siguientes del mismo batch reusan el digest ya persistido
