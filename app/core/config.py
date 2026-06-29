# 애플리케이션 전역 설정을 관리하는 파일 (환경변수, 설정값 등)

import os
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

    # LLM, Web-search, RAG
    ### 개인 API키 ###
    # ANTHROPIC_API_KEY: str
    ### aws bedrock ###
    BEDROCK_API_KEY: str
    AWS_REGION: str
    TAVILY_API_KEY: str
    LLM_MODEL: str
    RAG_LLM_MODEL: str

    # RAG - ChromaDB (로컬 persistent)
    CHROMA_NAME: str
    @property # 객체처럼 사용하기 위함
    def CHROMA_PATH(self) -> str:
        return str(BASE_DIR / self.CHROMA_NAME)

    # RAG - 섹션 작성(HITL) LangGraph 체크포인터 DB
    # 기존 .env에 없는 환경에서도 깨지지 않도록 기본값을 둔다.
    SECTION_CHECKPOINT_NAME: str = "datasets/section_checkpoints.db"
    @property # 객체처럼 사용하기 위함
    def SECTION_CHECKPOINT_PATH(self) -> str:
        return str(BASE_DIR / self.SECTION_CHECKPOINT_NAME)

    # RAG - Ollama (로컬)
    OLLAMA_HOST: str
    OLLAMA_PORT: str
    @property # 객체처럼 사용하기 위함
    def OLLAMA_BASE_URL(self) -> str:
        return f"http://{self.OLLAMA_HOST}:{self.OLLAMA_PORT}"    
    OLLAMA_EMBED_MODEL: str

# 로드할 부분
settings = Settings()
os.environ["AWS_BEARER_TOKEN_BEDROCK"] = settings.BEDROCK_API_KEY