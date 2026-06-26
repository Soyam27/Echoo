import re
import uuid
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.models.models import Comment
from app.services.embedding_service import embed_text

# Structural filter keywords — these queries need all comments, not vector search
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


_LISTING_SIGNALS = {"list", "show", "get", "fetch", "find", "filter", "display", "give", "which", "all"}

_ANALYSIS_SIGNALS = {
    "what do", "how do", "summarize", "summary", "analyze", "analysis",
    "sentiment", "opinion", "theme", "pattern", "breakdown", "overall", "think",
    "feel", "insight", "understand", "explain",
}


def _classify_intent(question: str) -> str:
    """Return 'listing' or 'analysis'."""
    q = question.lower()
    if set(q.split()) & _LISTING_SIGNALS:
        return "listing"
    if any(p in q for p in _ANALYSIS_SIGNALS):
        return "analysis"
    return "listing"  # default: assume retrieval


def _is_structural_query(question: str) -> bool:
    q = question.lower()
    return any(kw in q for kw in _STRUCTURAL_KEYWORDS)


def _prefilter_comments(question: str, comments: list[Comment]) -> list[Comment]:
    """Client-side filter for properties vector search can't find."""
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


_SYSTEM_PROMPT = """You are Echoo, an AI that helps creators understand their Instagram comments.

Each comment is prefixed with [N] — its index number.

Decide the response type based on the question:

LISTING (show, find, get, list, which comments, filter):
- Return ONLY a markdown list of matching comments: `- @username: comment text`
- No intro sentence, no summary, no extra words.

ANALYSIS (what do people think, sentiment, themes, opinions, summary, breakdown):
- Write 2-3 focused paragraphs.
- DO NOT list individual comments. Describe patterns and quote short phrases inline as evidence.

After your response, on a NEW LINE output exactly:
USED:[comma-separated indices of every comment you referenced, e.g. USED:0,3,7]

Rules: Be direct. Only use comments provided. Never invent comments or usernames."""


async def _search_comments(
    query_embedding: list[float],
    post_ids: list[uuid.UUID],
    db: AsyncSession,
    limit: int = 20,
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
    result = await db.execute(
        select(Comment).where(Comment.post_id.in_(post_ids))
    )
    return list(result.scalars().all())


async def answer_question(
    question: str,
    post_ids: list[uuid.UUID],
    db: AsyncSession,
) -> tuple[str, list[Comment]]:
    intent = _classify_intent(question)

    # ── Path 1: structural listing (emoji, hashtag, etc.) ──────────────────────
    # Zero LLM cost — Python filters all comments directly.
    if _is_structural_query(question) and intent == "listing":
        all_comments = await _fetch_all_comments(post_ids, db)
        comments = _prefilter_comments(question, all_comments)
        if not comments:
            return "No comments matched that filter.", []
        answer = "\n".join(f"- @{c.username}: {c.text}" for c in comments[:200])
        return answer, comments[:200]

    # ── Path 2 & 3: LLM pipeline ──────────────────────────────────────────────────
    if _is_structural_query(question):
        all_comments = await _fetch_all_comments(post_ids, db)
        comments = _prefilter_comments(question, all_comments)[:100]
    elif intent == "listing":
        # Listing needs full coverage — fetch all, cap at 300 so LLM context stays safe.
        # Vector search pre-filtering would silently miss valid matches.
        comments = await _fetch_all_comments(post_ids, db)
        comments = comments[:300]
    else:
        # Analysis only needs a representative sample — vector search is fine.
        query_embedding = await embed_text(question)
        comments = await _search_comments(query_embedding, post_ids, db, limit=20)

    if not comments:
        return "No synced comments found for the selected posts. Please sync comments first.", []

    context = "\n".join(f"[{i}] @{c.username}: {c.text[:200]}" for i, c in enumerate(comments))

    response = await _client.chat.completions.create(
        model=settings.azure_chat_deployment,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"Comments:\n{context}\n\nQuestion: {question}"},
        ],
        max_tokens=2000,
        temperature=0.3,
    )

    raw = response.choices[0].message.content.strip()

    used_indices: set[int] = set()
    answer = raw
    if "\nUSED:" in raw:
        answer_part, used_part = raw.rsplit("\nUSED:", 1)
        answer = answer_part.strip()
        try:
            used_indices = {int(x.strip()) for x in used_part.split(",") if x.strip().isdigit()}
        except ValueError:
            pass

    source_comments = [comments[i] for i in sorted(used_indices) if i < len(comments)]
    if not source_comments:
        source_comments = comments

    return answer, source_comments
