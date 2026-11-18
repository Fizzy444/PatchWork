"""
Real-time Server-Sent Events (SSE) broadcaster for Patchwork.
Publishes job status changes, timeline events, and logs to connected browser clients.
"""

import asyncio
import logging
from typing import Any, Dict, Set

logger = logging.getLogger("patchwork.realtime")


class EventBroadcaster:
    def __init__(self):
        self._subscribers: Set[asyncio.Queue] = set()

    def subscribe(self, queue: asyncio.Queue) -> None:
        """Register a client queue for event broadcasts."""
        self._subscribers.add(queue)
        logger.debug(f"Client connected to SSE stream. Total clients: {len(self._subscribers)}")

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Remove a client queue when disconnected."""
        self._subscribers.discard(queue)
        logger.debug(f"Client disconnected from SSE stream. Total clients: {len(self._subscribers)}")

    async def broadcast(self, event_type: str, data: Any) -> None:
        """Broadcast an event to all connected subscriber queues."""
        if not self._subscribers:
            return

        message = {"event": event_type, "data": data}
        dead_queues = []

        for q in list(self._subscribers):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                logger.warning("Subscriber queue is full; dropping event for subscriber.")
            except Exception:
                dead_queues.append(q)

        for dead in dead_queues:
            self._subscribers.discard(dead)


event_broadcaster = EventBroadcaster()
