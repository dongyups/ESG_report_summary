# 로그인, 회원 관련 API 엔드포인트를 정의하는 파일


from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
# local
from app.db.models.database import get_db
from app.schemas.user import UserLogin, UserCreate, UserResponse, TokenResponse
from app.modules.auth.service import AuthService
from app.modules.auth.dependency import get_current_user
from app.db.models.user import User
from app.db.crud.user import create_user


router = APIRouter()

@router.post("/login", response_model=TokenResponse)
async def login(
    user_data: UserLogin,
    db: Session = Depends(get_db)
):
    """
    Login endpoint
    Returns access token, refresh token, and user information
    """
    result = await AuthService.login(db, user_data.username, user_data.password)
    
    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="아이디 또는 비밀번호가 올바르지 않습니다."
        )
    return result


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    user_data: UserCreate,
    db: Session = Depends(get_db)
):
    """
    Register new user
    Returns user information
    """
    # Check if username already exists
    from app.db.crud.user import get_user_by_username
    existing_user = await get_user_by_username(db, user_data.username)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="이미 존재하는 사용자명입니다."
        )
    
    # Create new user
    new_user = await create_user(
        db=db,
        username=user_data.username,
        email=user_data.email,
        password=user_data.password,
        full_name=user_data.full_name
    )
    
    return new_user


### logout — user_id 파라미터 제거, 토큰에서 추출 ###
@router.post("/logout")
async def logout(current_user: User = Depends(get_current_user)):
    """
    Logout endpoint
    Removes token from Redis
    """
    await AuthService.logout(str(current_user.id))
    return {"message": "로그아웃되었습니다."}


### token-ttl — 경로의 {user_id} 제거, 본인 토큰만 조회 ###
@router.get("/token-ttl")
async def get_token_ttl(current_user: User = Depends(get_current_user)):
    """
    Get remaining time for access token
    Returns TTL in seconds
    """
    ttl = await AuthService.get_token_ttl(str(current_user.id))
    if ttl < 0:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="토큰이 만료되었습니다."
        )
    return {"ttl": ttl}