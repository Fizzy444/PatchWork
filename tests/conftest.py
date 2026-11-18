"""
Test isolation.

Every test run uses a throwaway SQLite database so synthetic fixtures never
leak into the developer's local patchwork.db (and therefore never appear in
the dashboard).
"""

import copy
import os
import tempfile
from pathlib import Path

import pytest

_TEST_DB = Path(tempfile.gettempdir()) / "patchwork-tests.db"

# Start from a clean database. The previous run's file may still exist because
# Windows can keep it locked until the test process exits.
if _TEST_DB.exists():
    _TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB.as_posix()}"
# Keep tests offline and deterministic even when a developer has real keys in .env.
os.environ["LLM_PROVIDER"] = "mock"
os.environ["GITHUB_TOKEN"] = ""
os.environ["GITHUB_APP_ID"] = ""
os.environ["GITHUB_PRIVATE_KEY"] = ""
os.environ["USE_DOCKER_SANDBOX"] = "false"
# Tests create several accounts in one session.
os.environ["ALLOW_REGISTRATION"] = "true"


def pytest_sessionfinish(session, exitstatus):
    """Best-effort removal once the test session ends."""
    try:
        _TEST_DB.unlink(missing_ok=True)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def restore_settings():
    """Undo settings changes made by a test."""
    from patchwork.config import settings

    snapshot = copy.deepcopy(settings.model_dump())
    yield
    for key, value in snapshot.items():
        setattr(settings, key, copy.deepcopy(value))
