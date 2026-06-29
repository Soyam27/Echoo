import asyncio
import time
import traceback
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from app.database import get_db, AsyncSessionLocal
from app.schemas.schemas import PostResponse, SyncRequest, SyncStatusResponse
from app.models.models import User, Post, Comment, SyncStatus, ConnectedAccount
from app.core.deps import get_current_user
from app.services.instagram_service import fetch_user_posts, fetch_post_comments
from app.services.youtube_service import (
    fetch_channel_videos,
    stream_video_comments,
    refresh_access_token,
    token_expires_at,
)
from app.services.embedding_service import embed_texts

router = APIRouter(prefix="/posts", tags=["posts"])

_SYNC_SEMAPHORE = asyncio.Semaphore(4)


@router.get("", response_model=list[PostResponse])
async def get_posts(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Always read from DB first — never block on platform API calls.
    result = await db.execute(
        select(Post)
        .where(Post.user_id == current_user.id, Post.is_external == False)
        .order_by(Post.posted_at.desc())
    )
    existing = list(result.scalars().all())

    if existing:
        # Returning user: refresh post list in background, respond instantly.
        background_tasks.add_task(_discover_posts_bg, current_user.id)
        return existing

    # First-time user: block inline so they see their posts immediately.
    return await _discover_posts_inline(current_user.id, db)


async def _discover_posts_inline(user_id: uuid.UUID, db: AsyncSession) -> list[Post]:
    """Call platform APIs and upsert new posts. Returns the full post list."""
    accounts_result = await db.execute(
        select(ConnectedAccount).where(ConnectedAccount.user_id == user_id)
    )
    accounts = accounts_result.scalars().all()
    all_posts: list[Post] = []

    for account in accounts:
        if account.platform == "instagram" and account.instagram_id and account.access_token:
            raw_ig = await fetch_user_posts(account.access_token, account.instagram_id)
            if raw_ig:
                ig_ids = [r["id"] for r in raw_ig]
                existing_result = await db.execute(
                    select(Post).where(Post.instagram_post_id.in_(ig_ids), Post.user_id == user_id)
                )
                existing_ig = {p.instagram_post_id: p for p in existing_result.scalars().all()}
                for raw in raw_ig:
                    post = existing_ig.get(raw["id"])
                    if not post:
                        post = Post(
                            id=uuid.uuid4(), user_id=user_id,
                            connected_account_id=account.id, platform="instagram",
                            instagram_post_id=raw["id"], caption=raw.get("caption"),
                            media_url=raw.get("media_url"),
                            media_type=raw.get("media_type", "IMAGE"),
                            permalink=raw.get("permalink", ""),
                            posted_at=datetime.fromisoformat(raw["timestamp"].replace("Z", "+00:00")),
                        )
                        db.add(post)
                    elif not post.connected_account_id:
                        post.connected_account_id = account.id
                    all_posts.append(post)

        elif account.platform == "youtube" and account.youtube_uploads_playlist_id and account.access_token:
            access_token = await _ensure_youtube_token_for_account(account, db)
            raw_yt = await fetch_channel_videos(access_token, account.youtube_uploads_playlist_id)
            if raw_yt:
                yt_ids = [r["id"] for r in raw_yt]
                existing_result = await db.execute(
                    select(Post).where(Post.youtube_video_id.in_(yt_ids), Post.user_id == user_id)
                )
                existing_yt = {p.youtube_video_id: p for p in existing_result.scalars().all()}
                for raw in raw_yt:
                    post = existing_yt.get(raw["id"])
                    if not post:
                        published = raw.get("published_at", "")
                        try:
                            posted_at = datetime.fromisoformat(published.replace("Z", "+00:00"))
                        except (ValueError, AttributeError):
                            posted_at = datetime.utcnow()
                        post = Post(
                            id=uuid.uuid4(), user_id=user_id,
                            connected_account_id=account.id, platform="youtube",
                            youtube_video_id=raw["id"], caption=raw.get("title"),
                            media_url=raw.get("thumbnail"), media_type="VIDEO",
                            permalink=raw.get("permalink", f"https://www.youtube.com/watch?v={raw['id']}"),
                            posted_at=posted_at,
                        )
                        db.add(post)
                    elif not post.connected_account_id:
                        post.connected_account_id = account.id
                    all_posts.append(post)

    if all_posts:
        await db.commit()
    return all_posts


async def _discover_posts_bg(user_id: uuid.UUID) -> None:
    """Background wrapper: discover new posts without blocking GET /posts."""
    try:
        async with AsyncSessionLocal() as db:
            await _discover_posts_inline(user_id, db)
    except Exception as e:
        print(f"[posts_bg] discovery error for user={user_id}: {e}")


@router.post("/sync")
async def sync_posts(
    request: SyncRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        db_uuids = [uuid.UUID(pid) for pid in request.post_ids]
    except ValueError:
        raise HTTPException(status_code=422, detail="post_ids must be valid UUIDs")

    result = await db.execute(
        select(Post).where(
            Post.id.in_(db_uuids),
            Post.user_id == current_user.id,
        )
    )
    posts = result.scalars().all()
    if not posts:
        raise HTTPException(status_code=404, detail="No matching posts found")

    background_tasks.add_task(_sync_comments_task, [p.id for p in posts])

    return {"message": "Sync started", "post_count": len(posts)}


@router.get("/sync-status", response_model=list[SyncStatusResponse])
async def sync_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Post).where(Post.user_id == current_user.id))
    posts = result.scalars().all()
    return [
        SyncStatusResponse(
            post_id=str(p.id),
            platform=p.platform,
            sync_status=p.sync_status.value,
            comment_count=p.comment_count,
            synced_at=p.synced_at,
        )
        for p in posts
    ]


