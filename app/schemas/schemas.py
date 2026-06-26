from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime
from uuid import UUID


# ── Auth ────────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class UserResponse(BaseModel):
    id: UUID
    email: str
    instagram_username: Optional[str] = None
    instagram_id: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Posts ────────────────────────────────────────────────────────────────────

class PostResponse(BaseModel):
    id: UUID
    instagram_post_id: str
    caption: Optional[str] = None
    media_url: Optional[str] = None
    media_type: str
    permalink: str
    posted_at: datetime
    sync_status: str
    comment_count: int
    synced_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

class SyncRequest(BaseModel):
    post_ids: List[str]  # Instagram post IDs (e.g. "17854360229135492")


# ── Comments ─────────────────────────────────────────────────────────────────

class CommentResponse(BaseModel):
    id: UUID
    username: str
    text: str
    posted_at: datetime

    model_config = {"from_attributes": True}


# ── Chat ─────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    question: str
    post_ids: List[UUID]           # internal DB post UUIDs to search over
    conversation_id: Optional[UUID] = None

class ChatResponse(BaseModel):
    answer: str
    conversation_id: UUID
    sources: List[CommentResponse]


# ── Instagram ─────────────────────────────────────────────────────────────────

class InstagramConnectResponse(BaseModel):
    url: str

class SyncStatusResponse(BaseModel):
    post_id: str
    sync_status: str
    comment_count: int
    synced_at: Optional[datetime] = None
