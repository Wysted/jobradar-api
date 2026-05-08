from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)
from tenacity import (
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)

OPENAI_TRANSIENT_ERRORS = (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)

openai_retry_policy = dict(
    retry=retry_if_exception_type(OPENAI_TRANSIENT_ERRORS),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=30) + wait_random(0, 1),
    reraise=True,
)
