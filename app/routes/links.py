import re
import uuid
import httpx
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.database import get_db
from app.models.models import Post, SyncStatus, User, ConnectedAccount
from app.core.deps import get_current_user
from app.routes.posts import _sync_comments_task

router = APIRouter(prefix="/links", tags=["links"])


class AnalyzeLinkRequest(BaseModel):
    url: str


def _extract_youtube_video_id(url: str) -> str | None:
    patterns = [
        r'youtube\.com/watch\?.*v=([a-zA-Z0-9_-]{11})',
        r'youtu\.be/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def _extract_instagram_shortcode(url: str) -> str | None:
    patterns = [
        r'instagram\.com/p/([A-Za-z0-9_-]+)',
        r'instagram\.com/reel/([A-Za-z0-9_-]+)',
        r'instagram\.com/tv/([A-Za-z0-9_-]+)',
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


@router.post("/analyze")
async def analyze_link(
    request: AnalyzeLinkRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    url = request.url.strip()

    # ── Instagram ──────────────────────────────────────────────────────────────
    shortcode = _extract_instagram_shortcode(url)
    if shortcode:
        ig_result = await db.execute(
            select(ConnectedAccount).where(
                ConnectedAccount.user_id == current_user.id,
                ConnectedAccount.platform == "instagram",
            ).limit(1)
        )
        ig_account = ig_result.scalar_one_or_none()
        if not ig_account:
            raise HTTPException(
                status_code=400,
                detail="Connect an Instagram account first to analyze Instagram links",
            )

        post_result = await db.execute(
            select(Post).where(
                Post.user_id == current_user.id,
                Post.platform == "instagram",
                Post.permalink.contains(shortcode),
            )
        )
        post = post_result.scalar_one_or_none()
        if not post:
            raise HTTPException(
                status_code=400,
                detail="This Instagram post was not found in your connected account. Only your own posts are supported.",
            )

        if post.sync_status in (SyncStatus.pending, SyncStatus.failed):
            post.sync_status = SyncStatus.pending
            await db.commit()
            background_tasks.add_task(_sync_comments_task, [post.id])

        return {
            "post_id": str(post.id),
            "sync_status": post.sync_status.value,
            "already_existed": True,
        }

    # ── YouTube ────────────────────────────────────────────────────────────────
    video_id = _extract_youtube_video_id(url)
    if not video_id:
        raise HTTPException(
            status_code=400,
            detail="Unsupported URL. Paste a YouTube (youtube.com/watch?v=...) or Instagram (instagram.com/p/...) link.",
        )

    # Need a connected YouTube account for the OAuth token — query directly to avoid async lazy-load
    yt_result = await db.execute(
        select(ConnectedAccount).where(
            ConnectedAccount.user_id == current_user.id,
            ConnectedAccount.platform == "youtube",
        ).limit(1)
    )
    yt_account = yt_result.scalar_one_or_none()
    if not yt_account:
        raise HTTPException(
            status_code=400,
            detail="Connect a YouTube account first to analyze YouTube links",
        )

    # Reuse existing post if already created for this user
    existing = await db.execute(
        select(Post).where(
            Post.user_id == current_user.id,
            Post.youtube_video_id == video_id,
        )
    )
    post = existing.scalar_one_or_none()
    if post:
        # Re-trigger sync if it previously failed or is stuck pending
        if post.sync_status in (SyncStatus.failed, SyncStatus.pending):
            post.sync_status = SyncStatus.pending
            await db.commit()
            background_tasks.add_task(_sync_comments_task, [post.id])
        return {
            "post_id": str(post.id),
            "sync_status": post.sync_status.value,
            "already_existed": True,
        }

    # Fetch video title + thumbnail via YouTube API
    title, thumbnail = f"YouTube video ({video_id})", None
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={
                    "part": "snippet",
                    "id": video_id,
                    "access_token": yt_account.access_token,
                },
            )
            if r.status_code == 200:
                items = r.json().get("items", [])
                if items:
                    snip = items[0]["snippet"]
                    title = snip.get("title", title)
                    thumbnail = (
                        (snip.get("thumbnails", {}).get("medium") or {}).get("url")
                        or (snip.get("thumbnails", {}).get("default") or {}).get("url")
                    )
    except Exception:
        pass

    post = Post(
        id=uuid.uuid4(),
        user_id=current_user.id,
        connected_account_id=yt_account.id,
        platform="youtube",
        youtube_video_id=video_id,
        caption=title,
        media_url=thumbnail,
        media_type="VIDEO",
        permalink=f"https://www.youtube.com/watch?v={video_id}",
        posted_at=datetime.utcnow(),
        sync_status=SyncStatus.pending,
        comment_count=0,
        is_external=True,
    )
    db.add(post)
    await db.commit()

    background_tasks.add_task(_sync_comments_task, [post.id])

    return {
        "post_id": str(post.id),
        "sync_status": "pending",
        "already_existed": False,
    }


@router.get("/analyzed")
async def list_analyzed(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import true as sa_true
    result = await db.execute(
        select(Post)
        .where(Post.user_id == current_user.id, Post.is_external == sa_true())
        .order_by(Post.created_at.desc())
    )
    posts = result.scalars().all()
    return [
        {
            "id": str(p.id),
            "platform": p.platform,
            "instagram_post_id": p.instagram_post_id,
            "youtube_video_id": p.youtube_video_id,
            "caption": p.caption,
            "media_url": p.media_url,
            "media_type": p.media_type,
            "permalink": p.permalink,
            "posted_at": p.posted_at.isoformat(),
            "sync_status": p.sync_status.value,
            "comment_count": p.comment_count,
            "synced_at": p.synced_at.isoformat() if p.synced_at else None,
            "connected_account_id": str(p.connected_account_id) if p.connected_account_id else None,
            "is_external": True,
        }
        for p in posts
    ]


@router.get("/status/{post_id}")
async def link_status(
    post_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Post).where(Post.id == post_id, Post.user_id == current_user.id)
    )
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return {
        "post_id": str(post.id),
        "id": str(post.id),
        "platform": post.platform,
        "youtube_video_id": post.youtube_video_id,
        "caption": post.caption,
        "media_url": post.media_url,
        "sync_status": post.sync_status.value,
        "comment_count": post.comment_count,
        "connected_account_id": str(post.connected_account_id) if post.connected_account_id else None,
        "is_external": True,
    }
