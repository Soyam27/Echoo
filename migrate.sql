-- Echoo: YouTube support migration
-- Run this ONCE against your existing database before deploying the new backend.
-- Safe to re-run: uses IF NOT EXISTS / IF EXISTS guards throughout.

-- ── Users: add YouTube / Google fields ────────────────────────────────────────
ALTER TABLE users ADD COLUMN IF NOT EXISTS youtube_channel_id VARCHAR(100);
ALTER TABLE users ADD COLUMN IF NOT EXISTS youtube_channel_name VARCHAR(100);
ALTER TABLE users ADD COLUMN IF NOT EXISTS youtube_uploads_playlist_id VARCHAR(100);
ALTER TABLE users ADD COLUMN IF NOT EXISTS youtube_access_token TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS youtube_refresh_token TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS youtube_token_expires_at TIMESTAMP;

-- ── Posts: add platform + YouTube video ID; make instagram_post_id nullable ───
ALTER TABLE posts ADD COLUMN IF NOT EXISTS platform VARCHAR(20) NOT NULL DEFAULT 'instagram';
ALTER TABLE posts ADD COLUMN IF NOT EXISTS youtube_video_id VARCHAR(100);

-- Make instagram_post_id nullable (existing rows keep their values).
-- PostgreSQL UNIQUE allows multiple NULLs so both columns can have a sparse unique index.
DO $$
BEGIN
    ALTER TABLE posts ALTER COLUMN instagram_post_id DROP NOT NULL;
EXCEPTION WHEN others THEN
    NULL; -- already nullable
END $$;

-- Add unique index for youtube_video_id (partial, only non-null rows)
CREATE UNIQUE INDEX IF NOT EXISTS uq_posts_youtube_video_id
    ON posts (youtube_video_id) WHERE youtube_video_id IS NOT NULL;

-- ── Comments: add platform + YouTube comment ID; make instagram_comment_id nullable ──
ALTER TABLE comments ADD COLUMN IF NOT EXISTS platform VARCHAR(20) NOT NULL DEFAULT 'instagram';
ALTER TABLE comments ADD COLUMN IF NOT EXISTS youtube_comment_id VARCHAR(200);

DO $$
BEGIN
    ALTER TABLE comments ALTER COLUMN instagram_comment_id DROP NOT NULL;
EXCEPTION WHEN others THEN
    NULL; -- already nullable
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_comments_youtube_comment_id
    ON comments (youtube_comment_id) WHERE youtube_comment_id IS NOT NULL;
