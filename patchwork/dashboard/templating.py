"""
Jinja environment, presentation filters, and read-only system state helpers.

The UI deliberately reads from the same sources as the pipeline (database rows,
settings, and guardrail configuration). Nothing here seeds or fabricates jobs.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from jinja2 import Environment, FileSystemLoader, select_autoescape

from patchwork.config import settings


TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")

STATUS_META: Dict[str, Dict[str, str]] = {
    "queued": {
        "label": "Queued",
        "state": "neutral",
        "icon": "dot-fill",
        "description": "Waiting for a worker to pick up the run.",
    },
    "extracting": {
        "label": "Extracting context",
        "state": "attention",
        "icon": "search",
        "description": "Slicing CI logs and isolating the failing frame.",
    },
    "diagnosing": {
        "label": "Diagnosing",
        "state": "attention",
        "icon": "code",
        "description": "Asking the model for a root cause and patch.",
    },
    "verifying": {
        "label": "Verifying",
        "state": "attention",
        "icon": "sync",
        "description": "Running tests inside the ephemeral sandbox.",
    },
    "pr_opened": {
        "label": "PR opened",
        "state": "open",
        "icon": "git-pull-request",
        "description": "A verified remediation pull request is open.",
    },
    "flaky_rerun": {
        "label": "Flaky rerun",
        "state": "attention",
        "icon": "sync",
        "description": "A transient failure was rerun instead of patched.",
    },
    "failed": {
        "label": "Failed",
        "state": "closed",
        "icon": "x-circle-fill",
        "description": "The pipeline could not produce a verified fix.",
    },
    "guardrail_rejected": {
        "label": "Guardrail blocked",
        "state": "blocked",
        "icon": "shield-check",
        "description": "A safety guardrail rejected the change.",
    },
}

ACTIVE_STATUSES = {"queued", "extracting", "diagnosing", "verifying"}


def status_meta(status: str) -> Dict[str, str]:
    """Return display metadata for a pipeline status."""
    return STATUS_META.get(
        (status or "").lower(),
        {
            "label": (status or "Unknown").replace("_", " ").title(),
            "state": "neutral",
            "icon": "dot-fill",
            "description": "",
        },
    )


def relative_time(value: Optional[datetime]) -> str:
    """Render a compact, GitHub-style relative timestamp."""
    if not value:
        return "—"

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    seconds = max(0, int((now - value).total_seconds()))

    if seconds < 10:
        return "just now"
    if seconds < 60:
        return f"{seconds} seconds ago"

    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"

    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"

    days = hours // 24
    if days < 30:
        return f"{days} day{'s' if days != 1 else ''} ago"

    return value.strftime("%b %d, %Y")


def format_duration(seconds: Optional[float]) -> str:
    """Render a pipeline duration in a compact human-readable form."""
    if seconds is None or seconds <= 0:
        return "—"
    if seconds < 1:
        return "<1s"
    if seconds < 60:
        return f"{seconds:.1f}s".rstrip("0").rstrip(".")

    minutes = int(seconds // 60)
    remainder = int(seconds % 60)
    return f"{minutes}m {remainder}s"


def short_sha(value: Optional[str]) -> str:
    """Return the seven-character commit abbreviation GitHub uses."""
    if not value:
        return "—"
    return value[:7]


def format_number(value: Optional[int]) -> str:
    """Add thousands separators to counters if available."""
    if value is None:
        return "0"
    return f"{int(value):,}"


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def parse_diff(diff: Optional[str]) -> Dict[str, Any]:
    """
    Convert a unified diff into rows with old/new line numbers so the
    template can render it like GitHub's diff viewer.
    """
    lines: List[Dict[str, Any]] = []
    additions = 0
    deletions = 0
    old_no: Optional[int] = None
    new_no: Optional[int] = None

    for raw in (diff or "").splitlines():
        if raw.startswith("@@"):
            match = _HUNK_RE.match(raw)
            if match:
                old_no = int(match.group(1))
                new_no = int(match.group(2))
            lines.append({"kind": "hunk", "old": None, "new": None, "text": raw})
            continue

        if raw.startswith("+++") or raw.startswith("---"):
            lines.append({"kind": "file", "old": None, "new": None, "text": raw})
            continue

        if raw.startswith("\\"):
            lines.append({"kind": "meta", "old": None, "new": None, "text": raw})
            continue

        if raw.startswith("+"):
            additions += 1
            lines.append({"kind": "add", "old": None, "new": new_no, "text": raw[1:]})
            if new_no is not None:
                new_no += 1
            continue

        if raw.startswith("-"):
            deletions += 1
            lines.append({"kind": "del", "old": old_no, "new": None, "text": raw[1:]})
            if old_no is not None:
                old_no += 1
            continue

        text = raw[1:] if raw.startswith(" ") else raw
        lines.append({"kind": "ctx", "old": old_no, "new": new_no, "text": text})
        if old_no is not None:
            old_no += 1
        if new_no is not None:
            new_no += 1

    return {"lines": lines, "additions": additions, "deletions": deletions}


def system_status() -> Dict[str, Dict[str, str]]:
    """Describe the current, real integration state without exposing secrets."""
    provider = (settings.LLM_PROVIDER or "mock").lower()
    if provider == "gemini":
        llm_has_key = bool(settings.GEMINI_API_KEY)
    elif provider in ("anthropic", "claude"):
        llm_has_key = bool(settings.ANTHROPIC_API_KEY)
    elif provider == "openai":
        llm_has_key = bool(settings.OPENAI_API_KEY)
    else:
        llm_has_key = False
    llm_ready = provider == "mock" or llm_has_key

    github_app = bool(settings.GITHUB_APP_ID and settings.GITHUB_PRIVATE_KEY)
    github_pat = bool(settings.GITHUB_TOKEN)
    github_ready = github_app or github_pat

    if github_app:
        github_value = "GitHub App"
        github_detail = "App credentials are loaded."
    elif github_pat:
        github_value = "Personal access token"
        github_detail = "Using a development token instead of a GitHub App."
    else:
        github_value = "Not configured"
        github_detail = "Add a GitHub App or token to open real pull requests."

    if provider == "mock":
        llm_value = "Mock provider"
        llm_detail = "Deterministic local patches; no model API is called."
    elif llm_has_key:
        llm_value = f"{provider.title()} · {settings.LLM_MODEL}"
        llm_detail = "Model credentials are loaded."
    else:
        llm_value = f"{provider.title()} · key missing"
        llm_detail = "Set the provider API key before running the worker."

    if settings.USE_DOCKER_SANDBOX:
        sandbox_value = "Docker sandbox"
        sandbox_detail = (
            f"{settings.SANDBOX_CPU_LIMIT:g} vCPU · {settings.SANDBOX_MEMORY_LIMIT} RAM · "
            f"{settings.SANDBOX_TIMEOUT_SECONDS}s timeout"
        )
    else:
        sandbox_value = "Local subprocess"
        sandbox_detail = "Docker verification is disabled in settings."

    database_value = "SQLite" if settings.DATABASE_URL.startswith("sqlite") else "PostgreSQL"
    redis_url = (settings.REDIS_URL or "").strip().lower()
    if not redis_url or redis_url in ("memory", "disabled", "none", "false"):
        queue_value = "In memory"
        queue_detail = "Jobs are processed by the built-in async queue in this process."
    else:
        queue_value = "Redis"
        queue_detail = (
            f"Configured at {settings.REDIS_URL}; falls back to an in-process async "
            "queue if Redis is unreachable."
        )

    return {
        "github": {
            "label": "GitHub",
            "value": github_value,
            "state": "ok" if github_ready else "warn",
            "detail": github_detail,
        },
        "llm": {
            "label": "AI provider",
            "value": llm_value,
            "state": "ok" if llm_ready and provider != "mock" else "warn",
            "detail": llm_detail,
        },
        "sandbox": {
            "label": "Sandbox",
            "value": sandbox_value,
            "state": "ok" if settings.USE_DOCKER_SANDBOX else "warn",
            "detail": sandbox_detail,
        },
        "queue": {
            "label": "Queue",
            "value": queue_value,
            "state": "off",
            "detail": queue_detail,
        },
        "database": {
            "label": "Storage",
            "value": database_value,
            "state": "ok",
            "detail": "Healing jobs and sandbox attempts are persisted here.",
        },
    }


def page_url(request: Any, page: int) -> str:
    """Build a pagination URL while preserving the current filters."""
    params = dict(request.query_params)
    params["page"] = str(page)
    return f"{request.url.path}?{urlencode(params)}"


jinja_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)

jinja_env.filters["relative_time"] = relative_time
jinja_env.filters["duration"] = format_duration
jinja_env.filters["short_sha"] = short_sha
jinja_env.filters["number"] = format_number
jinja_env.filters["parse_diff"] = parse_diff

jinja_env.globals["status_meta"] = status_meta
jinja_env.globals["system_status"] = system_status
jinja_env.globals["page_url"] = page_url
jinja_env.globals["settings"] = settings
jinja_env.globals["app_version"] = "1.0.0"


def render_page(
    template_name: str,
    request: Any,
    active: str = "",
    status_code: int = 200,
    **context: Any,
) -> Any:
    """Render a page with the shared shell context applied."""
    from fastapi.responses import HTMLResponse

    template = jinja_env.get_template(template_name)
    return HTMLResponse(
        content=template.render(
            request=request,
            active=active,
            system=system_status(),
            current_user=getattr(request.state, "user", None),
            **context,
        ),
        status_code=status_code,
    )
