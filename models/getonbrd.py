from pydantic import BaseModel, Field


class GetOnBrdSearchParams(BaseModel):
    keywords: str = ""
    country_code: str = ""
    max_pages: int = Field(default=1, ge=1, le=5)
