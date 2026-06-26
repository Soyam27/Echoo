from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from jose import JWTError, jwt

from app.config import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(user_id: str) -> str:
    expire = datetime.utcnow() + timedelta(days=settings.access_token_expire_days)
    return jwt.encode({"sub": user_id, "exp": expire}, settings.secret_key, algorithm="HS256")


def decode_access_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        return payload.get("sub")
    except JWTError:
        return None


def create_state_token(user_id: str) -> str:
    """Short-lived token embedded in Instagram OAuth state param."""
    expire = datetime.utcnow() + timedelta(minutes=10)
    return jwt.encode(
        {"sub": user_id, "exp": expire, "type": "oauth_state"},
        settings.secret_key,
        algorithm="HS256",
    )


def decode_state_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        if payload.get("type") != "oauth_state":
            return None
        return payload.get("sub")
    except JWTError:
        return None
