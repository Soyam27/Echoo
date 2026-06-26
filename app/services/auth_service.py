import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.models import User
from app.core.security import hash_password, verify_password, create_access_token


async def register_user(email: str, password: str, db: AsyncSession) -> tuple[User, str]:
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    user = User(id=uuid.uuid4(), email=email, password_hash=hash_password(password))
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return user, create_access_token(str(user.id))


async def login_user(email: str, password: str, db: AsyncSession) -> tuple[User, str]:
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    return user, create_access_token(str(user.id))
