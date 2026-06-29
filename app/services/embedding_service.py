import asyncio

from openai import AsyncOpenAI
from app.config import settings

_client = AsyncOpenAI(
    base_url=settings.azure_openai_endpoint,
    api_key=settings.azure_openai_api_key,
    max_retries=3,          # auto-retry on 429 / transient errors with backoff
    timeout=60.0,           # large batches can take a few extra seconds
)

# Pure I/O — these are HTTP calls to Azure, not CPU work.
# 10 concurrent batches is safe on any deployment size.
_EMBED_SEMAPHORE = asyncio.Semaphore(10)

# Azure text-embedding-3-small supports up to 2048 inputs per request.
# 2048 per batch: 4000 comments → 2 API calls in parallel instead of 8.
_BATCH_SIZE = 2048

# text-embedding-3-small hard limit is 8192 tokens.
# 8000 chars is a conservative safe cutoff (1 char ≈ 1 token worst-case for CJK).
_MAX_CHARS = 8000


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    texts = [t[:_MAX_CHARS] for t in texts]

    async def _embed_batch(batch: list[str]) -> list[list[float]]:
        async with _EMBED_SEMAPHORE:
            response = await _client.embeddings.create(
                input=batch,
                model=settings.azure_embedding_deployment,
            )
            return [item.embedding for item in response.data]

    tasks = [_embed_batch(texts[i : i + _BATCH_SIZE]) for i in range(0, len(texts), _BATCH_SIZE)]
    results = await asyncio.gather(*tasks)
    return [emb for batch_result in results for emb in batch_result]


async def embed_text(text: str) -> list[float]:
    results = await embed_texts([text])
    return results[0]
