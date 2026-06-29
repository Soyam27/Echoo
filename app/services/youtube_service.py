from urllib.parse import urlencode
from datetime import datetime, timedelta

import httpx
from fastapi import HTTPException

from app.config import settings

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_API_BASE = "https://www.googleapis.com/youtube/v3"

# youtube.readonly for reading; youtube.force-ssl for posting comments/replies
_SCOPES = (
    "https://www.googleapis.com/auth/youtube.readonly "
    "https://www.googleapis.com/auth/youtube.force-ssl"
)


def build_oauth_url(state: str) -> str:
    params = urlencode({
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": _SCOPES,
        "access_type": "offline",   # request refresh token
        "prompt": "consent",        # always show consent so refresh token is returned
        "state": state,
    })
    return f"{_AUTH_URL}?{params}"


async def exchange_code(code: str) -> dict:
    """Exchange auth code for access + refresh tokens."""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            _TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail=f"YouTube token exchange failed: {r.text}")
    return r.json()


async def refresh_access_token(refresh_token: str) -> dict:
    """Get a new access token using a refresh token."""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            _TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "grant_type": "refresh_token",
            },
        )
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail=f"YouTube token refresh failed: {r.text}")
    return r.json()


async def get_channel_info(access_token: str) -> dict:
    """Return channel id and title for the authenticated user."""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{_API_BASE}/channels",
            params={
                "part": "snippet,contentDetails",
                "mine": "true",
                "access_token": access_token,
            },
        )
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch YouTube channel info")
    data = r.json()
    items = data.get("items", [])
    if not items:
        raise HTTPException(status_code=400, detail="No YouTube channel found for this account")
    ch = items[0]
    return {
        "id": ch["id"],
        "title": ch["snippet"]["title"],
        "uploads_playlist_id": ch["contentDetails"]["relatedPlaylists"]["uploads"],
    }


async def fetch_channel_videos(access_token: str, uploads_playlist_id: str) -> list[dict]:
    """Return up to 50 most recent uploaded videos for the channel."""
    videos = []
    url = f"{_API_BASE}/playlistItems"
    params = {
        "part": "snippet",
        "playlistId": uploads_playlist_id,
        "maxResults": 50,
        "access_token": access_token,
    }

    async with httpx.AsyncClient() as client:
        while url and len(videos) < 50:
            r = await client.get(url, params=params)
            if r.status_code != 200:
                break
            data = r.json()
            for item in data.get("items", []):
                snip = item["snippet"]
                videos.append({
                    "id": snip["resourceId"]["videoId"],
                    "title": snip.get("title", ""),
                    "thumbnail": (snip.get("thumbnails", {}).get("medium") or
                                  snip.get("thumbnails", {}).get("default") or {}).get("url"),
                    "published_at": snip.get("publishedAt", ""),
                    "permalink": f"https://www.youtube.com/watch?v={snip['resourceId']['videoId']}",
                })
            next_page = data.get("nextPageToken")
            if not next_page or len(videos) >= 50:
                break
            params = {"part": "snippet", "playlistId": uploads_playlist_id,
                      "maxResults": 50, "access_token": access_token,
                      "pageToken": next_page}

    return videos[:50]


_MAX_COMMENTS = 10_000  # fetch up to 10k top-level comments (100 per page = 100 pages)


async def stream_video_comments(video_id: str, access_token: str, chunk_size: int = 1000):
    """Async generator yielding comment chunks as pages arrive.

    Yields a list of up to chunk_size comments after every chunk_size items are
    collected, so the caller can embed/insert the first batch while the next
    YouTube pages are still being fetched (true I/O pipelining via asyncio.gather).
    """
    chunk: list[dict] = []
    params = {
        "part": "snippet",
        "videoId": video_id,
        "maxResults": 100,
        "order": "time",
        "access_token": access_token,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        url = f"{_API_BASE}/commentThreads"
        total = 0
        while url and total < _MAX_COMMENTS:
            r = await client.get(url, params=params)
            if r.status_code == 403:
                break
            if r.status_code != 200:
                break
            data = r.json()
            for item in data.get("items", []):
                top = item["snippet"]["topLevelComment"]
                chunk.append({
                    "id": top["id"],
                    "text": top["snippet"].get("textDisplay", ""),
                    "username": top["snippet"].get("authorDisplayName", "unknown"),
                    "published_at": top["snippet"].get("publishedAt", ""),
                })
                total += 1
            if len(chunk) >= chunk_size:
                yield chunk
                chunk = []
            next_page = data.get("nextPageToken")
            if not next_page or total >= _MAX_COMMENTS:
                break
            params = {
                "part": "snippet", "videoId": video_id,
                "maxResults": 100, "order": "time",
                "access_token": access_token, "pageToken": next_page,
            }
            url = f"{_API_BASE}/commentThreads"
    if chunk:
        yield chunk


async def fetch_video_comments(video_id: str, access_token: str) -> list[dict]:
    """Return all top-level comments for a video (up to _MAX_COMMENTS).

    Stores the topLevelComment.id (not the thread ID) so it can be used
    as parentId when replying via the comments.insert API.
    """
    comments = []
    params = {
        "part": "snippet",
        "videoId": video_id,
        "maxResults": 100,
        "order": "time",
        "access_token": access_token,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        url = f"{_API_BASE}/commentThreads"
        while url and len(comments) < _MAX_COMMENTS:
            r = await client.get(url, params=params)
            if r.status_code == 403:
                # Comments disabled on this video — treat as empty
                break
            if r.status_code != 200:
                break
            data = r.json()
            for item in data.get("items", []):
                top = item["snippet"]["topLevelComment"]
                comments.append({
                    # Use the top-level comment's own ID (needed as parentId for replies)
                    "id": top["id"],
                    "text": top["snippet"].get("textDisplay", ""),
                    "username": top["snippet"].get("authorDisplayName", "unknown"),
                    "published_at": top["snippet"].get("publishedAt", ""),
                })
            next_page = data.get("nextPageToken")
            if not next_page or len(comments) >= _MAX_COMMENTS:
                break
            params = {
                "part": "snippet", "videoId": video_id,
                "maxResults": 100, "order": "time",
                "access_token": access_token, "pageToken": next_page,
            }
            url = f"{_API_BASE}/commentThreads"

    return comments


async def reply_to_youtube_comment(parent_comment_id: str, text: str, access_token: str) -> dict:
    """Post a reply to a YouTube comment.

    parent_comment_id should be the top-level comment's ID (topLevelComment.id),
    which is what we store in youtube_comment_id after the fix to fetch_video_comments.
    Requires the youtube.force-ssl OAuth scope.
    """
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{_API_BASE}/comments",
            params={"part": "snippet"},
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "snippet": {
                    "parentId": parent_comment_id,
                    "textOriginal": text,
                }
            },
        )
    if r.status_code not in (200, 201):
        raise HTTPException(
            status_code=r.status_code,
            detail=f"YouTube API error: {r.text}",
        )
    data = r.json()
    return {"reply_id": data.get("id"), "status": "sent"}


def token_expires_at(expires_in_seconds: int) -> datetime:
    """Convert expires_in seconds to absolute datetime with 60-second buffer."""
    return datetime.utcnow() + timedelta(seconds=expires_in_seconds - 60)
