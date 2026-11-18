"""
Dashboard page, account, and read-only API tests.

The UI must render from persisted state and must not expose a simulation or
seed endpoint that fabricates jobs.
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from patchwork.database import init_db
from patchwork.gateway.app import app


async def _client():
    await init_db()
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
async def client():
    """A signed-in browser session using a throwaway account."""
    async with await _client() as browser:
        username = f"tester-{uuid.uuid4().hex[:8]}"
        response = await browser.post(
            "/register",
            data={
                "username": username,
                "email": f"{username}@example.com",
                "password": "Testpass123!",
                "confirm": "Testpass123!",
            },
        )
        assert response.status_code == 303
        yield browser


@pytest.fixture
async def anonymous_client():
    """A browser with no login session."""
    async with await _client() as browser:
        yield browser


@pytest.mark.asyncio
async def test_dashboard_pages_render(client):
    for path in ("/overview", "/jobs", "/guardrails", "/settings", "/guide"):
        response = await client.get(path)
        assert response.status_code == 200
        assert "Patchwork" in response.text


@pytest.mark.asyncio
async def test_read_only_api_and_missing_simulate_endpoint(client):
    metrics = await client.get("/api/v1/metrics")
    assert metrics.status_code == 200
    assert "recent_jobs" in metrics.json()

    jobs = await client.get("/api/v1/jobs")
    assert jobs.status_code == 200
    assert "items" in jobs.json()
    assert "total" in jobs.json()

    simulate = await client.post("/api/v1/simulate")
    assert simulate.status_code == 404


@pytest.mark.asyncio
async def test_missing_job_returns_html_404(client):
    response = await client.get("/jobs/definitely-not-a-job")
    assert response.status_code == 404
    assert "Job not found" in response.text


@pytest.mark.asyncio
async def test_home_and_account_pages_are_public(anonymous_client):
    home = await anonymous_client.get("/")
    assert home.status_code == 200
    assert "Create account" in home.text
    assert "Sign in" in home.text

    assert (await anonymous_client.get("/guide")).status_code == 200
    assert (await anonymous_client.get("/login")).status_code == 200
    assert (await anonymous_client.get("/register")).status_code == 200


@pytest.mark.asyncio
async def test_dashboard_requires_login(anonymous_client):
    response = await anonymous_client.get("/overview", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/overview"

    settings = await anonymous_client.get("/settings", follow_redirects=False)
    assert settings.status_code == 303
    assert settings.headers["location"] == "/login?next=/settings"


@pytest.mark.asyncio
async def test_login_failure_and_logout(anonymous_client):
    username = f"login-{uuid.uuid4().hex[:8]}"
    registered = await anonymous_client.post(
        "/register",
        data={
            "username": username,
            "password": "Testpass123!",
            "confirm": "Testpass123!",
        },
    )
    assert registered.status_code == 303

    bad = await anonymous_client.post(
        "/login",
        data={"username": username, "password": "wrong-password", "next": "/overview"},
    )
    assert bad.status_code == 401
    assert "Check your username or password." in bad.text

    good = await anonymous_client.post(
        "/login",
        data={"username": username, "password": "Testpass123!", "next": "/overview"},
    )
    assert good.status_code == 303
    assert good.headers["location"] == "/overview"

    dashboard = await anonymous_client.get("/overview")
    assert dashboard.status_code == 200

    logout = await anonymous_client.post("/logout")
    assert logout.status_code == 303
    after = await anonymous_client.get("/overview", follow_redirects=False)
    assert after.status_code == 303


@pytest.mark.asyncio
async def test_registration_closes_after_first_account(anonymous_client, monkeypatch):
    from patchwork.config import settings

    username = f"admin-{uuid.uuid4().hex[:8]}"
    registered = await anonymous_client.post(
        "/register",
        data={
            "username": username,
            "password": "Adminpass123!",
            "confirm": "Adminpass123!",
        },
    )
    assert registered.status_code == 303
    await anonymous_client.post("/logout")

    monkeypatch.setattr(settings, "ALLOW_REGISTRATION", False)

    page = await anonymous_client.get("/register")
    assert page.status_code == 200
    assert "Registration is closed" in page.text

    second = await anonymous_client.post(
        "/register",
        data={
            "username": f"guest-{uuid.uuid4().hex[:8]}",
            "password": "Guestpass123!",
            "confirm": "Guestpass123!",
        },
    )
    assert second.status_code == 403
    assert "Registration is closed" in second.text


@pytest.mark.asyncio
async def test_settings_page_has_editable_fields(client):
    response = await client.get("/settings")
    assert response.status_code == 200
    assert 'name="GITHUB_WEBHOOK_SECRET"' in response.text
    assert 'name="LLM_PROVIDER"' in response.text
    assert 'name="MAX_DIFF_LINES"' in response.text
    assert "Saved to the database" in response.text
    assert "Confirm your account password" in response.text


@pytest.mark.asyncio
async def test_settings_save_persists_and_applies(client):
    response = await client.post(
        "/settings",
        data={
            "group": "guardrails",
            "MAX_DIFF_LINES": "42",
            "MAX_RETRY_ATTEMPTS": "4",
            "LOOP_PREVENTION_PREFIX": "ai-fix/",
            "PROTECTED_PATHS": "tests/\n.github/workflows",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    from patchwork.config import settings

    assert settings.MAX_DIFF_LINES == 42
    assert settings.MAX_RETRY_ATTEMPTS == 4

    saved = await client.get("/settings?saved=1")
    assert "Settings saved" in saved.text


@pytest.mark.asyncio
async def test_settings_never_echo_secrets(client):
    response = await client.post(
        "/settings",
        data={
            "group": "webhook",
            "PUBLIC_WEBHOOK_URL": "https://smee.io/test-channel",
            "GITHUB_WEBHOOK_SECRET": "super-secret-value",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    page = await client.get("/settings")
    assert "super-secret-value" not in page.text
    assert "ends in alue" in page.text
    assert "https://smee.io/test-channel" in page.text


@pytest.mark.asyncio
async def test_secret_reveal_requires_account_password(client):
    await client.post(
        "/settings",
        data={"group": "webhook", "GITHUB_WEBHOOK_SECRET": "reveal-me-1234"},
        follow_redirects=False,
    )

    wrong = await client.post(
        "/settings/reveal",
        data={"password": "not-my-password"},
        follow_redirects=False,
    )
    assert wrong.status_code == 401
    assert "reveal-me-1234" not in wrong.text

    correct = await client.post(
        "/settings/reveal",
        data={"password": "Testpass123!"},
        follow_redirects=False,
    )
    assert correct.status_code == 303
    assert correct.headers["location"] == "/settings?revealed=1"

    revealed = await client.get("/settings")
    assert 'value="reveal-me-1234"' in revealed.text
    assert "Keys and tokens are shown below" in revealed.text

    hidden = await client.post("/settings/hide", follow_redirects=False)
    assert hidden.status_code == 303

    after = await client.get("/settings")
    assert "reveal-me-1234" not in after.text
    assert "ends in 1234" in after.text


@pytest.mark.asyncio
async def test_settings_validation_error_keeps_form(client):
    response = await client.post(
        "/settings",
        data={"group": "llm", "LLM_PROVIDER": "invalid", "LLM_MODEL": "x"},
    )
    assert response.status_code == 400
    assert "Choose one of the available options" in response.text


@pytest.mark.asyncio
async def test_favicon_and_icon_fallbacks_are_served(anonymous_client):
    for path in (
        "/static/favicon.ico",
        "/static/favicon.svg",
        "/static/favicon-32.png",
        "/static/apple-touch-icon.png",
    ):
        response = await anonymous_client.get(path)
        assert response.status_code == 200

    page = await anonymous_client.get("/")
    assert 'href="/static/favicon.ico?v=4"' in page.text


@pytest.mark.asyncio
async def test_legacy_configuration_url_redirects(client):
    response = await client.get("/configuration", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/settings"


@pytest.mark.asyncio
async def test_smee_runner_status_and_guide():
    from patchwork.smee_runner import get_smee_status
    status = get_smee_status()
    assert isinstance(status, dict)
    assert "active" in status
    assert "connected" in status
    assert "url" in status
