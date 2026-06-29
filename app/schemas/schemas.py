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

class ConnectedAccountResponse(BaseModel):
    id: UUID
    platform: str
    instagram_id: Optional[str] = None
    instagram_username: Optional[str] = None
    youtube_channel_id: Optional[str] = None
    youtube_channel_name: Optional[str] = None

    model_config = {"from_attributes": True}

class UserResponse(BaseModel):
    id: UUID
    email: str
    connected_accounts: List[ConnectedAccountResponse] = []
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Posts ────────────────────────────────────────────────────────────────────

class PostResponse(BaseModel):
    id: UUID
    platform: str                          # 'instagram' | 'youtube'
    connected_account_id: Optional[UUID] = None
    instagram_post_id: Optional[str] = None
    youtube_video_id: Optional[str] = None
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
    post_ids: List[str]  # DB UUIDs (strings)


# ── Comments ─────────────────────────────────────────────────────────────────

class CommentResponse(BaseModel):
    id: UUID
    platform: str
    external_comment_id: str              # instagram_comment_id or youtube_comment_id
    username: str
    text: str
    posted_at: datetime

    model_config = {"from_attributes": False}


# ── Chat ─────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    question: str
    post_ids: List[UUID]
    mode: str = "listing"          # "listing" or "analysis"
    conversation_id: Optional[UUID] = None

class ChatResponse(BaseModel):
    answer: str
    conversation_id: UUID
    sources: List[CommentResponse]

class MessageResponse(BaseModel):
    id: UUID
    role: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Instagram ─────────────────────────────────────────────────────────────────

class InstagramConnectResponse(BaseModel):
    url: str

class SyncStatusResponse(BaseModel):
    post_id: str          # DB UUID
    platform: str
    sync_status: str
    comment_count: int
    synced_at: Optional[datetime] = None
