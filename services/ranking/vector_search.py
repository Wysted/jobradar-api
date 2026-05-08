from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def vector_topk(
    session: AsyncSession,
    profile_embedding: list[float],
    candidate_ids: list[int],
    k: int,
) -> list[tuple[int, float]]:
    if not candidate_ids:
        return []

    # ef_search=40 balances recall and latency for the ~2000-candidate L2 set (design §Q1).
    await session.execute(text("SET LOCAL hnsw.ef_search = 40"))

    # pgvector text input requires [v1,v2,v3] with commas. numpy's str() uses
    # spaces + truncates with ellipsis, so we format manually from the iterable.
    emb_literal = "[" + ",".join(f"{float(v):.8f}" for v in profile_embedding) + "]"

    result = await session.execute(
        text(
            "SELECT id, 1 - (embedding <=> :emb) AS similarity"
            " FROM jobs"
            " WHERE id = ANY(:ids) AND embedding IS NOT NULL"
            " ORDER BY embedding <=> :emb"
            " LIMIT :k"
        ),
        {
            "emb": emb_literal,
            "ids": candidate_ids,
            "k": k,
        },
    )
    return [(row.id, float(row.similarity)) for row in result]
