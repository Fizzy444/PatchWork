"""
GitHub Webhook Handler: HMAC verification, event filtering,
and queuing of failed build jobs.
"""

import uuid
import logging
from typing import Dict, Any
from fastapi import APIRouter, Request, Header, HTTPException, status
from fastapi.responses import JSONResponse

from patchwork.config import settings
from patchwork.gateway.security import verify_github_signature
from patchwork.guardrails.rules import is_loop_branch
from patchwork.queue.broker import queue_broker, JobMessage
from patchwork.database import create_job_record

logger = logging.getLogger("patchwork.gateway.webhook")
router = APIRouter(prefix="/api/v1/webhook", tags=["Webhook"])


@router.post("")
async def handle_github_webhook(
    request: Request,
    x_github_event: str = Header(..., alias="X-GitHub-Event"),
    x_hub_signature_256: str = Header(None, alias="X-Hub-Signature-256"),
    x_github_delivery: str = Header(None, alias="X-GitHub-Delivery"),
):
    """
    Receives GitHub webhook events.
    Verifies HMAC signature, filters for failed workflow_run.completed,
    enforces infinite loop guardrails, and drops the job into the queue.
    """
    raw_body = await request.body()

    # 1. Verify HMAC Signature
    if settings.GITHUB_WEBHOOK_SECRET:
        if not verify_github_signature(raw_body, x_hub_signature_256, settings.GITHUB_WEBHOOK_SECRET):
            logger.warning("Rejected webhook delivery with invalid HMAC signature.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid X-Hub-Signature-256 header."
            )

    # 2. Handle ping event
    if x_github_event == "ping":
        logger.info("Received GitHub ping event. Connection verified.")
        return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "pong"})

    # 3. Only handle workflow_run events
    if x_github_event != "workflow_run":
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ignored", "reason": f"Event '{x_github_event}' is not workflow_run."}
        )

    try:
        payload: Dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body.")

    action = payload.get("action")
    workflow_run = payload.get("workflow_run", {})
    conclusion = workflow_run.get("conclusion")

    # 4. Filter for action == "completed" and conclusion == "failure"
    if action != "completed":
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ignored", "reason": f"Action '{action}' is not completed."}
        )

    if conclusion not in ("failure", "timed_out"):
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ignored", "reason": f"Workflow run conclusion '{conclusion}' is not a failure."}
        )

    # Extract workflow parameters
    repo_info = payload.get("repository", {})
    repo_name = repo_info.get("full_name", "unknown/repo")
    head_branch = workflow_run.get("head_branch", "main")
    head_sha = workflow_run.get("head_sha", "HEAD")
    workflow_name = workflow_run.get("name", "CI")
    run_id = workflow_run.get("id", 0)
    sender_login = payload.get("sender", {}).get("login")

    # -----------------------------------------------------------------
    # GUARDRAIL 2: Infinite Loop Prevention Check at Gateway Ingestion
    # -----------------------------------------------------------------
    if is_loop_branch(head_branch, sender_login):
        logger.warning(
            f"Ignored failed build on branch '{head_branch}' to prevent infinite self-triggering loop."
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "ignored",
                "guardrail": "INFINITE_LOOP_PREVENTION",
                "reason": f"Branch '{head_branch}' starts with AI fix prefix."
            }
        )

    # 5. Create Job Record in Database
    job_id = f"job-{uuid.uuid4().hex[:12]}"
    await create_job_record(
        job_id=job_id,
        repo_name=repo_name,
        branch_name=head_branch,
        commit_sha=head_sha,
        workflow_name=workflow_name,
        run_id=run_id
    )

    # 6. Push job message into the asynchronous queue
    installation_id = payload.get("installation", {}).get("id")
    extra_data = {"installation_id": installation_id} if installation_id else {}

    job_message = JobMessage(
        job_id=job_id,
        repo_name=repo_name,
        branch_name=head_branch,
        commit_sha=head_sha,
        workflow_name=workflow_name,
        run_id=run_id,
        extra=extra_data
    )
    await queue_broker.enqueue(job_message)

    logger.info(f"Enqueued healing job {job_id} for {repo_name}@{head_branch} (Run #{run_id})")

    # 7. Respond immediately (HTTP 202 Accepted)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "job_id": job_id,
            "repo": repo_name,
            "branch": head_branch,
            "run_id": run_id
        }
    )
