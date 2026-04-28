# 현재 사용자 인증 확인 등 FastAPI 의존성(Dependency)을 정의하는 파일


from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from typing import Optional
# local
from app.db.models.database import get_db
from app.db.crud.user import get_user_by_id
from app.db.models.user import User
from app.modules.auth.service import AuthService

security = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """
    Get current authenticated user from token
    """
    token = credentials.credentials
    
    # Verify token
    payload = await AuthService.verify_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증이 유효하지 않습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Get user from database
    user_id = payload.get("sub")
    user = await get_user_by_id(db, int(user_id))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="사용자를 찾을 수 없습니다."
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="비활성화된 사용자입니다."
        )
    
    return user

async def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Get current active user
    """
    return current_user