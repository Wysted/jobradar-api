import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

CACHE_THRESHOLD_TOKENS = 1024


@lru_cache(maxsize=4)
def _get_encoding(model: str):
    import tiktoken
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(model: str, text: str) -> int:
    enc = _get_encoding(model)
    return len(enc.encode(text))


def measure_prefix_tokens(model: str, system: str, digest_sample: str) -> int:
    total = count_tokens(model, system) + count_tokens(model, digest_sample)
    if total < CACHE_THRESHOLD_TOKENS:
        logger.warning(
            "cv prefix %d tokens < %d — OpenAI prompt caching will NOT activate for model=%s",
            total, CACHE_THRESHOLD_TOKENS, model,
        )
    else:
        logger.info(
            "cv prefix %d tokens (caching enabled) model=%s",
            total, model,
        )
    return total
