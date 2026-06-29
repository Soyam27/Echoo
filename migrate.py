"""Run to apply all schema migrations. Safe to run multiple times (idempotent)."""
import asyncio
import sys
from sqlalchemy import text
from app.database import engine

_STATEMENTS = [
    # ── YouTube columns on users (from first migration) ──────────────────────
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS youtube_channel_id VARCHAR(100)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS youtube_channel_name VARCHAR(100)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS youtube_uploads_playlist_id VARCHAR(100)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS youtube_access_token TEXT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS youtube_refresh_token TEXT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS youtube_token_expires_at TIMESTAMP",
    # ── Posts platform + YouTube columns ─────────────────────────────────────
    "ALTER TABLE posts ADD COLUMN IF NOT EXISTS platform VARCHAR(20) NOT NULL DEFAULT 'instagram'",
    "ALTER TABLE posts ADD COLUMN IF NOT EXISTS youtube_video_id VARCHAR(100)",
    "ALTER TABLE posts ALTER COLUMN instagram_post_id DROP NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_posts_youtube_video_id ON posts (youtube_video_id) WHERE youtube_video_id IS NOT NULL",
    # ── Comments platform + YouTube columns ──────────────────────────────────
    "ALTER TABLE comments ADD COLUMN IF NOT EXISTS platform VARCHAR(20) NOT NULL DEFAULT 'instagram'",
    "ALTER TABLE comments ADD COLUMN IF NOT EXISTS youtube_comment_id VARCHAR(200)",
    "ALTER TABLE comments ALTER COLUMN instagram_comment_id DROP NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_comments_youtube_comment_id ON comments (youtube_comment_id) WHERE youtube_comment_id IS NOT NULL",
    # ── connected_accounts table (multiple accounts per user) ─────────────────
    """
    CREATE TABLE IF NOT EXISTS connected_accounts (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        platform VARCHAR(20) NOT NULL,
        instagram_id VARCHAR(100),
        instagram_username VARCHAR(100),
        youtube_channel_id VARCHAR(100),
        youtube_channel_name VARCHAR(100),
        youtube_uploads_playlist_id VARCHAR(100),
        access_token TEXT NOT NULL DEFAULT '',
        refresh_token TEXT,
        token_expires_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    # Unique per user per Instagram account
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_ca_instagram ON connected_accounts (user_id, instagram_id) WHERE instagram_id IS NOT NULL",
    # Unique per user per YouTube channel
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_ca_youtube ON connected_accounts (user_id, youtube_channel_id) WHERE youtube_channel_id IS NOT NULL",
    # ── Migrate existing Instagram accounts from users table ──────────────────
    """
    INSERT INTO connected_accounts (user_id, platform, instagram_id, instagram_username, access_token, token_expires_at)
    SELECT id, 'instagram', instagram_id, instagram_username,
           COALESCE(instagram_access_token, ''), token_expires_at
    FROM users
    WHERE instagram_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM connected_accounts ca
        WHERE ca.user_id = users.id
        AND ca.platform = 'instagram'
        AND ca.instagram_id = users.instagram_id
    )
    """,
    # ── Migrate existing YouTube accounts from users table ────────────────────
    """
    INSERT INTO connected_accounts (
        user_id, platform, youtube_channel_id, youtube_channel_name,
        youtube_uploads_playlist_id, access_token, refresh_token, token_expires_at
    )
    SELECT id, 'youtube', youtube_channel_id, youtube_channel_name,
           youtube_uploads_playlist_id, COALESCE(youtube_access_token, ''),
           youtube_refresh_token, youtube_token_expires_at
    FROM users
    WHERE youtube_channel_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM connected_accounts ca
        WHERE ca.user_id = users.id
        AND ca.platform = 'youtube'
        AND ca.youtube_channel_id = users.youtube_channel_id
    )
    """,
    # ── Add connected_account_id to posts ────────────────────────────────────
    "ALTER TABLE posts ADD COLUMN IF NOT EXISTS connected_account_id UUID REFERENCES connected_accounts(id)",
    # ── Back-fill connected_account_id on existing posts ─────────────────────
    """
    UPDATE posts p
    SET connected_account_id = ca.id
    FROM connected_accounts ca
    WHERE ca.user_id = p.user_id
    AND ca.platform = p.platform
    AND p.connected_account_id IS NULL
    """,
]


async def main():
    async with engine.begin() as conn:
        for stmt in _STATEMENTS:
            short = stmt.strip().replace('\n', ' ')
            short = short[:80] + '...' if len(short) > 80 else short
            print(f"  > {short}")
            await conn.execute(text(stmt))
    print("\nMigration complete.")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
