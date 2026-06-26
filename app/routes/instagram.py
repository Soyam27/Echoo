from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.database import get_db
from app.schemas.schemas import InstagramConnectResponse
from app.models.models import User
from app.core.deps import get_current_user
from app.core.security import create_state_token, decode_state_token
from app.services.instagram_service import (
    build_oauth_url,
    exchange_code,
    get_long_lived_token,
    get_ig_user,
)

router = APIRouter(prefix="/instagram", tags=["instagram"])


@router.get("/connect", response_model=InstagramConnectResponse)
async def connect_instagram(current_user: User = Depends(get_current_user)):
    state = create_state_token(str(current_user.id))
    return InstagramConnectResponse(url=build_oauth_url(state))


@router.get("/callback")
async def instagram_callback(
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
    long_token_data = await get_long_lived_token(token_data["access_token"])

    ig_user = await get_ig_user(long_token_data["access_token"])
    expires_in = long_token_data.get("expires_in", 5_184_000)  # default 60 days

    user.instagram_id = ig_user["id"]
    user.instagram_username = ig_user["username"]
    user.instagram_access_token = long_token_data["access_token"]
    user.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

    await db.commit()

    # Redirect browser to frontend posts page
    return RedirectResponse(url=f"{settings.frontend_url}/posts?connected=true")
