"""
Server-rendered dashboard pages.

The app is split into focused views instead of one overcrowded dashboard:
Overview, Jobs, Job detail, Guardrails, and Settings.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from patchwork.auth import SESSION_COOKIE, set_reveal, verify_password
from patchwork.config import settings
from patchwork.dashboard.templating import render_page, status_meta
from patchwork.database import (
    get_job,
    get_metrics_summary,
    get_repositories,
    get_status_counts,
    list_jobs,
)
from patchwork.guardrails.flaky import FLAKY_PATTERNS
from patchwork.models import JobStatus
from patchwork.settings_store import save_form, settings_view
from patchwork.settings_store import webhook_url as public_webhook_url
from patchwork.smee_runner import get_smee_status

router = APIRouter(tags=["Dashboard UI"])

PER_PAGE = 25
ACTIVE_STATUS_VALUES = [
    JobStatus.QUEUED.value,
    JobStatus.EXTRACTING.value,
    JobStatus.DIAGNOSING.value,
    JobStatus.VERIFYING.value,
]

JOB_TABS = [
    {"key": "all", "label": "All", "statuses": None},
    {"key": "active", "label": "In progress", "statuses": ACTIVE_STATUS_VALUES},
    {"key": "opened", "label": "PR opened", "statuses": [JobStatus.PR_OPENED.value]},
    {"key": "guardrails", "label": "Guardrail blocked", "statuses": [JobStatus.GUARDRAIL_REJECTED.value]},
    {"key": "flaky", "label": "Flaky rerun", "statuses": [JobStatus.FLAKY_RERUN.value]},
    {"key": "failed", "label": "Failed", "statuses": [JobStatus.FAILED.value]},
]


def _jobs_url(
    status_key: str = "all",
    repo: Optional[str] = None,
    query: Optional[str] = None,
    page: Optional[int] = None,
) -> str:
    params: Dict[str, str] = {"status": status_key}
    if repo:
        params["repo"] = repo
    if query:
        params["q"] = query
    if page and page > 1:
        params["page"] = str(page)
    return "/jobs?" + urlencode(params)


def _build_tabs(
    counts: Dict[str, int],
    repo: Optional[str],
    query: Optional[str],
) -> List[Dict[str, object]]:
    tabs: List[Dict[str, object]] = []
    for tab in JOB_TABS:
        statuses = tab["statuses"]
        if statuses:
            count = sum(counts.get(status, 0) for status in statuses)
        else:
            count = sum(counts.values())
        tabs.append(
            {
                **tab,
                "count": count,
                "url": _jobs_url(tab["key"], repo, query),
            }
        )
    return tabs


def _guardrail_checks(job) -> List[Dict[str, object]]:
    violation = (job.guardrail_violation or "").upper()
    active = job.status in ACTIVE_STATUS_VALUES

    if job.status == JobStatus.PR_OPENED.value:
        sandbox_state = "ok"
        sandbox_detail = "Tests passed inside the sandbox."
    elif active:
        sandbox_state = "pending"
        sandbox_detail = "Running now."
    elif job.status == JobStatus.FLAKY_RERUN.value:
        sandbox_state = "skipped"
        sandbox_detail = "Transient failure was rerun, not patched."
    else:
        sandbox_state = "blocked"
        sandbox_detail = "No verified patch was produced."

    return [
        {
            "name": "Never touch tests",
            "state": "blocked" if violation == "NEVER_TOUCH_TESTS" else "ok",
            "detail": (
                "Attempted to edit a protected test or CI file."
                if violation == "NEVER_TOUCH_TESTS"
                else "Only source files are eligible for patching."
            ),
        },
        {
            "name": "Diff size limit",
            "state": "blocked" if violation == "DIFF_SIZE_EXCEEDED" else "ok",
            "detail": (
                f"Patch exceeded the {settings.MAX_DIFF_LINES}-line limit."
                if violation == "DIFF_SIZE_EXCEEDED"
                else f"Patches are capped at {settings.MAX_DIFF_LINES} altered lines."
            ),
        },
        {
            "name": "Infinite loop guard",
            "state": "blocked" if violation == "INFINITE_LOOP_PREVENTION" else "ok",
            "detail": (
                f"Branch was ignored because it starts with {settings.LOOP_PREVENTION_PREFIX}."
                if violation == "INFINITE_LOOP_PREVENTION"
                else "AI-created branches never re-trigger the pipeline."
            ),
        },
        {
            "name": "Sandbox verification",
            "state": sandbox_state,
            "detail": sandbox_detail,
        },
    ]


@router.get("/overview", response_class=HTMLResponse)
async def overview_page(request: Request):
    """Small overview: live stats, recent activity, and system health."""
    metrics = await get_metrics_summary()
    counts = await get_status_counts()

    return render_page(
        "overview.html",
        request,
        active="overview",
        metrics=metrics,
        recent_jobs=metrics.recent_jobs[:6],
        status_counts=counts,
        active_jobs=sum(counts.get(status, 0) for status in ACTIVE_STATUS_VALUES),
    )


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_page(
    request: Request,
    status: str = "all",
    repo: Optional[str] = None,
    q: Optional[str] = None,
    page: int = 1,
):
    """Filterable, paginated remediation job list."""
    selected = next((tab for tab in JOB_TABS if tab["key"] == status), JOB_TABS[0])
    repo = (repo or "").strip() or None
    q = (q or "").strip() or None
    page = max(1, page)

    jobs, total = await list_jobs(
        statuses=selected["statuses"],
        repo=repo,
        search=q,
        limit=PER_PAGE,
        offset=(page - 1) * PER_PAGE,
    )
    counts = await get_status_counts()
    repositories = [
        {"name": name, "count": count} for name, count in await get_repositories()
    ]

    total_pages = max(1, math.ceil(total / PER_PAGE))
    if page > total_pages:
        page = total_pages
        jobs, total = await list_jobs(
            statuses=selected["statuses"],
            repo=repo,
            search=q,
            limit=PER_PAGE,
            offset=(page - 1) * PER_PAGE,
        )

    return render_page(
        "jobs.html",
        request,
        active="jobs",
        jobs=jobs,
        total=total,
        page=page,
        total_pages=total_pages,
        tabs=_build_tabs(counts, repo, q),
        selected_status=selected,
        repositories=repositories,
        selected_repo=repo,
        search_query=q,
        start_index=((page - 1) * PER_PAGE + 1) if total else 0,
        end_index=min(page * PER_PAGE, total),
        prev_url=_jobs_url(selected["key"], repo, q, page - 1) if page > 1 else None,
        next_url=_jobs_url(selected["key"], repo, q, page + 1) if page < total_pages else None,
        clear_url=_jobs_url(selected["key"]),
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail_page(request: Request, job_id: str):
    """Inspect one remediation job: diagnosis, diff, and sandbox attempts."""
    job = await get_job(job_id)
    if not job:
        return render_page("not_found.html", request, active="jobs", status_code=404)

    meta = status_meta(job.status)
    return render_page(
        "job_detail.html",
        request,
        active="jobs",
        job=job,
        meta=meta,
        guardrail_checks=_guardrail_checks(job),
    )


@router.get("/guardrails", response_class=HTMLResponse)
async def guardrails_page(request: Request):
    """Explain and expose the real guardrail configuration and outcomes."""
    metrics = await get_metrics_summary()
    blocked_jobs, blocked_total = await list_jobs(
        statuses=[JobStatus.GUARDRAIL_REJECTED.value],
        limit=10,
    )

    return render_page(
        "guardrails.html",
        request,
        active="guardrails",
        metrics=metrics,
        protected_paths=settings.PROTECTED_PATHS,
        max_diff_lines=settings.MAX_DIFF_LINES,
        max_retry_attempts=settings.MAX_RETRY_ATTEMPTS,
        loop_prefix=settings.LOOP_PREVENTION_PREFIX,
        flaky_patterns=FLAKY_PATTERNS,
        blocked_jobs=blocked_jobs,
        blocked_total=blocked_total,
    )


def _settings_context(
    request: Request,
    form_values: Optional[Dict[str, str]] = None,
    errors: Optional[Dict[str, str]] = None,
    saved: bool = False,
    reveal_error: Optional[str] = None,
) -> Dict[str, object]:
    revealed = bool(getattr(request.state, "revealed", False))
    return {
        "groups": settings_view(form_values, errors, revealed=revealed),
        "webhook_url": public_webhook_url(request),
        "webhook_configured": bool(settings.PUBLIC_WEBHOOK_URL),
        "smee_status": get_smee_status(),
        "saved": saved,
        "revealed": revealed,
        "reveal_error": reveal_error,
        "reveal_notice": request.query_params.get("revealed") == "1",
        "hidden_notice": request.query_params.get("hidden") == "1",
        "errors": errors or {},
    }


@router.get("/guide", response_class=HTMLResponse)
async def guide_page(request: Request):
    """Complete, step-by-step documentation and setup walkthrough."""
    return render_page(
        "guide.html",
        request,
        active="guide",
        webhook_url=public_webhook_url(request),
        smee_status=get_smee_status(),
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, saved: int = 0):
    """Edit integrations, provider credentials, sandbox limits, and guardrails."""
    return render_page(
        "settings.html",
        request,
        active="settings",
        **_settings_context(request, saved=bool(saved)),
    )


@router.post("/settings/reveal", response_class=HTMLResponse)
async def reveal_secrets(request: Request):
    """Reveal stored keys and tokens after confirming the account password."""
    form = await request.form()
    password = str(form.get("password") or "")
    user = getattr(request.state, "user", None)

    if not user or not verify_password(password, user.password_hash):
        return render_page(
            "settings.html",
            request,
            active="settings",
            status_code=401,
            **_settings_context(request, reveal_error="That password is not correct."),
        )

    await set_reveal(request.cookies.get(SESSION_COOKIE), True)
    return RedirectResponse("/settings?revealed=1", status_code=303)


@router.post("/settings/hide")
async def hide_secrets(request: Request):
    """End the temporary secret reveal window."""
    await set_reveal(request.cookies.get(SESSION_COOKIE), False)
    return RedirectResponse("/settings?hidden=1", status_code=303)


@router.get("/configuration")
async def legacy_configuration_page():
    """Redirect the old settings URL used by earlier builds."""
    return RedirectResponse("/settings", status_code=307)


@router.post("/settings", response_class=HTMLResponse)
async def save_settings(request: Request):
    """Persist one settings group and apply it to the running process."""
    form = await request.form()
    errors = await save_form(form)

    if errors:
        form_values = {
            key: str(value)
            for key, value in form.items()
            if key != "group" and not key.startswith("clear_")
        }
        return render_page(
            "settings.html",
            request,
            active="settings",
            status_code=400,
            **_settings_context(request, form_values=form_values, errors=errors),
        )

    return RedirectResponse("/settings?saved=1", status_code=303)
