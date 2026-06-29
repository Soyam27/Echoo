import uuid as uuid_lib
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.models import User, Comment, Post, ConnectedAccount
from app.core.deps import get_current_user
from app.services.youtube_service import (
    reply_to_youtube_comment,
    refresh_access_token,
    token_expires_at,
)

router = APIRouter(prefix="/comments", tags=["comments"])


@router.get("")
async def list_comments(
    post_ids: str = Query(..., description="Comma-separated post UUIDs"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        ids = [uuid_lib.UUID(pid.strip()) for pid in post_ids.split(",") if pid.strip()]
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid post ID format")

    result = await db.execute(
        select(Comment)
        .join(Post, Comment.post_id == Post.id)
        .where(Post.id.in_(ids), Post.user_id == current_user.id)
        .order_by(Comment.posted_at.desc())
        .limit(500)
    )
    comments = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "platform": c.platform,
            "external_comment_id": c.instagram_comment_id or c.youtube_comment_id or "",
            "username": c.username,
            "text": c.text,
            "posted_at": c.posted_at.isoformat(),
        }
        for c in comments
    ]


_GRAPH_URL = "https://graph.instagram.com"


class ReplyRequest(BaseModel):
    message: str


@router.post("/{comment_id}/reply")
async def reply_to_comment(
    comment_id: str,
    request: ReplyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Look up comment by either platform ID
    result = await db.execute(
        select(Comment)
        .join(Post, Comment.post_id == Post.id)
        .where(
            or_(
                Comment.instagram_comment_id == comment_id,
                Comment.youtube_comment_id == comment_id,
            ),
            Post.user_id == current_user.id,
        )
    )
    comment = result.scalar_one_or_none()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    # Load the post's connected account for credentials
    post_result = await db.execute(
        select(Post).where(Post.id == comment.post_id)
        .options(selectinload(Post.connected_account))
    )
    post = post_result.scalar_one()

    if comment.platform == "instagram":
        if post.connected_account:
            access_token = post.connected_account.access_token
        else:
            access_token = current_user.instagram_access_token
        if not access_token:
            raise HTTPException(status_code=400, detail="Instagram account not connected")

        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{_GRAPH_URL}/{comment_id}/replies",
                params={
                    "message": request.message,
                    "access_token": access_token,
                },
            )
        if r.status_code != 200:
            raise HTTPException(
                status_code=r.status_code,
                detail=f"Instagram API error: {r.text}",
            )
        return {"reply_id": r.json().get("id"), "status": "sent"}

    elif comment.platform == "youtube":
        if post.connected_account:
            account = post.connected_account
            # Refresh token if expired
            if account.token_expires_at and datetime.utcnow() >= account.token_expires_at:
                if not account.refresh_token:
                    raise HTTPException(status_code=400, detail="YouTube token expired")
                token_data = await refresh_access_token(account.refresh_token)
                account.access_token = token_data["access_token"]
                account.token_expires_at = token_expires_at(token_data.get("expires_in", 3600))
                await db.commit()
            access_token = account.access_token
        else:
            access_token = current_user.youtube_access_token
        if not access_token:
            raise HTTPException(status_code=400, detail="YouTube account not connected")

        return await reply_to_youtube_comment(comment_id, request.message, access_token)

    else:
        raise HTTPException(status_code=400, detail="Replies are not supported for this platform")
