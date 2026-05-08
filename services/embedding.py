import logging

from tenacity import retry

from dependencies.openai_client import EMBEDDING_MODEL
from services.retry import openai_retry_policy

logger = logging.getLogger(__name__)

EMBED_DIM = 1536


@retry(**openai_retry_policy)
async def _embed_batch_with_retry(client, texts: list[str]) -> list[list[float]]:
    response = await client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]


async def _embed_batch_recursive(client, texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    try:
        return await _embed_batch_with_retry(client, texts)
    except Exception:
        if len(texts) == 1:
            raise
        mid = len(texts) // 2
        left = await _embed_batch_recursive(client, texts[:mid])
        right = await _embed_batch_recursive(client, texts[mid:])
        return left + right


async def embed_texts(client, texts: list[str]) -> list[list[float]]:
    return await _embed_batch_recursive(client, texts)


async def embed_one(client, text: str) -> list[float]:
    results = await embed_texts(client, [text])
    return results[0]
