from pydantic import BaseModel


class Job(BaseModel):
    platform: str
    external_id: str
    title: str
    company: str
    location: str
    posted_date: str
    url: str
    description: str = ""


class JobResponse(BaseModel):
    source: str
    total: int
    jobs: list[Job]
