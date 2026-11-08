"""Fake session and context shared by the scraper and store tests."""

import json
import logging
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from visualizer_sdk import Context


class FakeSession:
    """`pages` maps page number to a payload, an int HTTP status, or an Exception."""

    def __init__(self, pages: dict[int, Any]) -> None:
        self.pages = pages
        self.urls: list[str] = []
        self.native = self

    async def get(self, url: str) -> Any:
        self.urls.append(url)
        if "/companies/" in url:
            body = {"data": {"attributes": {"name": "Acme"}}}
            return SimpleNamespace(status=200, body=json.dumps(body))
        page = int(url.split("page=")[1].split("&")[0])
        out = self.pages.get(page, {"data": []})
        if isinstance(out, Exception):
            raise out
        if isinstance(out, int):
            return SimpleNamespace(status=out, body="")
        return SimpleNamespace(status=200, body=json.dumps(out))


def ctx_for(session: FakeSession) -> Context:
    ctx = Context(session_id=uuid4(), log=logging.getLogger("test"))
    ctx.bind_session(session)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
    return ctx


def jobs_page(*ids: str) -> dict[str, Any]:
    return {"data": [{"id": i, "attributes": {"title": f"job {i}"}} for i in ids]}
