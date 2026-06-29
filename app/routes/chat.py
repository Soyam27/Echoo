import json
import uuid
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.database import get_db, AsyncSessionLocal
from app.schemas.schemas import ChatRequest, ChatResponse, CommentResponse, MessageResponse
from app.models.models import User, Conversation, Message
from app.core.deps import get_current_user
from app.services.chat_service import answer_question, build_analysis_context, _client, _parse_used

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
            post_ids=[str(pid) for pid in request.post_ids],
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

    # Build history for analysis mode (last 3 turns)
    history = None
    if request.mode == "analysis" and request.conversation_id:
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.desc())
            .limit(6)
        )
        past = list(reversed(result.scalars().all()))
        history = [{"role": m.role, "content": m.content} for m in past]

    answer, source_comments = await answer_question(
        request.question, request.post_ids, db,
        mode=request.mode,
        history=history,
    )
    print(answer)
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
            CommentResponse(
                id=c.id,
                platform=c.platform,
                external_comment_id=c.instagram_comment_id or c.youtube_comment_id or "",
                username=c.username,
                text=c.text,
                posted_at=c.posted_at,
            )
            for c in source_comments
        ],
    )


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
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
            post_ids=[str(pid) for pid in request.post_ids],
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

    history = None
    if request.conversation_id:
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.desc())
            .limit(6)
        )
        past = list(reversed(result.scalars().all()))
        history = [{"role": m.role, "content": m.content} for m in past]

    # Do all retrieval before streaming starts so db session is free
    llm_messages, comments = await build_analysis_context(
        request.question, request.post_ids, db, history
    )
    await db.commit()

    conversation_id = conversation.id

    async def generate():
        if not comments:
            yield f"data: {json.dumps({'type': 'error', 'content': 'No synced comments found.'})}\n\n"
            return

        stream = await _client.chat.completions.create(
            model=settings.azure_chat_deployment,
            messages=llm_messages,
            max_tokens=400,
            temperature=0.3,
            stream=True,
        )

        full_text = ""
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content or ""
            if delta:
                full_text += delta
                yield f"data: {json.dumps({'type': 'text', 'content': delta})}\n\n"

        answer, used_indices = _parse_used(full_text)
        source_comments = [comments[i] for i in sorted(used_indices) if i < len(comments)]
        if not source_comments:
            source_comments = comments

        async with AsyncSessionLocal() as save_db:
            save_db.add(Message(
                id=uuid.uuid4(),
                conversation_id=conversation_id,
                role="assistant",
                content=answer,
            ))
            await save_db.commit()

        sources_data = [
            {
                "id": str(c.id),
                "platform": c.platform,
                "external_comment_id": c.instagram_comment_id or c.youtube_comment_id or "",
                "username": c.username,
                "text": c.text,
                "posted_at": c.posted_at.isoformat(),
            }
            for c in source_comments
        ]
        yield f"data: {json.dumps({'type': 'done', 'conversation_id': str(conversation_id), 'sources': sources_data})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


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
    return [{"id": str(c.id), "title": c.title, "post_ids": c.post_ids or [], "created_at": c.created_at} for c in convos]


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageResponse])
async def get_conversation_messages(
    conversation_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
        )
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    return result.scalars().all()
