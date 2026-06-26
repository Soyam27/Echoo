from openai import AsyncOpenAI
from app.config import settings

_client = AsyncOpenAI(
    base_url=settings.azure_openai_endpoint,
    api_key=settings.azure_openai_api_key,
)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    embeddings: list[list[float]] = []
    # Azure allows up to 2048 inputs per call; batch at 100 to stay safe
    for i in range(0, len(texts), 100):
        batch = texts[i : i + 100]
        response = await _client.embeddings.create(
            input=batch,
            model=settings.azure_embedding_deployment,
        )
        embeddings.extend(item.embedding for item in response.data)

    return embeddings


async def embed_text(text: str) -> list[float]:
    results = await embed_texts([text])
    return results[0]
