# 애플리케이션 전역 설정을 관리하는 파일 (환경변수, 설정값 등)

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


# 루트폴더 위치
BASE_DIR = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    # Global
    model_config = SettingsConfigDict(
        env_file=(
            BASE_DIR / ".env.development",
            BASE_DIR / ".env.production",
        ),
        env_file_encoding="utf-8",
        # 파일이 없어도 에러를 내지 않고 다음 단계(시스템 환경 변수)로 넘어가게 합니다.
        env_file_ignore_missing=True, 
        extra="ignore"
    )
    # Application
    APP_NAME: str = "ESG Summary Platform"
    # DEBUG: bool = True

    # Database
    DB_NAME: str
    DB_USER: str
    DB_PWD: str
    DB_IP: str
    DB_PORT: str
    @property # 객체처럼 사용하기 위함
    def DATABASE_URL(self) -> str:
        return f"mysql+aiomysql://{self.DB_USER}:{self.DB_PWD}@{self.DB_IP}:{self.DB_PORT}/{self.DB_NAME}"

    # Redis
    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_DB: int
    REDIS_PASSWORD: Optional[str] = None
    
    # Security
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str
    JWT_ACCESS_TOKEN_EXPIRE_HOURS: int
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int

    # LLM, Web-search
    LLM_MODEL: str
    ANTHROPIC_API_KEY: str
    TAVILY_API_KEY: str

# 로드할 부분
settings = Settings()