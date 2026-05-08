from enum import Enum

from pydantic import BaseModel, Field


class DateFilter(str, Enum):
    ANY = ""
    TODAY = "1"
    YESTERDAY = "2"
    THREE_DAYS = "3"
    SIX_DAYS = "6"
    WEEK = "7"
    MONTH = "30"


class JobType(str, Enum):
    PART_TIME = "Part-time"
    FULL_TIME = "Full-time"
    BOTH = "Part-time & Full-time"


class ChileTrabajosSearchParams(BaseModel):
    keywords: str = ""
    location: str = ""
    date_filter: DateFilter | None = None
    category: str = ""
    job_type: JobType | None = None
    company: str = ""
    max_pages: int = Field(default=1, ge=1, le=5)
    fetch_details: bool = True
