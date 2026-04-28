# 챗봇 관련 요청/응답 데이터 구조(Pydantic 스키마)를 정의하는 파일
from pydantic import BaseModel, Field
from typing import Optional, List


# Request/Response Models
class MessageRequest(BaseModel):
    content: str


class ConversationCreate(BaseModel):
    title: Optional[str] = "새 채팅"


class ConversationUpdate(BaseModel):
    title: str


class MessageResponse(BaseModel):
    id: int
    role: str
    content: str
    created_at: str
    
    class Config:
        from_attributes = True


class ConversationResponse(BaseModel):
    id: int
    title: str
    created_at: str
    updated_at: str
    
    class Config:
        from_attributes = True


class ConversationDetailResponse(BaseModel):
    id: int
    title: str
    created_at: str
    updated_at: str
    messages: List[MessageResponse]
    
    class Config:
        from_attributes = True