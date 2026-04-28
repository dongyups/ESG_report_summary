# Redis 연결 및 세션/캐시 관련 기능을 담당하는 파일

from redis.asyncio import Redis
from typing import Optional
from app.core.config import settings

class RedisClient:
    def __init__(self):
        self.redis = Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD,
            decode_responses=True
        )

    async def set_token(self, user_id: str, token: str, expire_seconds: int):
        key = f"token:{user_id}"
        await self.redis.setex(key, expire_seconds, token)

    async def get_token(self, user_id: str) -> Optional[str]:
        key = f"token:{user_id}"
        return await self.redis.get(key)

    async def delete_token(self, user_id: str):
        key = f"token:{user_id}"
        await self.redis.delete(key)

    async def get_ttl(self, user_id: str) -> int:
        key = f"token:{user_id}"
        return await self.redis.ttl(key)

    async def ping(self):
        return await self.redis.ping()

    async def close(self):
        await self.redis.close()


redis_client = RedisClient()

