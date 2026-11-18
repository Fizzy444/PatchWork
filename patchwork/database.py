"""
Database session and persistence management.
"""

from typing import AsyncGenerator, Dict, List, Optional, Tuple
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select, func, desc, or_, delete

from patchwork.config import settings
from patchwork.realtime import event_broadcaster
from patchwork.models import (
    Base,
    HealingJobRecord,
    PatchAttemptRecord,
    JobEventRecord,
    JobStatus,
    MetricsSummary,
    JobResponse,
    PatchAttemptSchema,
    JobEventSchema,
)


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    future=True
)

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)


async def init_db() -> None:
    """Create all database tables if they do not exist, and apply column migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        def _migrate_columns(sync_conn):
            from sqlalchemy import text
            try:
                # Check for SQLite table schema
                res = sync_conn.execute(text("PRAGMA table_info(healing_jobs)"))
                columns = {row[1] for row in res.fetchall()}
                if columns and "installation_id" not in columns:
                    sync_conn.execute(text("ALTER TABLE healing_jobs ADD COLUMN installation_id INTEGER"))
            except Exception:
                pass

        await conn.run_sync(_migrate_columns)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for providing database sessions."""
    async with async_session() as session:
        yield session


async def create_job_record(
    job_id: str,
    repo_name: str,
    branch_name: str,
    commit_sha: str,
    workflow_name: str,
    run_id: int,
    installation_id: Optional[int] = None
) -> HealingJobRecord:
    """Create a new job record with QUEUED status or reset existing."""
    async with async_session() as session:
        result = await session.execute(
            select(HealingJobRecord).where(HealingJobRecord.id == job_id)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.status = JobStatus.QUEUED.value
            existing.repo_name = repo_name
            existing.branch_name = branch_name
            existing.commit_sha = commit_sha
            existing.workflow_name = workflow_name
            existing.run_id = run_id
            existing.installation_id = installation_id or existing.installation_id
            existing.attempts_count = 0
            existing.guardrail_violation = None
            existing.pull_request_url = None
            existing.diff_patch = None
            await session.commit()
            await session.refresh(existing)
            job = existing
        else:
            job = HealingJobRecord(
                id=job_id,
                repo_name=repo_name,
                branch_name=branch_name,
                commit_sha=commit_sha,
                workflow_name=workflow_name,
                run_id=run_id,
                installation_id=installation_id,
                status=JobStatus.QUEUED.value,
                created_at=datetime.utcnow()
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)

    # Broadcast new job creation to connected dashboard clients
    await event_broadcaster.broadcast(
        "job_created",
        {
            "id": job.id,
            "repo_name": job.repo_name,
            "branch_name": job.branch_name,
            "commit_sha": job.commit_sha,
            "workflow_name": job.workflow_name,
            "run_id": job.run_id,
            "status": job.status,
            "created_at": job.created_at.isoformat()
        }
    )
    return job


async def update_job_status(
    job_id: str,
    status: JobStatus,
    failing_file: Optional[str] = None,
    failing_line: Optional[int] = None,
    error_summary: Optional[str] = None,
    root_cause: Optional[str] = None,
    diff_patch: Optional[str] = None,
    pull_request_url: Optional[str] = None,
    pull_request_number: Optional[int] = None,
    guardrail_violation: Optional[str] = None,
    tokens_used: Optional[int] = None,
    duration_seconds: Optional[float] = None
) -> Optional[HealingJobRecord]:
    """Update job fields as it moves through the 6-stage pipeline."""
    async with async_session() as session:
        result = await session.execute(
            select(HealingJobRecord).where(HealingJobRecord.id == job_id)
        )
        job = result.scalar_one_or_none()
        if not job:
            return None

        job.status = status.value
        if failing_file is not None:
            job.failing_file = failing_file
        if failing_line is not None:
            job.failing_line = failing_line
        if error_summary is not None:
            job.error_summary = error_summary
        if root_cause is not None:
            job.root_cause = root_cause
        if diff_patch is not None:
            job.diff_patch = diff_patch
        if pull_request_url is not None:
            job.pull_request_url = pull_request_url
        if pull_request_number is not None:
            job.pull_request_number = pull_request_number
        if guardrail_violation is not None:
            job.guardrail_violation = guardrail_violation
        if tokens_used is not None:
            job.tokens_used += tokens_used
        if duration_seconds is not None:
            job.duration_seconds = duration_seconds

        await session.commit()
        await session.refresh(job)

    # Broadcast updated job status to connected clients
    await event_broadcaster.broadcast(
        "job_status",
        {
            "id": job.id,
            "status": job.status,
            "failing_file": job.failing_file,
            "failing_line": job.failing_line,
            "error_summary": job.error_summary,
            "root_cause": job.root_cause,
            "diff_patch": job.diff_patch,
            "pull_request_url": job.pull_request_url,
            "pull_request_number": job.pull_request_number,
            "guardrail_violation": job.guardrail_violation,
            "duration_seconds": round(job.duration_seconds or 0.0, 2),
            "attempts_count": job.attempts_count,
        }
    )
    return job


async def record_patch_attempt(
    job_id: str,
    attempt_number: int,
    patch_diff: str,
    test_output: str,
    passed: bool
) -> PatchAttemptRecord:
    """Record a test execution attempt in the sandbox loop."""
    async with async_session() as session:
        attempt = PatchAttemptRecord(
            id=f"{job_id}-attempt-{attempt_number}",
            job_id=job_id,
            attempt_number=attempt_number,
            patch_diff=patch_diff,
            test_output=test_output,
            passed=passed,
            created_at=datetime.utcnow()
        )
        session.add(attempt)

        # Update attempt count on parent job
        result = await session.execute(
            select(HealingJobRecord).where(HealingJobRecord.id == job_id)
        )
        job = result.scalar_one_or_none()
        if job:
            job.attempts_count = attempt_number
            if passed:
                job.diff_patch = patch_diff

        await session.commit()

    # Broadcast attempt result to connected clients
    await event_broadcaster.broadcast(
        "patch_attempt",
        {
            "job_id": job_id,
            "attempt_number": attempt_number,
            "patch_diff": patch_diff,
            "test_output": test_output,
            "passed": passed,
            "created_at": attempt.created_at.isoformat()
        }
    )
    return attempt


async def get_metrics_summary() -> MetricsSummary:
    """Calculate aggregated metrics for the Observability Dashboard."""
    async with async_session() as session:
        # Total intercepted
        total_res = await session.execute(select(func.count(HealingJobRecord.id)))
        total_intercepted = total_res.scalar() or 0

        # Total successfully healed (PR opened)
        healed_res = await session.execute(
            select(func.count(HealingJobRecord.id)).where(
                HealingJobRecord.status == JobStatus.PR_OPENED.value
            )
        )
        total_healed = healed_res.scalar() or 0

        # Flaky re-runs
        flaky_res = await session.execute(
            select(func.count(HealingJobRecord.id)).where(
                HealingJobRecord.status == JobStatus.FLAKY_RERUN.value
            )
        )
        flaky_runs_rerun = flaky_res.scalar() or 0

        # Guardrails prevented
        guardrails_res = await session.execute(
            select(func.count(HealingJobRecord.id)).where(
                HealingJobRecord.status == JobStatus.GUARDRAIL_REJECTED.value
            )
        )
        guardrails_prevented = guardrails_res.scalar() or 0

        # Avg healing time for healed jobs
        avg_time_res = await session.execute(
            select(func.avg(HealingJobRecord.duration_seconds)).where(
                HealingJobRecord.status == JobStatus.PR_OPENED.value
            )
        )
        avg_healing_time = avg_time_res.scalar() or 0.0

        # Total tokens used
        tokens_res = await session.execute(select(func.sum(HealingJobRecord.tokens_used)))
        total_tokens = tokens_res.scalar() or 0

        success_rate = (total_healed / total_intercepted * 100.0) if total_intercepted > 0 else 0.0

        # Recent jobs
        recent_res = await session.execute(
            select(HealingJobRecord).order_by(desc(HealingJobRecord.created_at)).limit(15)
        )
        recent_records = recent_res.scalars().all()

        recent_jobs = [
            _record_to_response(r)
            for r in recent_records
        ]

        return MetricsSummary(
            total_intercepted=total_intercepted,
            total_healed=total_healed,
            success_rate_percentage=round(success_rate, 1),
            average_healing_time_seconds=round(avg_healing_time, 1),
            total_tokens_used=total_tokens,
            flaky_runs_rerun=flaky_runs_rerun,
            guardrails_prevented=guardrails_prevented,
            recent_jobs=recent_jobs
        )


async def record_job_event(
    job_id: str,
    stage: str,
    message: str,
    level: str = "info",
    details: Optional[str] = None
) -> JobEventRecord:
    """Record a real-time execution event or log entry for a remediation job."""
    async with async_session() as session:
        event = JobEventRecord(
            job_id=job_id,
            stage=stage,
            level=level,
            message=message,
            details=details,
            created_at=datetime.utcnow()
        )
        session.add(event)
        await session.commit()
        await session.refresh(event)

    # Broadcast event to active dashboard clients
    await event_broadcaster.broadcast(
        "job_event",
        {
            "id": event.id,
            "job_id": job_id,
            "stage": stage,
            "level": level,
            "message": message,
            "details": details,
            "created_at": event.created_at.isoformat()
        }
    )
    return event


async def get_job_events(job_id: str) -> List[JobEventRecord]:
    """Retrieve full chronological execution events for a job."""
    async with async_session() as session:
        res = await session.execute(
            select(JobEventRecord)
            .where(JobEventRecord.job_id == job_id)
            .order_by(JobEventRecord.id.asc())
        )
        return list(res.scalars().all())


async def rerun_job(job_id: str) -> Optional[HealingJobRecord]:
    """
    Resets a job's diagnosis, diff, and attempts, then re-enqueues it
    into the remediation pipeline queue.
    """
    from patchwork.queue.broker import queue_broker, JobMessage

    async with async_session() as session:
        result = await session.execute(
            select(HealingJobRecord).where(HealingJobRecord.id == job_id)
        )
        job = result.scalar_one_or_none()
        if not job:
            return None

        # Reset fields
        job.status = JobStatus.QUEUED.value
        job.failing_file = None
        job.failing_line = None
        job.error_summary = None
        job.root_cause = None
        job.diff_patch = None
        job.attempts_count = 0
        job.pull_request_url = None
        job.pull_request_number = None
        job.pr_merged = False
        job.guardrail_violation = None
        job.duration_seconds = 0.0
        job.updated_at = datetime.utcnow()

        # Clear prior patch attempts and events so execution logs reflect fresh run
        await session.execute(
            delete(PatchAttemptRecord).where(PatchAttemptRecord.job_id == job_id)
        )
        await session.execute(
            delete(JobEventRecord).where(JobEventRecord.job_id == job_id)
        )
        await session.commit()
        await session.refresh(job)

    # Initial event for re-run
    await record_job_event(
        job_id=job.id,
        stage="queued",
        level="info",
        message=f"Manual re-run requested by user for {job.repo_name} run #{job.run_id}."
    )

    # Broadcast updated status
    await event_broadcaster.broadcast(
        "job_status",
        {
            "id": job.id,
            "status": JobStatus.QUEUED.value,
            "attempts_count": 0,
            "duration_seconds": 0.0,
            "pull_request_url": None,
            "diff_patch": None,
            "root_cause": None,
        }
    )

    # Enqueue job message into the broker
    extra_data = {}
    if job.installation_id:
        extra_data["installation_id"] = job.installation_id

    job_msg = JobMessage(
        job_id=job.id,
        repo_name=job.repo_name,
        branch_name=job.branch_name,
        commit_sha=job.commit_sha,
        workflow_name=job.workflow_name,
        run_id=job.run_id,
        extra=extra_data
    )
    await queue_broker.enqueue(job_msg)
    return job


def _record_to_response(
    record: HealingJobRecord,
    attempts: Optional[List[PatchAttemptRecord]] = None,
    events: Optional[List[JobEventRecord]] = None,
) -> JobResponse:
    """Serialize a job row (and optional attempts and events) for API and page rendering."""
    return JobResponse(
        id=record.id,
        repo_name=record.repo_name,
        branch_name=record.branch_name,
        commit_sha=record.commit_sha,
        workflow_name=record.workflow_name,
        run_id=record.run_id,
        installation_id=record.installation_id,
        status=record.status,
        failing_file=record.failing_file,
        failing_line=record.failing_line,
        error_summary=record.error_summary,
        root_cause=record.root_cause,
        diff_patch=record.diff_patch,
        attempts_count=record.attempts_count,
        pull_request_url=record.pull_request_url,
        pull_request_number=record.pull_request_number,
        pr_merged=record.pr_merged,
        guardrail_violation=record.guardrail_violation,
        tokens_used=record.tokens_used,
        duration_seconds=round(record.duration_seconds or 0.0, 2),
        created_at=record.created_at,
        attempts=[
            PatchAttemptSchema(
                attempt_number=a.attempt_number,
                patch_diff=a.patch_diff,
                test_output=a.test_output,
                passed=a.passed,
                created_at=a.created_at,
            )
            for a in (attempts or [])
        ],
        events=[
            JobEventSchema(
                id=e.id,
                job_id=e.job_id,
                stage=e.stage,
                level=e.level,
                message=e.message,
                details=e.details,
                created_at=e.created_at,
            )
            for e in (events or [])
        ],
    )


async def list_jobs(
    statuses: Optional[List[str]] = None,
    repo: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 25,
    offset: int = 0,
) -> Tuple[List[JobResponse], int]:
    """Return a filtered, paginated list of remediation jobs and the total count."""
    async with async_session() as session:
        filters = []
        if statuses:
            filters.append(HealingJobRecord.status.in_(statuses))
        if repo:
            filters.append(HealingJobRecord.repo_name == repo)
        if search and search.strip():
            pattern = f"%{search.strip()}%"
            filters.append(
                or_(
                    HealingJobRecord.repo_name.ilike(pattern),
                    HealingJobRecord.branch_name.ilike(pattern),
                    HealingJobRecord.id.ilike(pattern),
                    HealingJobRecord.failing_file.ilike(pattern),
                )
            )

        base = select(HealingJobRecord)
        if filters:
            base = base.where(*filters)

        total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0

        result = await session.execute(
            base.order_by(desc(HealingJobRecord.created_at)).limit(limit).offset(offset)
        )
        records = result.scalars().all()
        return [_record_to_response(r) for r in records], int(total)


async def get_job(job_id: str) -> Optional[JobResponse]:
    """Fetch one job with its full sandbox attempt history and execution events."""
    async with async_session() as session:
        result = await session.execute(
            select(HealingJobRecord).where(HealingJobRecord.id == job_id)
        )
        record = result.scalar_one_or_none()
        if not record:
            return None

        attempts_result = await session.execute(
            select(PatchAttemptRecord)
            .where(PatchAttemptRecord.job_id == job_id)
            .order_by(PatchAttemptRecord.attempt_number)
        )
        attempts = attempts_result.scalars().all()

        events_result = await session.execute(
            select(JobEventRecord)
            .where(JobEventRecord.job_id == job_id)
            .order_by(JobEventRecord.id.asc())
        )
        events = events_result.scalars().all()

        return _record_to_response(record, attempts, events)


async def get_status_counts() -> Dict[str, int]:
    """Count jobs grouped by their pipeline status."""
    async with async_session() as session:
        result = await session.execute(
            select(HealingJobRecord.status, func.count(HealingJobRecord.id))
            .group_by(HealingJobRecord.status)
        )
        return {status: count for status, count in result.all()}


async def get_repositories() -> List[Tuple[str, int]]:
    """List repositories that have at least one recorded job, with job counts."""
    async with async_session() as session:
        result = await session.execute(
            select(HealingJobRecord.repo_name, func.count(HealingJobRecord.id))
            .group_by(HealingJobRecord.repo_name)
            .order_by(desc(func.count(HealingJobRecord.id)))
        )
        return [(repo_name, count) for repo_name, count in result.all()]