async def _ensure_youtube_token_for_account(account: ConnectedAccount, db: AsyncSession) -> str:
    """Refresh YouTube access token on the ConnectedAccount if expired; returns valid token."""
    if account.token_expires_at and datetime.utcnow() >= account.token_expires_at:
        if not account.refresh_token:
            raise HTTPException(status_code=400, detail="YouTube token expired and no refresh token available")
        token_data = await refresh_access_token(account.refresh_token)
        account.access_token = token_data["access_token"]
        account.token_expires_at = token_expires_at(token_data.get("expires_in", 3600))
        await db.commit()
    return account.access_token


async def _sync_one_post(post_id: uuid.UUID) -> None:
    async with _SYNC_SEMAPHORE:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Post).where(Post.id == post_id)
                .options(
                    selectinload(Post.connected_account),
                    selectinload(Post.user),
                )
            )
            post = result.scalar_one_or_none()
            if not post:
                return

            post.sync_status = SyncStatus.syncing
            await db.commit()

            platform = post.platform  # read before any potential session poison
            try:
                if platform == "instagram":
                    await _sync_instagram_post(post, db)
                else:
                    await _sync_youtube_post(post, db)
            except Exception as e:
                print(f"[sync error] post={post_id} platform={platform} error={e}")
                traceback.print_exc()
                # Session may be poisoned after a DB error — rollback first, then
                # re-fetch the post in a clean state before updating status.
                try:
                    await db.rollback()
                    r2 = await db.execute(select(Post).where(Post.id == post_id))
                    failed_post = r2.scalar_one_or_none()
                    if failed_post:
                        failed_post.sync_status = SyncStatus.failed
                        await db.commit()
                except Exception as inner:
                    print(f"[sync error] could not mark post={post_id} as failed: {inner}")


async def _sync_instagram_post(post: Post, db: AsyncSession) -> None:
    if post.connected_account:
        access_token = post.connected_account.access_token
    else:
        # Legacy fallback for posts without a connected_account link
        access_token = post.user.instagram_access_token
    raw_comments = await fetch_post_comments(post.instagram_post_id, access_token)
    await _upsert_comments(post, db, raw_comments, platform="instagram",
                           id_key="id", text_key="text", user_key="username", time_key="timestamp",
                           comment_id_field="instagram_comment_id")


async def _sync_youtube_post(post: Post, db: AsyncSession) -> None:
    if post.connected_account:
        account = post.connected_account
        if account.token_expires_at and datetime.utcnow() >= account.token_expires_at:
            token_data = await refresh_access_token(account.refresh_token)
            async with AsyncSessionLocal() as token_db:
                result = await token_db.execute(
                    select(ConnectedAccount).where(ConnectedAccount.id == account.id)
                )
                db_account = result.scalar_one_or_none()
                if db_account:
                    db_account.access_token = token_data["access_token"]
                    db_account.token_expires_at = token_expires_at(token_data.get("expires_in", 3600))
                    await token_db.commit()
            account.access_token = token_data["access_token"]
        access_token = account.access_token
    else:
        user = post.user
        if user.youtube_token_expires_at and datetime.utcnow() >= user.youtube_token_expires_at:
            token_data = await refresh_access_token(user.youtube_refresh_token)
            async with AsyncSessionLocal() as token_db:
                result = await token_db.execute(select(User).where(User.id == user.id))
                db_user = result.scalar_one_or_none()
                if db_user:
                    db_user.youtube_access_token = token_data["access_token"]
                    db_user.youtube_token_expires_at = token_expires_at(token_data.get("expires_in", 3600))
                    await token_db.commit()
            user.youtube_access_token = token_data["access_token"]
        access_token = user.youtube_access_token

    # Load existing IDs once so every chunk skips already-stored comments.
    existing_result = await db.execute(
        select(Comment.youtube_comment_id).where(Comment.post_id == post.id)
    )
    existing_ids: set[str] = {row[0] for row in existing_result.fetchall() if row[0]}
    seen: set[str] = set()

    # Producer/consumer pipeline: fetch YouTube pages and embed/insert in parallel.
    # While the consumer is awaiting the Azure embedding API, the producer continues
    # fetching the next YouTube pages — true I/O overlap via asyncio.gather.
    queue: asyncio.Queue[list[dict] | None] = asyncio.Queue(maxsize=3)

    t_sync_start = time.perf_counter()
    chunk_n = 0

    async def _producer() -> None:
        nonlocal chunk_n
        try:
            async for chunk in stream_video_comments(post.youtube_video_id, access_token):
                chunk_n += 1
                print(f"[yt_sync] producer: chunk {chunk_n} ready ({len(chunk)} comments, t={time.perf_counter()-t_sync_start:.1f}s)")
                await queue.put(chunk)
        finally:
            await queue.put(None)

    async def _consumer() -> None:
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            await _upsert_chunk(post, db, chunk, existing_ids, seen, t_sync_start)

    await asyncio.gather(_producer(), _consumer())

    count_result = await db.execute(select(func.count()).where(Comment.post_id == post.id))
    post.comment_count = count_result.scalar()
    post.sync_status = SyncStatus.completed
    post.synced_at = datetime.utcnow()
    await db.commit()


