from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.schemas.schemas import RegisterRequest, LoginRequest, TokenResponse, UserResponse
from app.services.auth_service import register_user, login_user
from app.models.models import User
from app.core.deps import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])

_COOKIE_MAX_AGE = settings.access_token_expire_days * 24 * 60 * 60


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="echoo_token",
        value=token,
        httponly=True,
        secure=not settings.debug,
        samesite="lax",
        max_age=_COOKIE_MAX_AGE,
        path="/",
    )


@router.post("/register", response_model=TokenResponse)
async def register(data: RegisterRequest, response: Response, db: AsyncSession = Depends(get_db)):
    _, token = await register_user(data.email, data.password, db)
    _set_auth_cookie(response, token)
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    _, token = await login_user(data.email, data.password, db)
    _set_auth_cookie(response, token)
    return TokenResponse(access_token=token)


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(key="echoo_token", path="/")
    return {"message": "Logged out"}


@router.get("/me", response_model=UserResponse)
async def me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User)
        .where(User.id == current_user.id)
        .options(selectinload(User.connected_accounts))
    )
    user = result.scalar_one()
    return user
