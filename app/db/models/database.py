# SQLAlchemy 모델들이 상속받는 Base 객체를 정의하는 파일
# 데이터베이스 연결(engine)과 세션(Session)을 생성하고 관리하는 파일

import asyncio
from fastapi import FastAPI
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from sqlalchemy.orm import declarative_base
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
# local
from app.core.redis import redis_client
from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False
)

Base = declarative_base()

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


# DB, Redis 연결
@asynccontextmanager
async def lifespan(app: FastAPI):
    # DB 없는경우 만들기
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # yield
    # await engine.dispose()


    # DB 연결
    for i in range(10):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            print("DB 준비완료")
            break
        except Exception as e:
            print(f"DB 연결 실패 ({i+1}/10): {e}")
            await asyncio.sleep(2)
    else:
        raise RuntimeError("DB 연결 실패 - 서버 종료")

    # Redis 연결
    for i in range(10):
        try:
            await redis_client.ping()
            print("Redis 준비완료")
            break
        except Exception as e:
            print(f"Redis 연결 실패 ({i+1}/10): {e}")
            await asyncio.sleep(2)
    else:
        raise RuntimeError("Redis 연결 실패 - 서버 종료")

    # 연결됨
    yield

    # 종료
    await redis_client.close()
    await engine.dispose()



