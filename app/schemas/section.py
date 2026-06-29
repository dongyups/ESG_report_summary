# 단일 섹션 작성/수정 API(HITL 기반, section_api.py)의 요청/응답 데이터 구조
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel


class SectionStartRequest(BaseModel):
    target_year: str
    section_title: str
    user_query: str                  # 최초 작성 요청/질문
    esg_category: Optional[str] = None  # "E" | "S" | "G" | "I" | None(자동 감지)


class SectionFeedbackRequest(BaseModel):
    action: str                      # "approve" | "reject" | "edit" | "search"
    content: Optional[str] = None    # reject/edit/search 시 필요한 텍스트


class SectionStatusResponse(BaseModel):
    thread_id: str
    stage: Literal["review_docs", "review_draft", "done"]
    message: Optional[str] = None
    draft: Optional[str] = None
    sources: Optional[List[Dict[str, Any]]] = None


# ── 완성 초안 MySQL 저장 관련 스키마 ─────────────────────────
class SectionDraftResponse(BaseModel):
    """GET /rag/sections/drafts, GET /rag/sections/drafts/{id} 응답."""
    id:            int
    thread_id:     str
    section_title: str
    target_year:   str
    esg_category:  Optional[str]
    draft:         str
    sources:       List[Dict[str, Any]]
    created_at:    datetime
    updated_at:    datetime

    model_config = {"from_attributes": True}


class SectionDraftListResponse(BaseModel):
    """GET /rag/sections/drafts 목록 응답."""
    drafts: List[SectionDraftResponse]
    total:  int