from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.database import get_db
from app.models.models import User, ConnectedAccount
from app.core.deps import get_current_user
from app.core.security import create_state_token, decode_state_token
from app.services.youtube_service import (
    build_oauth_url,
    exchange_code,
    get_channel_info,
    token_expires_at,
)

router = APIRouter(prefix="/youtube", tags=["youtube"])


@router.get("/connect")
async def connect_youtube(current_user: User = Depends(get_current_user)):
    state = create_state_token(str(current_user.id))
    return {"url": build_oauth_url(state)}


@router.get("/callback")
async def youtube_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    user_id = decode_state_token(state)
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    token_data = await exchange_code(code)
    channel = await get_channel_info(token_data["access_token"])
    expires_at = token_expires_at(token_data.get("expires_in", 3600))

    # Upsert connected account (unique per user + youtube_channel_id)
    result = await db.execute(
        select(ConnectedAccount).where(
            ConnectedAccount.user_id == user.id,
            ConnectedAccount.platform == "youtube",
            ConnectedAccount.youtube_channel_id == channel["id"],
        )
    )
    account = result.scalar_one_or_none()
    if account:
        account.youtube_channel_name = channel["title"]
        account.youtube_uploads_playlist_id = channel["uploads_playlist_id"]
        account.access_token = token_data["access_token"]
        # Only update refresh_token when Google returns a new one
        if token_data.get("refresh_token"):
            account.refresh_token = token_data["refresh_token"]
        account.token_expires_at = expires_at
    else:
        account = ConnectedAccount(
            user_id=user.id,
            platform="youtube",
            youtube_channel_id=channel["id"],
            youtube_channel_name=channel["title"],
            youtube_uploads_playlist_id=channel["uploads_playlist_id"],
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            token_expires_at=expires_at,
        )
        db.add(account)

    await db.commit()
    return RedirectResponse(url=f"{settings.frontend_url}/posts?connected=true")
