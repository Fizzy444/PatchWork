"""
Background Worker: Consumes failed build jobs from the queue broker
and executes the 6-stage self-healing pipeline.
"""

import asyncio
import logging
from typing import Optional

from patchwork.queue.broker import queue_broker, JobMessage
from patchwork.pipeline import pipeline_orchestrator

logger = logging.getLogger("patchwork.worker")


class PatchworkWorker:
    def __init__(self):
        self._running = False

    async def start(self) -> None:
        """Starts the worker polling loop."""
        self._running = True
        await queue_broker.connect()
        logger.info("Patchwork background worker started. Listening for failed build jobs...")

        while self._running:
            try:
                job_msg = await queue_broker.dequeue(timeout=2)
                if job_msg:
                    logger.info(f"Worker picked up job {job_msg.job_id} ({job_msg.repo_name} run #{job_msg.run_id})")
                    try:
                        await pipeline_orchestrator.execute_job(job_msg)
                    except Exception as job_err:
                        logger.error(f"Error processing job {job_msg.job_id}: {job_err}", exc_info=True)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker loop error: {e}")
                await asyncio.sleep(1)

    async def stop(self) -> None:
        """Gracefully stops the worker."""
        self._running = False
        await queue_broker.close()
        logger.info("Patchwork worker stopped.")

    async def process_all_pending(self) -> int:
        """Drains and processes all currently enqueued jobs during tests."""
        await queue_broker.connect()
        processed = 0
        while True:
            job_msg = await queue_broker.dequeue(timeout=1)
            if not job_msg:
                break
            await pipeline_orchestrator.execute_job(job_msg)
            processed += 1
        return processed


worker = PatchworkWorker()
