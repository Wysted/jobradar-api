import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

_RUT_RAW_RE = re.compile(r"[^0-9kK]")


def _normalize_rut(raw: str) -> str:
    cleaned = _RUT_RAW_RE.sub("", raw).upper()
    if len(cleaned) < 2:
        raise ValueError("rut too short")
    body, dv = cleaned[:-1], cleaned[-1]
    if not body.isdigit():
        raise ValueError("rut body must be digits")
    return f"{body}-{dv}"


def _validate_rut_check_digit(rut: str) -> str:
    body, dv = rut.split("-")
    digits = list(map(int, body[::-1]))
    factors = [2, 3, 4, 5, 6, 7] * ((len(digits) + 5) // 6)
    total = sum(d * f for d, f in zip(digits, factors))
    remainder = 11 - (total % 11)
    expected_dv = {11: "0", 10: "K"}.get(remainder, str(remainder))
    if expected_dv != dv:
        raise ValueError("invalid RUT check digit")
    return rut


class AreaIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    seniority: int = Field(..., ge=1, le=5)


class TechnologyIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    area_name: str = Field(..., min_length=1, max_length=100)
    seniority: int = Field(..., ge=1, le=5)


class LocationIn(BaseModel):
    city: str | None = None
    country: str | None = None
    willing_to_relocate: bool = False


class SalaryIn(BaseModel):
    currency: str = Field(..., min_length=3, max_length=3)
    min_monthly: int = Field(..., ge=0)
    expected_monthly: int | None = Field(default=None, ge=0)


class LanguageIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    level: str = Field(..., min_length=1, max_length=20)


class PersonalInfoIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=50)


class LinksIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    linkedin: str | None = Field(default=None, max_length=500)
    github: str | None = Field(default=None, max_length=500)
    portfolio: str | None = Field(default=None, max_length=500)


class AchievementIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = Field(..., min_length=1)
    tags: list[str] = Field(default_factory=list)


class ExperienceIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str | None = None
    company: str = Field(..., min_length=1, max_length=200)
    location: str | None = None
    role: str = Field(..., min_length=1, max_length=200)
    subtitle: str | None = None
    dates: str = Field(default="", max_length=200)
    start: str | None = None
    end: str | None = None
    is_current: bool = False
    areas: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    achievements: list[AchievementIn] = Field(default_factory=list)


class EducationIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    institution: str = Field(..., min_length=1, max_length=200)
    location: str | None = None
    degree: str = Field(..., min_length=1, max_length=200)
    dates: str = Field(default="", max_length=200)
    start: str | None = None
    end: str | None = None
    details: list[str] = Field(default_factory=list)


class ProfileIn(BaseModel):
    headline: str | None = None
    total_years: float = Field(..., ge=0)
    rut: str | None = None
    personal_info: PersonalInfoIn | None = None
    links: LinksIn | None = None
    summary: str | None = None
    methodologies: list[str] = Field(default_factory=list)
    experiences: list[ExperienceIn] = Field(default_factory=list)
    education: list[EducationIn] = Field(default_factory=list)
    areas: list[AreaIn] = Field(default_factory=list)
    technologies: list[TechnologyIn] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)
    roles_target: list[str] = Field(default_factory=list)
    roles_excluded: list[str] = Field(default_factory=list)
    industries_preferred: list[str] = Field(default_factory=list)
    industries_excluded: list[str] = Field(default_factory=list)
    location: LocationIn
    work_mode: list[str] = Field(default_factory=list)
    salary: SalaryIn | None = None
    languages: list[LanguageIn] = Field(default_factory=list)
    contract_types: list[str] = Field(default_factory=list)
    company_size_pref: list[str] = Field(default_factory=list)
    deal_breakers: list[str] = Field(default_factory=list)

    @field_validator("rut", mode="before")
    @classmethod
    def _normalize_and_validate_rut(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        normalized = _normalize_rut(value)
        return _validate_rut_check_digit(normalized)


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline: str | None = None
    total_years: float | None = Field(default=None, ge=0)
    rut: str | None = None
    personal_info: PersonalInfoIn | None = None
    links: LinksIn | None = None
    summary: str | None = None
    methodologies: list[str] | None = None
    experiences: list[ExperienceIn] | None = None
    education: list[EducationIn] | None = None
    areas: list[AreaIn] | None = None
    technologies: list[TechnologyIn] | None = None
    soft_skills: list[str] | None = None
    roles_target: list[str] | None = None
    roles_excluded: list[str] | None = None
    industries_preferred: list[str] | None = None
    industries_excluded: list[str] | None = None
    location: LocationIn | None = None
    work_mode: list[str] | None = None
    salary: SalaryIn | None = None
    languages: list[LanguageIn] | None = None
    contract_types: list[str] | None = None
    company_size_pref: list[str] | None = None
    deal_breakers: list[str] | None = None

    @field_validator("rut", mode="before")
    @classmethod
    def _normalize_and_validate_rut(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        normalized = _normalize_rut(value)
        return _validate_rut_check_digit(normalized)


class ProfileOut(BaseModel):
    id: str
    headline: str | None
    total_years: float
    rut: str | None = None
    personal_info: PersonalInfoIn | None = None
    links: LinksIn | None = None
    summary: str | None = None
    methodologies: list[str] = Field(default_factory=list)
    experiences: list[ExperienceIn] = Field(default_factory=list)
    education: list[EducationIn] = Field(default_factory=list)
    areas: list[AreaIn]
    technologies: list[TechnologyIn]
    soft_skills: list[str]
    roles_target: list[str]
    roles_excluded: list[str]
    industries_preferred: list[str]
    industries_excluded: list[str]
    location: LocationIn
    work_mode: list[str]
    salary: SalaryIn | None
    languages: list[LanguageIn]
    contract_types: list[str]
    company_size_pref: list[str]
    deal_breakers: list[str]
    embedding_hash: str | None
    created_at: datetime
    updated_at: datetime
