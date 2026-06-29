import uuid
import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import String, DateTime, ForeignKey, Text, Integer, Boolean, Enum as SAEnum, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

from app.database import Base


class SyncStatus(str, enum.Enum):
    pending = "pending"
    syncing = "syncing"
    completed = "completed"
    failed = "failed"


class Platform(str, enum.Enum):
    instagram = "instagram"
    youtube = "youtube"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # Legacy single-account fields — kept for DB backward compat; source of truth is now connected_accounts
    instagram_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    instagram_username: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    instagram_access_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    youtube_channel_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    youtube_channel_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    youtube_uploads_playlist_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    youtube_access_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    youtube_refresh_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    youtube_token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    posts: Mapped[list["Post"]] = relationship("Post", back_populates="user", cascade="all, delete-orphan")
    conversations: Mapped[list["Conversation"]] = relationship("Conversation", back_populates="user", cascade="all, delete-orphan")
    connected_accounts: Mapped[list["ConnectedAccount"]] = relationship("ConnectedAccount", back_populates="user", cascade="all, delete-orphan")


class ConnectedAccount(Base):
    __tablename__ = "connected_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)

    # Instagram-specific
    instagram_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    instagram_username: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # YouTube-specific
    youtube_channel_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    youtube_channel_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    youtube_uploads_playlist_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Tokens
    access_token: Mapped[str] = mapped_column(Text, nullable=False, default="")
    refresh_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship("User", back_populates="connected_accounts")
    posts: Mapped[list["Post"]] = relationship("Post", back_populates="connected_account")


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    connected_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("connected_accounts.id"), nullable=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False, default="instagram")

    # Platform-specific IDs — only one is set per row; PostgreSQL UNIQUE allows multiple NULLs
    instagram_post_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True, nullable=True, index=True)
    youtube_video_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True, nullable=True, index=True)

    caption: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    media_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    media_type: Mapped[str] = mapped_column(String(50), nullable=False)
    permalink: Mapped[str] = mapped_column(Text, nullable=False)
    posted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    sync_status: Mapped[SyncStatus] = mapped_column(SAEnum(SyncStatus), default=SyncStatus.pending)
    comment_count: Mapped[int] = mapped_column(Integer, default=0)
    synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_external: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship("User", back_populates="posts")
    connected_account: Mapped[Optional["ConnectedAccount"]] = relationship("ConnectedAccount", back_populates="posts")
    comments: Mapped[list["Comment"]] = relationship("Comment", back_populates="post", cascade="all, delete-orphan")


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("posts.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False, default="instagram")

    # Platform-specific comment IDs — only one is set per row
    instagram_comment_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True, nullable=True)
    youtube_comment_id: Mapped[Optional[str]] = mapped_column(String(200), unique=True, nullable=True)

    username: Mapped[str] = mapped_column(String(100), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    posted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    embedding: Mapped[Optional[list]] = mapped_column(Vector(1536), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    post: Mapped["Post"] = relationship("Post", back_populates="comments")

    @property
    def external_comment_id(self) -> str:
        return self.instagram_comment_id or self.youtube_comment_id or ""


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    post_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship("User", back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "user" or "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")
