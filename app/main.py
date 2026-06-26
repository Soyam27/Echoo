from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import init_db, get_db
from app.routes import auth, instagram, posts, chat
from app.routes.instagram import instagram_callback


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Echoo API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(instagram.router)
app.include_router(posts.router)
app.include_router(chat.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/callback")
async def callback_alias(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Alias for /instagram/callback — matches Meta portal redirect URI."""
    return await instagram_callback(code=code, state=state, db=db)


@app.get("/webhook")
async def webhook_verify(
    hub_mode: str = None,
    hub_challenge: str = None,
    hub_verify_token: str = None,
):
    """Meta webhook verification handshake."""
    if hub_mode == "subscribe" and hub_challenge:
        return int(hub_challenge)
    return {"status": "ok"}


@app.post("/webhook")
async def webhook_events():
    """Receive Meta webhook events — ignored for now (using manual sync)."""
    return {"status": "ok"}
