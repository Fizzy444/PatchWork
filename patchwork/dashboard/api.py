"""
Dashboard API endpoints for observability and job inspection.

These endpoints expose persisted pipeline data only. There is no simulation or
seed endpoint: jobs enter Patchwork through the signed GitHub webhook or the
real pipeline, never from the dashboard.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from patchwork.database import get_job, get_metrics_summary, list_jobs
from patchwork.models import JobResponse, JobStatus, MetricsSummary

router = APIRouter(prefix="/api/v1", tags=["Dashboard & Observability"])


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
