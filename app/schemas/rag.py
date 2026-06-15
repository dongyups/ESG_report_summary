# 보고서 관련 요청/응답 데이터 구조(Pydantic 스키마)를 정의하는 파일
from pydantic import BaseModel
from typing import Optional, List


class RagMessageRequest(BaseModel):
    content: str


class RagConversationCreate(BaseModel):
    title: Optional[str] = "새 RAG 채팅"


class RagConversationUpdate(BaseModel):
    title: str


class RagMessageResponse(BaseModel):
    id: int
    role: str
    content: str
    thinking: Optional[str] = None
    sources: Optional[str] = None   # JSON string — 프론트에서 JSON.parse
    created_at: str

    class Config:
        from_attributes = True


class RagConversationResponse(BaseModel):
    id: int
    title: str
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class RagConversationDetailResponse(BaseModel):
    id: int
    title: str
    created_at: str
    updated_at: str
    messages: List[RagMessageResponse]

    class Config:
        from_attributes = True
