from enum import IntEnum

from pydantic import BaseModel, Field


class DateFilter(IntEnum):
    LAST_24H = 1
    LAST_7D = 7
    LAST_30D = 30


class WorkSchedule(IntEnum):
    COMPLETA = 9
    PARCIAL = 10
    SIN_LIMITACION = 11


class ContractType(IntEnum):
    OBRA_O_FAENA = 1
    PLAZO_FIJO = 2
    INDEFINIDO = 3
    ADMINISTRACION_PUBLICA = 4
    HONORARIOS = 5
    NO_SABE = 6
    INDIFERENTE = 7
    PRACTICA = 8
    APRENDIZAJE = 9
    SIN_CONTRATO = 10


class EducationLevel(IntEnum):
    SIN_EDUCACION = 1
    BASICA_INCOMPLETA = 2
    BASICA_COMPLETA = 3
    MEDIA_INCOMPLETA = 4
    MEDIA_COMPLETA = 5
    SUPERIOR_INCOMPLETA = 6
    SUPERIOR_COMPLETA = 7
    MAGISTER = 8


class EmploymentGroup(IntEnum):
    INDIGENAS = 1
    MIGRANTES = 2
    ADULTOS_MAYORES = 3
    INFRACTORES_DE_LEY = 4
    JOVENES = 5
    PENSION_INVALIDEZ = 7
    MUJER = 8


REGION_IDS: dict[str, int] = {
    "Metropolitana": 378,
    "Valparaíso": 375,
    "Antofagasta": 387,
    "Los Lagos": 381,
    "Bío Bío": 384,
    "Maule": 379,
    "Coquimbo": 383,
    "Los Ríos": 389,
    "Atacama": 386,
    "O'Higgins": 377,
    "Tarapacá": 376,
    "La Araucanía": 382,
    "Ñuble": 391,
    "Aysén": 385,
    "Magallanes": 380,
    "Arica y Parinacota": 388,
}


class BNESearchParams(BaseModel):
    keywords: str = ""
    region_ids: list[int] = Field(default_factory=list)
    work_schedule: list[int] = Field(default_factory=list)
    contract_type: list[int] = Field(default_factory=list)
    education_level: list[int] = Field(default_factory=list)
    employment_group: list[int] = Field(default_factory=list)
    date_filter: int | None = None
    max_pages: int = Field(default=1, ge=1, le=10)
