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
        self._active_tasks: dict[str, asyncio.Task] = {}

    def cancel_job(self, job_id: str) -> bool:
        """Cancels an in-flight job task if currently running."""
        task = self._active_tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            logger.info(f"Cancellation requested for in-flight job {job_id}")
            return True
        return False

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
                    task = asyncio.create_task(pipeline_orchestrator.execute_job(job_msg))
                    self._active_tasks[job_msg.job_id] = task
                    try:
                        await task
                    except asyncio.CancelledError:
                        logger.info(f"Job {job_msg.job_id} was cancelled during execution.")
                    except Exception as job_err:
                        logger.error(f"Error processing job {job_msg.job_id}: {job_err}", exc_info=True)
                    finally:
                        self._active_tasks.pop(job_msg.job_id, None)
            except asyncio.CancelledError:
                for tid, t in list(self._active_tasks.items()):
                    if not t.done():
                        t.cancel()
                break
            except Exception as e:
                logger.error(f"Worker loop error: {e}")
                await asyncio.sleep(1)

    async def stop(self) -> None:
        """Gracefully stops the worker."""
        self._running = False
        for tid, t in list(self._active_tasks.items()):
            if not t.done():
                t.cancel()
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
            task = asyncio.create_task(pipeline_orchestrator.execute_job(job_msg))
            self._active_tasks[job_msg.job_id] = task
            try:
                await task
            except asyncio.CancelledError:
                logger.info(f"Job {job_msg.job_id} was cancelled during test execution.")
            finally:
                self._active_tasks.pop(job_msg.job_id, None)
            processed += 1
        return processed


worker = PatchworkWorker()
