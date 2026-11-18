"""
Integration tests for Webhook Gateway (Component A).
"""

import json
import hmac
import hashlib
import pytest
from httpx import AsyncClient, ASGITransport

from patchwork.gateway.app import app
from patchwork.config import settings


def make_sig(body: bytes, secret: str = settings.GITHUB_WEBHOOK_SECRET) -> str:
    mac = hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


@pytest.mark.asyncio
async def test_ping_event():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        body = b"{}"
        headers = {
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": make_sig(body),
            "Content-Type": "application/json"
        }
        resp = await client.post("/api/v1/webhook", content=body, headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"status": "pong"}


@pytest.mark.asyncio
async def test_invalid_signature_rejected():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        body = b"{}"
        headers = {
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": "sha256=invaliddeadbeef",
            "Content-Type": "application/json"
        }
        resp = await client.post("/api/v1/webhook", content=body, headers=headers)
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_infinite_loop_branch_ignored():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "action": "completed",
            "workflow_run": {
                "id": 9999,
                "head_branch": "ai-fix/run-1234",
                "conclusion": "failure"
            },
            "repository": {"full_name": "acme/repo"}
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": make_sig(body),
            "Content-Type": "application/json"
        }
        resp = await client.post("/api/v1/webhook", content=body, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ignored"
        assert data["guardrail"] == "INFINITE_LOOP_PREVENTION"


from unittest.mock import patch
from patchwork.github.client import GitHubClient


@pytest.mark.asyncio
async def test_github_app_stateless_token_support():
    """Verify GitHub App client sends override header and supports 520+ char stateless tokens."""
    client = GitHubClient()
    client.app_id = "123456"
    client.private_key = "fake_key"

    # Simulated ~520 char stateless GitHub token (ghs_APPID_JWT)
    stateless_token = "ghs_123456_" + ("aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789" * 14)
    assert len(stateless_token) >= 500

    with patch.object(client, "_generate_app_jwt", return_value="mock.jwt.token"), \
         patch("httpx.AsyncClient.post") as mock_post:

        mock_post.return_value.status_code = 201
        mock_post.return_value.json = lambda: {"token": stateless_token}

        token = await client.get_installation_token(987654)
        assert token == stateless_token

        # Verify override header was sent
        call_args = mock_post.call_args
        assert call_args is not None
        req_headers = call_args[1].get("headers", {})
        assert req_headers.get("X-GitHub-Stateless-S2S-Token") == "true"
        assert "Authorization" in req_headers
