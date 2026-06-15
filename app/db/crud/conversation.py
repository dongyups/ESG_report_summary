# 간단한 챗봇 채팅 관련 데이터베이스 생성/조회/수정/삭제(CRUD) 작업을 처리하는 파일

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import List, Optional
# local
from app.db.models.conversation import Conversation, Message


async def create_conversation(db: AsyncSession, user_id: int, title: str = "새 채팅") -> Conversation:
    """새 대화 생성"""
    conversation = Conversation(user_id=user_id, title=title)
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


async def get_conversations(db: AsyncSession, user_id: int) -> List[Conversation]:
    """사용자의 모든 대화 목록 조회"""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        # .order_by(desc(Conversation.updated_at)) # (최신순)
    )
    return result.scalars().all()


async def get_conversation(db: AsyncSession, conversation_id: int, user_id: int) -> Optional[Conversation]:
    """특정 대화 조회"""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def update_conversation_title(db: AsyncSession, conversation_id: int, user_id: int, title: str) -> Optional[Conversation]:
    """대화 제목 수정"""
    conversation = await get_conversation(db, conversation_id, user_id)
    if conversation:
        conversation.title = title
        await db.commit()
        await db.refresh(conversation)
    return conversation


async def delete_conversation(db: AsyncSession, conversation_id: int, user_id: int) -> bool:
    """대화 삭제"""
    conversation = await get_conversation(db, conversation_id, user_id)
    if conversation:
        await db.delete(conversation)
        await db.commit()
        return True
    return False


async def create_message(db: AsyncSession, conversation_id: int, role: str, content: str) -> Message:
    """메시지 생성"""
    message = Message(conversation_id=conversation_id, role=role, content=content)
    db.add(message)
    await db.commit()
    await db.refresh(message)
    
    # 대화의 updated_at 갱신
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    if conversation:
        await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        await db.commit()
    
    return message


async def get_messages(db: AsyncSession, conversation_id: int) -> List[Message]:
    """특정 대화의 모든 메시지 조회"""
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    return result.scalars().all()