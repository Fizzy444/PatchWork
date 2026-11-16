"""
Dashboard-editable settings.

Values saved from the Settings page live in the ``app_settings`` table and are
applied to the running ``settings`` object immediately. Database values take
precedence over ``.env`` and survive restarts.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select

from patchwork.config import settings
from patchwork.database import async_session
from patchwork.models import SettingRecord

logger = logging.getLogger("patchwork.settings")


@dataclass(frozen=True)
class SettingField:
    key: str
    label: str
    kind: str = "text"
    help: str = ""
    placeholder: str = ""
    options: Tuple[Tuple[str, str], ...] = ()
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    step: str = "1"
    multiline: bool = False
    required: bool = False


@dataclass(frozen=True)
class SettingGroup:
    key: str
    title: str
    description: str
    fields: Tuple[SettingField, ...]


SETTING_GROUPS: Tuple[SettingGroup, ...] = (
    SettingGroup(
        key="github",
        title="GitHub App",
        description="Credentials used to read workflow logs and open pull requests.",
        fields=(
            SettingField(
                key="GITHUB_APP_ID",
                label="App ID",
                placeholder="123456",
                help="Numeric ID of your GitHub App.",
            ),
            SettingField(
                key="GITHUB_PRIVATE_KEY",
                label="Private key",
                kind="secret",
                multiline=True,
                help="PEM contents or a path to the private key file.",
            ),
            SettingField(
                key="GITHUB_TOKEN",
                label="Development token",
                kind="secret",
                help="Personal access token used when no GitHub App is configured.",
            ),
        ),
    ),
    SettingGroup(
        key="webhook",
        title="Webhook",
        description="Where GitHub delivers workflow run events.",
        fields=(
            SettingField(
                key="PUBLIC_WEBHOOK_URL",
                label="Webhook payload URL (or Smee URL)",
                placeholder="https://smee.io/your-channel",
                help=(
                    "Full URL GitHub posts to. If you enter a smee.io URL (e.g. https://smee.io/abc123xyz), "
                    "Patchwork automatically connects to Smee and forwards events to /api/v1/webhook in the background."
                ),
            ),
            SettingField(
                key="GITHUB_WEBHOOK_SECRET",
                label="Webhook secret",
                kind="secret",
                help="Must match the secret configured on the GitHub webhook.",
            ),
        ),
    ),
    SettingGroup(
        key="llm",
        title="AI provider",
        description="Model used to diagnose failures and propose patches.",
        fields=(
            SettingField(
                key="LLM_PROVIDER",
                label="Provider",
                kind="select",
                options=(
                    ("gemini", "Google Gemini"),
                    ("anthropic", "Anthropic Claude"),
                    ("openai", "OpenAI"),
                    ("mock", "Mock (no API calls)"),
                ),
            ),
            SettingField(
                key="LLM_MODEL",
                label="Model",
                placeholder="gemini-2.5-flash, claude-3-5-sonnet-20241022, or gpt-4o",
                help="Model name passed to the selected provider.",
            ),
            SettingField(
                key="GEMINI_API_KEY",
                label="Gemini API key",
                kind="secret",
            ),
            SettingField(
                key="ANTHROPIC_API_KEY",
                label="Anthropic Claude API key",
                kind="secret",
            ),
            SettingField(
                key="OPENAI_API_KEY",
                label="OpenAI API key",
                kind="secret",
            ),
        ),
    ),
    SettingGroup(
        key="sandbox",
        title="Verification sandbox",
        description="Limits applied when tests run against a proposed patch.",
        fields=(
            SettingField(
                key="USE_DOCKER_SANDBOX",
                label="Run tests in Docker",
                kind="toggle",
                help="Falls back to an isolated subprocess when Docker is unavailable.",
            ),
            SettingField(
                key="SANDBOX_TIMEOUT_SECONDS",
                label="Timeout (seconds)",
                kind="number",
                minimum=10,
                maximum=3600,
            ),
            SettingField(
                key="SANDBOX_MEMORY_LIMIT",
                label="Memory limit",
                placeholder="512m",
                help="Docker memory value such as 512m or 1g.",
            ),
            SettingField(
                key="SANDBOX_CPU_LIMIT",
                label="CPU limit",
                kind="float",
                minimum=0.1,
                maximum=8,
                step="0.1",
                help="CPU cores allocated to the sandbox container.",
            ),
            SettingField(
                key="SANDBOX_NETWORK_DISABLED",
                label="Disable container network",
                kind="toggle",
                help="Prevents tests from reaching the network.",
            ),
        ),
    ),
    SettingGroup(
        key="guardrails",
        title="Guardrails",
        description="Deterministic limits enforced before a pull request can open.",
        fields=(
            SettingField(
                key="MAX_DIFF_LINES",
                label="Maximum changed lines",
                kind="number",
                minimum=1,
                maximum=1000,
                help="A patch above this size is rejected.",
            ),
            SettingField(
                key="MAX_RETRY_ATTEMPTS",
                label="Self-correction attempts",
                kind="number",
                minimum=1,
                maximum=10,
            ),
            SettingField(
                key="LOOP_PREVENTION_PREFIX",
                label="AI branch prefix",
                placeholder="ai-fix/",
                help="Failures on branches with this prefix are ignored.",
            ),
            SettingField(
                key="PROTECTED_PATHS",
                label="Protected paths",
                kind="list",
                help="One path or filename fragment per line. Patches touching these are rejected.",
            ),
        ),
    ),
)

FIELD_BY_KEY: Dict[str, SettingField] = {
    field.key: field for group in SETTING_GROUPS for field in group.fields
}

_MEMORY_RE = re.compile(r"^\d+(?:\.\d+)?[bkmg]?$", re.IGNORECASE)


def current_value(key: str) -> Any:
    return getattr(settings, key, "")


def is_configured(key: str) -> bool:
    value = current_value(key)
    return bool(value) if not isinstance(value, bool) else value


def display_value(key: str) -> str:
    value = current_value(key)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "\n".join(str(item) for item in value)
    return "" if value is None else str(value)


def masked_hint(key: str) -> str:
    value = current_value(key)
    if not value:
        return "Not configured"
    if isinstance(value, str) and len(value) >= 8:
        return f"Configured · ends in {value[-4:]}"
    return "Configured"


def _parse(field: SettingField, raw: str) -> Tuple[Any, Optional[str]]:
    raw = raw.strip()

    if field.kind == "toggle":
        return raw.lower() in ("true", "on", "1"), None

    if field.kind == "list":
        values = [line.strip() for line in raw.splitlines() if line.strip()]
        if field.required and not values:
            return None, "Add at least one entry."
        return values, None

    if field.kind == "select":
        allowed = {value for value, _ in field.options}
        if raw not in allowed:
            return None, "Choose one of the available options."
        return raw, None

    if field.kind in ("number", "float"):
        try:
            number = float(raw) if field.kind == "float" else int(raw)
        except ValueError:
            return None, "Enter a valid number."
        if field.minimum is not None and number < field.minimum:
            return None, f"Must be at least {field.minimum:g}."
        if field.maximum is not None and number > field.maximum:
            return None, f"Must be at most {field.maximum:g}."
        return number, None

    if field.key == "PUBLIC_WEBHOOK_URL" and raw:
        if not re.match(r"^https?://", raw):
            return None, "Enter a full http:// or https:// URL."
        return raw.rstrip("/"), None

    if field.key == "GITHUB_APP_ID" and raw and not raw.isdigit():
        return None, "The App ID must be numeric."

    if field.key == "SANDBOX_MEMORY_LIMIT" and raw and not _MEMORY_RE.match(raw):
        return None, "Use a Docker size such as 512m or 1g."

    return raw, None


def _serialize(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "\n".join(str(item) for item in value)
    return "" if value is None else str(value)


def _apply(updates: Dict[str, Any]) -> None:
    for key, value in updates.items():
        setattr(settings, key, value)


async def load_and_apply() -> None:
    """Apply saved settings over the environment values at startup."""
    try:
        async with async_session() as session:
            result = await session.execute(select(SettingRecord))
            records = list(result.scalars())
    except Exception:
        logger.exception("Could not load saved settings.")
        return

    for record in records:
        field = FIELD_BY_KEY.get(record.key)
        if not field:
            continue
        value, error = _parse(field, record.value)
        if error:
            logger.warning("Ignoring saved setting %s: %s", record.key, error)
            continue
        setattr(settings, field.key, value)


async def save_form(form: Any) -> Dict[str, str]:
    """
    Validate and save one settings group from a submitted form.

    Returns a mapping of field name to error message. An empty mapping means the
    values were saved and applied.
    """
    group_key = str(form.get("group") or "")
    group = next((item for item in SETTING_GROUPS if item.key == group_key), None)
    if group is None:
        return {"_form": "Unknown settings group."}

    updates: Dict[str, Any] = {}
    errors: Dict[str, str] = {}

    for field in group.fields:
        if field.kind == "secret":
            if form.get(f"clear_{field.key}"):
                updates[field.key] = ""
                continue
            raw = form.get(field.key)
            if raw is None or str(raw) == "":
                continue
            raw_value = str(raw)
        elif field.kind == "toggle":
            raw_value = "true" if field.key in form else "false"
        else:
            raw_value = str(form.get(field.key) or "")

        value, error = _parse(field, raw_value)
        if error:
            errors[field.key] = error
        else:
            updates[field.key] = value

    if errors:
        return errors

    async with async_session() as session:
        for key, value in updates.items():
            record = await session.get(SettingRecord, key)
            if record is None:
                session.add(SettingRecord(key=key, value=_serialize(value)))
            else:
                record.value = _serialize(value)
        await session.commit()

    _apply(updates)
    if "PUBLIC_WEBHOOK_URL" in updates:
        try:
            import asyncio
            from patchwork.smee_runner import restart_smee
            asyncio.create_task(restart_smee())
        except Exception as exc:
            logger.warning("Could not restart Smee runner: %s", exc)
    return {}


def settings_view(
    form_values: Optional[Dict[str, str]] = None,
    errors: Optional[Dict[str, str]] = None,
    revealed: bool = False,
) -> List[Dict[str, Any]]:
    """Build the template representation of the editable settings."""
    form_values = form_values or {}
    errors = errors or {}
    groups: List[Dict[str, Any]] = []

    for group in SETTING_GROUPS:
        fields: List[Dict[str, Any]] = []
        for field in group.fields:
            if field.kind == "secret":
                value: Any = display_value(field.key) if revealed else ""
            elif field.kind == "toggle":
                value = bool(current_value(field.key))
            else:
                value = form_values.get(field.key, display_value(field.key))

            fields.append(
                {
                    "key": field.key,
                    "label": field.label,
                    "kind": field.kind,
                    "help": field.help,
                    "placeholder": field.placeholder,
                    "options": field.options,
                    "value": value,
                    "configured": is_configured(field.key),
                    "hint": masked_hint(field.key),
                    "error": errors.get(field.key),
                    "minimum": field.minimum,
                    "maximum": field.maximum,
                    "step": field.step,
                    "multiline": field.multiline,
                }
            )

        groups.append(
            {
                "key": group.key,
                "title": group.title,
                "description": group.description,
                "fields": fields,
                "error": errors.get("_form"),
            }
        )

    return groups


def webhook_url(request: Any) -> str:
    """Return the configured public webhook URL, or this server's URL."""
    configured = (settings.PUBLIC_WEBHOOK_URL or "").strip()
    if configured:
        return configured
    return str(request.base_url).rstrip("/") + "/api/v1/webhook"
