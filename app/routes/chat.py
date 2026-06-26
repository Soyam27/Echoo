import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.schemas.schemas import ChatRequest, ChatResponse, CommentResponse
from app.models.models import User, Conversation, Message
from app.core.deps import get_current_user
from app.services.chat_service import answer_question

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Get existing conversation or create one
    conversation = None
    if request.conversation_id:
        result = await db.execute(
            select(Conversation).where(
                Conversation.id == request.conversation_id,
                Conversation.user_id == current_user.id,
            )
        )
        conversation = result.scalar_one_or_none()

    if not conversation:
        conversation = Conversation(
            id=uuid.uuid4(),
            user_id=current_user.id,
            title=request.question[:60],
            created_at=datetime.utcnow(),
        )
        db.add(conversation)
        await db.flush()

    db.add(Message(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        role="user",
        content=request.question,
    ))

    answer, source_comments = await answer_question(request.question, request.post_ids, db)

    db.add(Message(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        role="assistant",
        content=answer,
    ))

    await db.commit()

    return ChatResponse(
        answer=answer,
        conversation_id=conversation.id,
        sources=[
            CommentResponse(id=c.id, username=c.username, text=c.text, posted_at=c.posted_at)
            for c in source_comments
        ],
    )


@router.get("/conversations")
async def list_conversations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == current_user.id)
        .order_by(Conversation.created_at.desc())
    )
    convos = result.scalars().all()
    return [{"id": str(c.id), "title": c.title, "created_at": c.created_at} for c in convos]
