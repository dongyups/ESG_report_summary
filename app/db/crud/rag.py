# 보고서 생성을 위한 챗봇 관련 데이터베이스 생성/조회/수정/삭제(CRUD) 작업을 처리하는 파일
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
# local
from app.db.models.rag import RagConversation, RagMessage


async def create_conversation(db: AsyncSession, user_id: int, title: str = "새 RAG 채팅") -> RagConversation:
    conv = RagConversation(user_id=user_id, title=title)
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return conv


async def get_conversations(db: AsyncSession, user_id: int) -> List[RagConversation]:
    result = await db.execute(
        select(RagConversation)
        .where(RagConversation.user_id == user_id)
        .order_by(RagConversation.updated_at.desc())
    )
    return result.scalars().all()


async def get_conversation(db: AsyncSession, conv_id: int, user_id: int) -> Optional[RagConversation]:
    result = await db.execute(
        select(RagConversation).where(
            RagConversation.id == conv_id,
            RagConversation.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def update_conversation_title(db: AsyncSession, conv_id: int, user_id: int, title: str) -> Optional[RagConversation]:
    conv = await get_conversation(db, conv_id, user_id)
    if conv:
        conv.title = title
        await db.commit()
        await db.refresh(conv)
    return conv


async def delete_conversation(db: AsyncSession, conv_id: int, user_id: int) -> bool:
    conv = await get_conversation(db, conv_id, user_id)
    if conv:
        await db.delete(conv)
        await db.commit()
        return True
    return False


async def create_message(
    db: AsyncSession,
    conversation_id: int,
    role: str,
    content: str,
    thinking: Optional[str] = None,
    sources: Optional[str] = None,
) -> RagMessage:
    msg = RagMessage(
        conversation_id=conversation_id,
        role=role,
        content=content,
        thinking=thinking,
        sources=sources,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return msg


async def get_messages(db: AsyncSession, conversation_id: int) -> List[RagMessage]:
    result = await db.execute(
        select(RagMessage)
        .where(RagMessage.conversation_id == conversation_id)
        .order_by(RagMessage.created_at)
    )
    return result.scalars().all()
