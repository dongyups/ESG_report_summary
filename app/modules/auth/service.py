# 로그인 처리 등 인증 관련 비즈니스 로직을 담당하는 파일

from sqlalchemy.orm import Session
from datetime import timedelta
from typing import Optional
# local
from app.db.crud.user import authenticate_user, get_user_by_id
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.core.redis import redis_client
from app.core.config import settings
from app.schemas.user import TokenResponse, UserResponse

class AuthService:
    @staticmethod
    async def login(db: Session, username: str, password: str) -> Optional[TokenResponse]:
        """Login user and create tokens"""
        # Authenticate user
        user = await authenticate_user(db, username, password)
        if not user:
            return None
        
        # Create tokens
        token_data = {"sub": str(user.id), "username": user.username}
        access_token = create_access_token(token_data)
        refresh_token = create_refresh_token(token_data)
        
        # Store access token in Redis with 24-hour expiration
        expire_seconds = settings.JWT_ACCESS_TOKEN_EXPIRE_HOURS * 3600
        await redis_client.set_token(str(user.id), access_token, expire_seconds)

        # Return token response
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user=UserResponse.model_validate(user)
        )
    
    @staticmethod
    async def verify_token(token: str) -> Optional[dict]:
        """Verify access token"""
        payload = decode_token(token)
        if not payload:
            return None
        
        # Check if token is in Redis
        user_id = payload.get("sub")
        stored_token = await redis_client.get_token(user_id)
        if not stored_token or stored_token != token:
            return None

        return payload
    
    @staticmethod
    async def get_token_ttl(user_id: str) -> int:
        """Get remaining TTL for user's token"""
        return await redis_client.get_ttl(user_id)
    
    @staticmethod
    async def logout(user_id: str):
        """Logout user by deleting token from Redis"""
        await redis_client.delete_token(user_id)