import asyncio
import re
import uuid
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import defer

from app.config import settings
from app.models.models import Comment
from app.services.embedding_service import embed_text

_STRUCTURAL_KEYWORDS = {
    "emoji", "emojis", "hashtag", "hashtags", "link", "url",
    "mention", "question", "exclamation", "uppercase", "capital",
    "number", "phone", "email", "longest", "shortest",
}

_EMOJI_RE = re.compile(
    r"[\U00010000-\U0010FFFF"
    r"\U0001F300-\U0001F9FF"
    r"\U00002702-\U000027B0"
    r"\U0001F000-\U0001F02F"
    r"☀-⛿✀-➿]",
    flags=re.UNICODE,
)


def _is_structural_query(question: str) -> bool:
    q = question.lower()
    return any(kw in q for kw in _STRUCTURAL_KEYWORDS)


def _prefilter_comments(question: str, comments: list[Comment]) -> list[Comment]:
    q = question.lower()
    if "emoji" in q or "emojis" in q:
        return [c for c in comments if _EMOJI_RE.search(c.text)]
    if "hashtag" in q:
        return [c for c in comments if "#" in c.text]
    if "mention" in q:
        return [c for c in comments if "@" in c.text]
    if "question" in q:
        return [c for c in comments if "?" in c.text]
    if "link" in q or "url" in q:
        return [c for c in comments if "http" in c.text or "www." in c.text]
    return comments


_client = AsyncOpenAI(
    base_url=settings.azure_openai_endpoint,
    api_key=settings.azure_openai_api_key,
)

_LISTING_PROMPT = """You are filtering Instagram comments.
Each comment is prefixed with [N] — its index number.

Output ONLY one line — the indices of comments that match the request:
USED:[comma-separated indices, e.g. USED:0,3,7]

If none match, output: USED:
Nothing else. No explanation. No list. Just the USED line."""

_ANALYSIS_PROMPT = """You are Echoo, an AI that helps creators understand their Instagram comments.
Each comment is prefixed with [N] — its index number.

The user wants ANALYSIS or SUMMARY. Write 2-3 focused paragraphs.
DO NOT list individual comments. Describe patterns and quote short phrases inline as evidence.

After your response, on a NEW LINE output exactly:
USED:[comma-separated indices of comments you referenced, e.g. USED:0,3,7]

Be direct. Only use comments provided. Never invent comments or usernames."""


async def _search_comments(
    query_embedding: list[float],
    post_ids: list[uuid.UUID],
    db: AsyncSession,
    limit: int = 30,
) -> list[Comment]:
    result = await db.execute(
        select(Comment)
        .where(Comment.post_id.in_(post_ids))
        .where(Comment.embedding.isnot(None))
        .order_by(Comment.embedding.cosine_distance(query_embedding))
        .limit(limit)
    )
    return list(result.scalars().all())


async def _fetch_all_comments(post_ids: list[uuid.UUID], db: AsyncSession) -> list[Comment]:
    # Defer embedding column — 1536 floats per row, not needed for listing
    result = await db.execute(
        select(Comment)
        .where(Comment.post_id.in_(post_ids))
        .options(defer(Comment.embedding))
    )
    return list(result.scalars().all())


def _parse_used(raw: str) -> tuple[str, set[int]]:
    """Split LLM output into (answer, used_indices)."""
    used_indices: set[int] = set()
    answer = raw
    if "USED:" in raw:
        answer_part, used_part = raw.rsplit("USED:", 1)
        answer = answer_part.strip()
        used_part = used_part.strip().split("\n")[0]  # only first line after USED:
        try:
            used_indices = {int(x.strip()) for x in used_part.split(",") if x.strip().isdigit()}
        except ValueError:
            pass
    return answer, used_indices


async def answer_question(
    question: str,
    post_ids: list[uuid.UUID],
    db: AsyncSession,
    mode: str = "listing",
    history: list[dict] | None = None,
) -> tuple[str, list[Comment]]:

    # ── LISTING mode ───────────────────────────────────────────────────────────
    if mode == "listing":

        # Structural filters — pure Python, zero LLM cost, zero answer text
        if _is_structural_query(question):
            all_comments = await _fetch_all_comments(post_ids, db)
            comments = _prefilter_comments(question, all_comments)
            return "", comments[:200]

        # Semantic listing — fetch all, LLM classifies (outputs only USED: line)
        comments = await _fetch_all_comments(post_ids, db)
        comments = comments[:2000]
        if not comments:
            return "", []

        context = "\n".join(f"[{i}] @{c.username}: {c.text[:200]}" for i, c in enumerate(comments))
        response = await _client.chat.completions.create(
            model=settings.azure_chat_deployment,
            messages=[
                {"role": "system", "content": _LISTING_PROMPT},
                {"role": "user", "content": f"Comments:\n{context}\n\nFilter request: {question}"},
            ],
            max_tokens=5000,  # worst case: 2000 indices × ~2 tokens each
            temperature=0.0,
        )
        raw = response.choices[0].message.content.strip()
        _, used_indices = _parse_used(raw)
        matched = [comments[i] for i in sorted(used_indices) if i < len(comments)]
        return "", matched

    # ── ANALYSIS mode ──────────────────────────────────────────────────────────
    # Parallelize: embed question + fetch random sample simultaneously
    query_embedding, rand_result = await asyncio.gather(
        embed_text(question),
        db.execute(
            select(Comment)
            .where(Comment.post_id.in_(post_ids))
            .order_by(func.random())
            .limit(20)
        ),
    )
    random_sample = list(rand_result.scalars().all())
    relevant = await _search_comments(query_embedding, post_ids, db, limit=30)

    seen = {c.id for c in relevant}
    comments = relevant + [c for c in random_sample if c.id not in seen]
    if not comments:
        return "No synced comments found. Please sync comments first.", []

    context = "\n".join(f"[{i}] @{c.username}: {c.text[:200]}" for i, c in enumerate(comments))
    messages = [{"role": "system", "content": _ANALYSIS_PROMPT}]
    if history:
        messages.extend(history[-6:])
    messages.append({"role": "user", "content": f"Comments:\n{context}\n\nQuestion: {question}"})

    response = await _client.chat.completions.create(
        model=settings.azure_chat_deployment,
        messages=messages,
        max_tokens=800,
        temperature=0.3,
    )
    raw = response.choices[0].message.content.strip()
    answer, used_indices = _parse_used(raw)
    source_comments = [comments[i] for i in sorted(used_indices) if i < len(comments)]
    if not source_comments:
        source_comments = comments

    return answer, source_comments
