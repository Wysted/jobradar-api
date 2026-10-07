from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.scrape_orchestrator import PLATFORM_RUNNERS

MAX_QUERIES = 10
QUERY_MIN_LEN = 2
QUERY_MAX_LEN = 100
LOCATION_MAX_LEN = 100
MAX_PAGES_MIN = 1
MAX_PAGES_MAX = 5


class SearchSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queries: list[str] = Field(min_length=1, max_length=MAX_QUERIES)
    location: str = Field(max_length=LOCATION_MAX_LEN)
    max_pages: int = Field(ge=MAX_PAGES_MIN, le=MAX_PAGES_MAX)
    platforms: list[str] = Field(min_length=1)

    @field_validator("queries")
    @classmethod
    def _clean_queries(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        cleaned: list[str] = []
        for raw in value:
            query = raw.strip()
            if not QUERY_MIN_LEN <= len(query) <= QUERY_MAX_LEN:
                raise ValueError(
                    f"each query must have {QUERY_MIN_LEN}-{QUERY_MAX_LEN} characters"
                )
            key = query.casefold()
            if key not in seen:
                seen.add(key)
                cleaned.append(query)
        return cleaned

    @field_validator("location", mode="before")
    @classmethod
    def _strip_location(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("platforms")
    @classmethod
    def _check_platforms(cls, value: list[str]) -> list[str]:
        unknown = [p for p in value if p not in PLATFORM_RUNNERS]
        if unknown:
            raise ValueError(f"unknown platform(s): {', '.join(unknown)}")
        return list(dict.fromkeys(value))


class AvailablePlatform(BaseModel):
    name: str
    display_name: str


class SearchSettingsOut(BaseModel):
    queries: list[str]
    location: str
    max_pages: int
    platforms: list[str]
    available_platforms: list[AvailablePlatform]
    updated_at: datetime | None
