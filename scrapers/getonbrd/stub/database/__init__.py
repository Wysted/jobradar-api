"""Stands in for backend/database/__init__.py, which imports the API's engine and config (a mkdir and the OpenAI
settings). The scraper only needs the declarative base the ORM models subclass."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
