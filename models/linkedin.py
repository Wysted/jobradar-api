from enum import Enum

from pydantic import BaseModel, Field


class TimeRange(str, Enum):
    DAY = "r86400"
    WEEK = "r604800"
    MONTH = "r2592000"


class JobType(str, Enum):
    FULL_TIME = "F"
    PART_TIME = "P"
    CONTRACT = "C"
    TEMPORARY = "T"
    INTERNSHIP = "I"
    VOLUNTEER = "V"
    OTHER = "O"


class ExperienceLevel(str, Enum):
    INTERNSHIP = "1"
    ENTRY_LEVEL = "2"
    ASSOCIATE = "3"
    MID_SENIOR = "4"
    DIRECTOR = "5"
    EXECUTIVE = "6"


class WorkMode(str, Enum):
    ON_SITE = "1"
    REMOTE = "2"
    HYBRID = "3"


class SortBy(str, Enum):
    RELEVANCE = "relevance"
    DATE_POSTED = "date_posted"


class LinkedInSearchParams(BaseModel):
    keywords: str = "software developer"
    location: str = ""
    geo_id: str = ""
    time_range: TimeRange | None = TimeRange.WEEK
    max_pages: int = Field(default=1, ge=1, le=5)
    fetch_details: bool = True
    job_type: list[JobType] | None = None
    experience: list[ExperienceLevel] | None = None
    work_mode: list[WorkMode] | None = None
    easy_apply: bool = False
    sort_by: SortBy | None = None
    company_ids: list[str] | None = None
    distance: int | None = None