async def _upsert_chunk(
    post: Post,
    db: AsyncSession,
    raw_comments: list[dict],
    existing_ids: set[str],
    seen: set[str],
    t0: float = 0.0,
) -> None:
    """Embed and insert one streaming chunk of YouTube comments."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    new_raw: list[dict] = []
    for c in raw_comments:
        cid = c["id"]
        if cid not in existing_ids and cid not in seen:
            seen.add(cid)
            new_raw.append(c)

    if not new_raw:
        print(f"[yt_sync] chunk skipped (all duplicates), t={time.perf_counter()-t0:.1f}s")
        return

    t_embed = time.perf_counter()
    embeddings = await embed_texts([c["text"] for c in new_raw])
    print(f"[yt_sync] embed {len(new_raw)} comments: {time.perf_counter()-t_embed:.1f}s")

    rows = []
    for raw, embedding in zip(new_raw, embeddings):
        try:
            posted_at = datetime.fromisoformat(raw.get("published_at", "").replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            posted_at = datetime.utcnow()
        rows.append({
            "id": uuid.uuid4(),
            "post_id": post.id,
            "platform": "youtube",
            "instagram_comment_id": None,
            "youtube_comment_id": raw["id"],
            "username": raw.get("username", "unknown"),
            "text": raw["text"],
            "posted_at": posted_at,
            "embedding": embedding,
            "created_at": datetime.utcnow(),
        })

    t_insert = time.perf_counter()
    for i in range(0, len(rows), 150):
        stmt = pg_insert(Comment).values(rows[i : i + 150]).on_conflict_do_nothing()
        await db.execute(stmt)
    t_commit = time.perf_counter()
    await db.commit()
    print(f"[yt_sync] insert {len(rows)} rows: {t_commit-t_insert:.1f}s | commit: {time.perf_counter()-t_commit:.1f}s | total t={time.perf_counter()-t0:.1f}s")


async def _upsert_comments(
    post: Post,
    db: AsyncSession,
    raw_comments: list[dict],
    *,
    platform: str,
    id_key: str,
    text_key: str,
    user_key: str,
    time_key: str,
    comment_id_field: str,
) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    if comment_id_field == "instagram_comment_id":
        existing_result = await db.execute(
            select(Comment.instagram_comment_id).where(Comment.post_id == post.id)
        )
    else:
        existing_result = await db.execute(
            select(Comment.youtube_comment_id).where(Comment.post_id == post.id)
        )
    existing_ids = {row[0] for row in existing_result.fetchall() if row[0]}

    # Deduplicate within the batch too — YouTube API returns the same comment ID
    # on multiple pages when new comments arrive during pagination of live videos.
    seen: set[str] = set()
    new_raw: list[dict] = []
    for c in raw_comments:
        cid = c[id_key]
        if cid not in existing_ids and cid not in seen:
            seen.add(cid)
            new_raw.append(c)

    if new_raw:
        embeddings = await embed_texts([c[text_key] for c in new_raw])
        rows = []
        for raw, embedding in zip(new_raw, embeddings):
            ts_str = raw.get(time_key, "")
            try:
                posted_at = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                posted_at = datetime.utcnow()

            row = {
                "id": uuid.uuid4(),
                "post_id": post.id,
                "platform": platform,
                "instagram_comment_id": None,
                "youtube_comment_id": None,
                "username": raw.get(user_key, "unknown"),
                "text": raw[text_key],
                "posted_at": posted_at,
                "embedding": embedding,
                "created_at": datetime.utcnow(),
            }
            row[comment_id_field] = raw[id_key]
            rows.append(row)

        for i in range(0, len(rows), 150):
            stmt = pg_insert(Comment).values(rows[i : i + 150]).on_conflict_do_nothing()
            await db.execute(stmt)

    count_result = await db.execute(
        select(func.count()).where(Comment.post_id == post.id)
    )
    post.comment_count = count_result.scalar()
    post.sync_status = SyncStatus.completed
    post.synced_at = datetime.utcnow()
    await db.commit()


async def _sync_comments_task(post_ids: list[uuid.UUID]) -> None:
    await asyncio.gather(*[_sync_one_post(pid) for pid in post_ids])
