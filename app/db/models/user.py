# 사용자(User) 테이블 구조를 정의하는 SQLAlchemy 모델

from sqlalchemy import Column, Integer, String, DateTime, Boolean, func
# local
from app.db.models.database import Base

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(100))
    is_active = Column(Boolean, default=True) # status = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_onupdate=func.now()) # onupdate

