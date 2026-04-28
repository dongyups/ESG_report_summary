# 사용자 요청/응답 데이터 구조(Pydantic 스키마)를 정의하는 파일

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class UserCreate(BaseModel):
    # UserBase를 만들고 공통된 변수 사용 가능, Field(...)
    username: str
    email: str # EmailStr
    password: str
    full_name: Optional[str]


class UserLogin(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    full_name: Optional[str]
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse