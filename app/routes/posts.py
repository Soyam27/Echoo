import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db, AsyncSessionLocal
from app.schemas.schemas import PostResponse, SyncRequest, SyncStatusResponse
from app.models.models import User, Post, Comment, SyncStatus
from app.core.deps import get_current_user
from app.services.instagram_service import fetch_user_posts, fetch_post_comments
from app.services.embedding_service import embed_texts

router = APIRouter(prefix="/posts", tags=["posts"])


@router.get("", response_model=list[PostResponse])
async def get_posts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not current_user.instagram_access_token or not current_user.instagram_id:
        return []

    raw_posts = await fetch_user_posts(current_user.instagram_access_token, current_user.instagram_id)

    posts = []
    for raw in raw_posts:
        result = await db.execute(select(Post).where(Post.instagram_post_id == raw["id"]))
        post = result.scalar_one_or_none()

        if not post:
            post = Post(
                id=uuid.uuid4(),
                user_id=current_user.id,
                instagram_post_id=raw["id"],
                caption=raw.get("caption"),
                media_url=raw.get("media_url"),
                media_type=raw.get("media_type", "IMAGE"),
                permalink=raw.get("permalink", ""),
                posted_at=datetime.fromisoformat(raw["timestamp"].replace("Z", "+00:00")),
            )
            db.add(post)

        posts.append(post)

    await db.commit()
    return posts


@router.post("/sync")
async def sync_posts(
    request: SyncRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify all requested posts belong to this user
    result = await db.execute(
        select(Post).where(
            Post.instagram_post_id.in_(request.post_ids),
            Post.user_id == current_user.id,
        )
    )
    posts = result.scalars().all()

    if not posts:
        raise HTTPException(status_code=404, detail="No matching posts found")

    background_tasks.add_task(
        _sync_comments_task,
        [p.instagram_post_id for p in posts],
        current_user.instagram_access_token,
    )

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
            post_id=p.instagram_post_id,
            sync_status=p.sync_status.value,
            comment_count=p.comment_count,
            synced_at=p.synced_at,
        )
        for p in posts
    ]


async def _sync_comments_task(instagram_post_ids: list[str], access_token: str) -> None:
    """Background task — uses its own DB session, independent of the request session."""
    async with AsyncSessionLocal() as db:
        for ig_post_id in instagram_post_ids:
            result = await db.execute(select(Post).where(Post.instagram_post_id == ig_post_id))
            post = result.scalar_one_or_none()
            if not post:
                continue

            post.sync_status = SyncStatus.syncing
            await db.commit()

            try:
                raw_comments = await fetch_post_comments(ig_post_id, access_token)

                # Only embed comments not already in DB
                existing_ids_result = await db.execute(
                    select(Comment.instagram_comment_id).where(Comment.post_id == post.id)
                )
                existing_ids = {row[0] for row in existing_ids_result.fetchall()}

                new_raw = [c for c in raw_comments if c["id"] not in existing_ids]

                if new_raw:
                    embeddings = await embed_texts([c["text"] for c in new_raw])
                    for raw, embedding in zip(new_raw, embeddings):
                        db.add(Comment(
                            id=uuid.uuid4(),
                            post_id=post.id,
                            instagram_comment_id=raw["id"],
                            username=raw.get("username", "unknown"),
                            text=raw["text"],
                            posted_at=datetime.fromisoformat(raw["timestamp"].replace("Z", "+00:00")),
                            embedding=embedding,
                        ))

                post.sync_status = SyncStatus.completed
                post.comment_count = len(raw_comments)
                post.synced_at = datetime.utcnow()

            except Exception as e:
                import traceback
                print(f"[sync error] post={ig_post_id} error={e}")
                traceback.print_exc()
                post.sync_status = SyncStatus.failed

            await db.commit()
