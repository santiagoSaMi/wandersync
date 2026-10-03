"""Rate limiting por ventana deslizante sobre Redis (sorted set). Compartido entre replicas."""
from __future__ import annotations

import time
import uuid

import redis.asyncio as aioredis


class RateLimiter:
    def __init__(self, url: str):
        self.r = aioredis.from_url(url, decode_responses=True)

    async def hit(self, bucket: str, identifier: str, limit: int, window_s: int) -> tuple[bool, int]:
        """Registra un intento. Devuelve (permitido, segundos_para_reintentar)."""
        key = f"rl:{bucket}:{identifier}"
        now = time.time()
        pipe = self.r.pipeline(transaction=True)
        pipe.zremrangebyscore(key, 0, now - window_s)
        pipe.zadd(key, {f"{now}:{uuid.uuid4().hex[:8]}": now})
        pipe.zcard(key)
        pipe.expire(key, window_s)
        _, _, count, _ = await pipe.execute()
        if count <= limit:
            return True, 0
        oldest = await self.r.zrange(key, 0, 0, withscores=True)
        retry = int(window_s - (now - oldest[0][1])) + 1 if oldest else window_s
        return False, max(retry, 1)

    async def close(self) -> None:
        await self.r.aclose()
