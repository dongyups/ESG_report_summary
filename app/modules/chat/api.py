# 챗봇 요청을 처리하는 API 엔드포인트를 정의하는 파일

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import List, Optional
import json
# local
from app.db.models.database import get_db
from app.db.crud import conversation as crud
from app.modules.chat import service
from app.modules.auth.dependency import get_current_user
from app.db.models.user import User
from app.schemas import conversation as schemas

router = APIRouter()


# 대화 목록 조회
@router.get("/conversations", response_model=List[schemas.ConversationResponse])
async def list_conversations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """사용자의 모든 대화 목록 조회"""
    conversations = await crud.get_conversations(db, current_user.id)
    return [
        schemas.ConversationResponse(
            id=conv.id,
            title=conv.title,
            created_at=conv.created_at.isoformat(),
            updated_at=conv.updated_at.isoformat()
        )
        for conv in conversations
    ]


# 새 대화 생성
@router.post("/conversations", response_model=schemas.ConversationResponse)
async def create_conversation(
    conversation_data: schemas.ConversationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """새 대화 생성"""
    conversation = await crud.create_conversation(db, current_user.id, conversation_data.title)
    return schemas.ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at.isoformat(),
        updated_at=conversation.updated_at.isoformat()
    )


# 특정 대화 조회 (메시지 포함)
@router.get("/conversations/{conversation_id}", response_model=schemas.ConversationDetailResponse)
async def get_conversation(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """특정 대화 및 메시지 조회"""
    conversation = await crud.get_conversation(db, conversation_id, current_user.id)
    if not conversation:
        raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다")
    
    messages = await crud.get_messages(db, conversation_id)
    
    return schemas.ConversationDetailResponse(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at.isoformat(),
        updated_at=conversation.updated_at.isoformat(),
        messages=[
            schemas.MessageResponse(
                id=msg.id,
                role=msg.role,
                content=msg.content,
                created_at=msg.created_at.isoformat()
            )
            for msg in messages
        ]
    )


# 대화 제목 수정
@router.put("/conversations/{conversation_id}", response_model=schemas.ConversationResponse)
async def update_conversation(
    conversation_id: int,
    conversation_data: schemas.ConversationUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """대화 제목 수정"""
    conversation = await crud.update_conversation_title(
        db, conversation_id, current_user.id, conversation_data.title
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다")
    
    return schemas.ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at.isoformat(),
        updated_at=conversation.updated_at.isoformat()
    )


# 대화 삭제
@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """대화 삭제"""
    success = await crud.delete_conversation(db, conversation_id, current_user.id)
    if not success:
        raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다")
    
    return {"message": "대화가 삭제되었습니다"}


# 메시지 전송 및 AI 응답 (스트리밍)
@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: int,
    message: schemas.MessageRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """메시지 전송 및 AI 응답 스트리밍"""
    # 대화 존재 확인
    conversation = await crud.get_conversation(db, conversation_id, current_user.id)
    if not conversation:
        raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다")
    
    # 사용자 메시지 저장
    await crud.create_message(db, conversation_id, "user", message.content)
    
    # 대화 히스토리 로드
    messages = await crud.get_messages(db, conversation_id)
    formatted_messages = service.format_messages_for_api(messages)
    formatted_messages = formatted_messages[-5:] ### 가장 최신 5개의 기록만 LLM의 입력으로
    
    # AI 응답 생성 (스트리밍)
    async def response_generator():
        full_response = ""
        try:
            async for chunk in service.generate_chat_response(formatted_messages):
                full_response += chunk
                # SSE 형식으로 전송
                yield f"data: {json.dumps({'chunk': chunk})}\n\n"
            
            # 응답 완료 후 DB에 저장
            await crud.create_message(db, conversation_id, "assistant", full_response)
            yield f"data: {json.dumps({'done': True})}\n\n"
            
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
    
    return StreamingResponse(
        response_generator(),
        media_type="text/event-stream"
    )