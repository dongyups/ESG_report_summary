# 사용자(User) 데이터에 대한 생성/조회/수정/삭제(CRUD) 기능을 담당하는 파일

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
# local
from app.db.models.user import User
from app.core.security import get_password_hash, verify_password



async def get_user_by_username(db: AsyncSession, username: str) -> Optional[User]:
    """Get user by username"""
    result = await db.execute(select(User).filter(User.username == username))
    return result.scalars().first()


async def get_user_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
    """Get user by ID"""
    result = await db.execute(select(User).filter(User.id == user_id))
    return result.scalars().first()


async def create_user(db: AsyncSession, username: str, email: str, password: str, full_name: str = None) -> User:
    """Create new user"""
    hashed_password = get_password_hash(password)
    db_user = User(
        username=username,
        email=email,
        hashed_password=hashed_password,
        full_name=full_name
    )
    db.add(db_user)
    await db.commit()  # 비동기 커밋
    await db.refresh(db_user)  # 비동기 리프레시
    return db_user


async def authenticate_user(db: AsyncSession, username: str, password: str) -> Optional[User]:
    """Authenticate user with username and password"""
    user = await get_user_by_username(db, username)
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    if not user.is_active:
        return None
    return user

