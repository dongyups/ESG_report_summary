# section_drafts 테이블 — SECTION_GRAPH(HITL)에서 최종 승인된 섹션 초안을 영구 저장한다.
#
# SQLite 체크포인트(AsyncSqliteSaver)는 그래프 재개를 위한 실행 상태 저장소이므로
# 서버 재시작·파일 삭제 시 소멸할 수 있다. 완성된 초안은 별도 MySQL 테이블에 저장해
# 이력 조회·재활용이 가능하도록 한다.

import json
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship

from app.db.models.database import Base


class SectionDraft(Base):
    __tablename__ = "section_drafts"

    id            = Column(Integer, primary_key=True, index=True)
    thread_id     = Column(String(36), unique=True, nullable=False, index=True)  # LangGraph UUID
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    section_title = Column(String(200), nullable=False)
    target_year   = Column(String(10),  nullable=False)
    esg_category  = Column(String(5),   nullable=True)   # "E" | "S" | "G" | "I" | None
    draft         = Column(Text,        nullable=False)   # 최종 승인된 초안 본문
    sources_json  = Column(Text,        nullable=True)    # docs_to_sources() 결과 JSON 직렬화
    created_at    = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at    = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", backref="section_drafts")

    # ── 편의 프로퍼티 ──────────────────────────────
    @property
    def sources(self) -> List[Dict[str, Any]]:
        """sources_json을 역직렬화해서 반환한다."""
        if not self.sources_json:
            return []
        try:
            return json.loads(self.sources_json)
        except (json.JSONDecodeError, TypeError):
            return []

    @sources.setter
    def sources(self, value: Optional[List[Dict[str, Any]]]) -> None:
        """리스트를 JSON 문자열로 직렬화해 저장한다."""
        self.sources_json = json.dumps(value or [], ensure_ascii=False)