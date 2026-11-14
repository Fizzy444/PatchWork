"""
Full End-to-End Pipeline Integration Test.
"""

import uuid
import pytest
from patchwork.database import init_db, get_metrics_summary, create_job_record
from patchwork.queue.broker import JobMessage
from patchwork.pipeline import pipeline_orchestrator
from patchwork.models import JobStatus


@pytest.mark.asyncio
async def test_full_pipeline_heals_bug_and_opens_pr():
    await init_db()

    repo_files = {
        "math_service.py": """def divide(a: int, b: int) -> int:
    return a / b
""",
        "tests/test_math_service.py": """from math_service import divide

def test_divide():
    assert divide(10, 2) == 5
    assert divide(10, 0) == 0
"""
    }

    job_id = f"test-e2e-{uuid.uuid4().hex[:8]}"
    job_msg = JobMessage(
        job_id=job_id,
        repo_name="test-org/payment-service",
        branch_name="feature/fast-checkout",
        commit_sha="c0ffee123456",
        workflow_name="CI Test Suite",
        run_id=5555
    )

    await create_job_record(
        job_id=job_id,
        repo_name=job_msg.repo_name,
        branch_name=job_msg.branch_name,
        commit_sha=job_msg.commit_sha,
        workflow_name=job_msg.workflow_name,
        run_id=job_msg.run_id
    )

    # Execute full pipeline
    success = await pipeline_orchestrator.execute_job(job_msg, local_repo_files=repo_files)

    assert success is True

    # Verify metrics updated
    metrics = await get_metrics_summary()
    assert metrics.total_intercepted >= 1
    assert metrics.total_healed >= 1
    assert metrics.success_rate_percentage > 0.0

    recent = [j for j in metrics.recent_jobs if j.id == job_id]
    assert len(recent) == 1
    assert recent[0].status == JobStatus.PR_OPENED.value
    assert recent[0].pull_request_url is not None
    assert "https://github.com/test-org/payment-service/pull/" in recent[0].pull_request_url
    assert "divide" in recent[0].failing_file or "math_service" in recent[0].failing_file
