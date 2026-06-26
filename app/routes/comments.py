import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.models import User, Comment, Post
from app.core.deps import get_current_user

router = APIRouter(prefix="/comments", tags=["comments"])

_GRAPH_URL = "https://graph.instagram.com"


class ReplyRequest(BaseModel):
    message: str


@router.post("/{instagram_comment_id}/reply")
async def reply_to_comment(
    instagram_comment_id: str,
    request: ReplyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify the comment belongs to this user
    result = await db.execute(
        select(Comment)
        .join(Post, Comment.post_id == Post.id)
        .where(
            Comment.instagram_comment_id == instagram_comment_id,
            Post.user_id == current_user.id,
        )
    )
    comment = result.scalar_one_or_none()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    if not current_user.instagram_access_token:
        raise HTTPException(status_code=400, detail="Instagram account not connected")

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{_GRAPH_URL}/{instagram_comment_id}/replies",
            params={
                "message": request.message,
                "access_token": current_user.instagram_access_token,
            },
        )

    if r.status_code != 200:
        raise HTTPException(
            status_code=r.status_code,
            detail=f"Instagram API error: {r.text}",
        )

    return {"reply_id": r.json().get("id"), "status": "sent"}
