# RAG 대화(newsroom, press, report) 테이블 구조를 정의하는 SQLAlchemy 모델
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func, Text
from sqlalchemy.orm import relationship
# local
from app.db.models.database import Base


class RagConversation(Base):
    __tablename__ = "rag_conversations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(200), default="새 RAG 채팅")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    messages = relationship("RagMessage", back_populates="conversation", cascade="all, delete-orphan")


class RagMessage(Base):
    __tablename__ = "rag_messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("rag_conversations.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)         # "user" | "assistant"
    content = Column(Text, nullable=False)
    thinking = Column(Text, nullable=True)            # Claude extended thinking 원문
    sources = Column(Text, nullable=True)             # JSON 직렬화된 출처 목록
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    conversation = relationship("RagConversation", back_populates="messages")
