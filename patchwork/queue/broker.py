"""
Asynchronous Task Queue Broker (Redis with async In-Memory fallback).
"""

import json
import asyncio
import logging
from typing import Optional, Dict, Any
from pydantic import BaseModel
import redis.asyncio as aioredis

from patchwork.config import settings

logger = logging.getLogger("patchwork.queue")


class JobMessage(BaseModel):
    job_id: str
    repo_name: str
    branch_name: str
    commit_sha: str
    workflow_name: str
    run_id: int
    attempt_count: int = 0
    extra: Dict[str, Any] = {}


class TaskQueueBroker:
    def __init__(self, redis_url: str = settings.REDIS_URL, queue_name: str = "patchwork:jobs"):
        self.redis_url = redis_url
        self.queue_name = queue_name
        self.redis_client: Optional[aioredis.Redis] = None
        self._memory_queue: asyncio.Queue[str] = asyncio.Queue()
        self._using_memory_fallback = False

    async def connect(self) -> None:
        """Attempt to connect to Redis, fall back to asyncio.Queue if unreachable."""
        url_lower = (self.redis_url or "").strip().lower()
        if not url_lower or url_lower in ("memory", "disabled", "none", "false", ""):
            self._using_memory_fallback = True
            logger.info("Using built-in in-memory async task queue.")
            return

        try:
            client = aioredis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=0.4
            )
            await client.ping()
            self.redis_client = client
            self._using_memory_fallback = False
            logger.info(f"Connected to Redis at {self.redis_url}")
        except Exception as e:
            self._using_memory_fallback = True
            logger.info(f"Redis not detected on {self.redis_url}; using built-in async in-memory queue.")

    async def enqueue(self, message: JobMessage) -> None:
        """Add job message to queue."""
        payload = message.model_dump_json()
        if self.redis_client and not self._using_memory_fallback:
            try:
                await self.redis_client.rpush(self.queue_name, payload)
                return
            except Exception as e:
                logger.error(f"Redis push failed, falling back to in-memory queue: {e}")
                self._using_memory_fallback = True

        await self._memory_queue.put(payload)

    async def dequeue(self, timeout: int = 5) -> Optional[JobMessage]:
        """Pop a job message from queue with timeout."""
        if self.redis_client and not self._using_memory_fallback:
            try:
                item = await self.redis_client.blpop(self.queue_name, timeout=timeout)
                if item:
                    _, payload = item
                    return JobMessage.model_validate_json(payload)
            except Exception as e:
                logger.error(f"Redis pop failed, falling back to in-memory queue: {e}")
                self._using_memory_fallback = True

        try:
            payload = await asyncio.wait_for(self._memory_queue.get(), timeout=float(timeout))
            return JobMessage.model_validate_json(payload)
        except asyncio.TimeoutError:
            return None

    async def length(self) -> int:
        """Get number of pending jobs in queue."""
        if self.redis_client and not self._using_memory_fallback:
            try:
                return await self.redis_client.llen(self.queue_name)
            except Exception:
                pass
        return self._memory_queue.qsize()

    async def close(self) -> None:
        """Close connections."""
        if self.redis_client:
            await self.redis_client.aclose()


# Global broker singleton
queue_broker = TaskQueueBroker()
