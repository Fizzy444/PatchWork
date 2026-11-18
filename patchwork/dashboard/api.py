"""
Dashboard API endpoints for observability and job inspection.

These endpoints expose persisted pipeline data only. There is no simulation or
seed endpoint: jobs enter Patchwork through the signed GitHub webhook or the
real pipeline, never from the dashboard.
"""

import asyncio
import json
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from patchwork.database import (
    get_job,
    get_job_events,
    get_metrics_summary,
    list_jobs,
    rerun_job,
)
from patchwork.models import JobEventSchema, JobResponse, JobStatus, MetricsSummary
from patchwork.realtime import event_broadcaster

router = APIRouter(prefix="/api/v1", tags=["Dashboard & Observability"])


@router.get("/events")
async def sse_event_stream(request: Request, once: bool = False):
    """
    Real-time Server-Sent Events (SSE) feed for live job creation, status updates,
    and step-by-step pipeline execution logs.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    event_broadcaster.subscribe(queue)

    async def event_generator():
        try:
            # Yield initial connection confirmation
            yield "event: connected\ndata: {}\n\n"
            if once:
                return

            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"event: {msg['event']}\ndata: {json.dumps(msg['data'])}\n\n"
                except asyncio.TimeoutError:
                    # Keepalive comment to prevent socket timeout
                    yield ": ping\n\n"
        finally:
            event_broadcaster.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/metrics", response_model=MetricsSummary)
async def fetch_metrics():
    """Return aggregated system metrics and the most recent jobs."""
    return await get_metrics_summary()


@router.get("/jobs")
async def fetch_jobs(
    status: Optional[str] = None,
    repo: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List persisted remediation jobs with optional status, repo, and text filters."""
    statuses = None
    if status:
        valid_statuses = {item.value for item in JobStatus}
        if status not in valid_statuses:
            raise HTTPException(status_code=400, detail="Unknown job status.")
        statuses = [status]

    items, total = await list_jobs(
        statuses=statuses,
        repo=(repo or "").strip() or None,
        search=(q or "").strip() or None,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [item.model_dump() for item in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job_details(job_id: str):
    """Return one job with its full sandbox verification attempt history."""
    job = await get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs/{job_id}/events", response_model=List[JobEventSchema])
async def fetch_job_events(job_id: str):
    """Return the complete execution event timeline for a specific job."""
    job = await get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    events = await get_job_events(job_id)
    return [JobEventSchema.model_validate(e) for e in events]


@router.post("/jobs/{job_id}/rerun")
async def trigger_job_rerun(job_id: str):
    """
    Manually re-run an existing remediation job from the dashboard.
    Resets job state and queues it for pipeline execution.
    """
    job = await rerun_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": "queued", "job_id": job.id, "message": "Job successfully queued for re-run."}
