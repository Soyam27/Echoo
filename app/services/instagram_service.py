import httpx
from fastapi import HTTPException

from app.config import settings

_AUTH_URL = "https://api.instagram.com/oauth/authorize"
_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
_GRAPH_URL = "https://graph.instagram.com"


def build_oauth_url(state: str) -> str:
    params = (
        f"client_id={settings.instagram_client_id}"
        f"&redirect_uri={settings.instagram_redirect_uri}"
        f"&scope=instagram_business_basic,instagram_business_manage_comments"
        f"&response_type=code"
        f"&state={state}"
    )
    return f"{_AUTH_URL}?{params}"


async def exchange_code(code: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            _TOKEN_URL,
            data={
                "client_id": settings.instagram_client_id,
                "client_secret": settings.instagram_client_secret,
                "grant_type": "authorization_code",
                "redirect_uri": settings.instagram_redirect_uri,
                "code": code,
            },
        )
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Instagram token exchange failed: {r.text}")
    return r.json()


async def get_long_lived_token(short_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{_GRAPH_URL}/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": settings.instagram_client_secret,
                "access_token": short_token,
            },
        )
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Long-lived token exchange failed: {r.text}")
    return r.json()


async def get_ig_user(access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{_GRAPH_URL}/me",
            params={"fields": "id,username", "access_token": access_token},
        )
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch Instagram user info")
    return r.json()


async def fetch_user_posts(access_token: str, ig_user_id: str) -> list[dict]:
    posts = []
    url = f"{_GRAPH_URL}/{ig_user_id}/media"
    params = {
        "fields": "id,caption,media_type,media_url,permalink,timestamp",
        "access_token": access_token,
        "limit": 50,
    }

    async with httpx.AsyncClient() as client:
        while url:
            r = await client.get(url, params=params)
            if r.status_code != 200:
                break
            data = r.json()
            posts.extend(data.get("data", []))
            url = data.get("paging", {}).get("next")
            params = {}  # next URL already includes all params

    return posts


async def fetch_post_comments(post_id: str, access_token: str) -> list[dict]:
    all_comments = []
    url = f"{_GRAPH_URL}/{post_id}/comments"
    params = {
        # replies{id} lets us collect all reply IDs so we can exclude them
        "fields": "id,text,username,timestamp,replies{id}",
        "access_token": access_token,
        "limit": 100,
    }

    async with httpx.AsyncClient() as client:
        while url:
            r = await client.get(url, params=params)
            if r.status_code != 200:
                break
            data = r.json()
            all_comments.extend(data.get("data", []))
            url = data.get("paging", {}).get("next")
            params = {}

    # Build set of all reply IDs — any comment whose ID appears here is a reply
    reply_ids: set[str] = set()
    for c in all_comments:
        for reply in c.get("replies", {}).get("data", []):
            reply_ids.add(reply["id"])

    return [c for c in all_comments if c["id"] not in reply_ids]
