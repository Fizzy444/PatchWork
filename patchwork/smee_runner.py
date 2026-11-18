"""
Automatic Smee Runner: Native Python SSE Client for Smee.io

When a user configures a smee.io webhook URL (e.g. https://smee.io/xyz),
this runner automatically establishes a persistent Server-Sent Events (SSE)
stream connection to smee.io and forwards all incoming GitHub webhook payloads
directly to the local Patchwork webhook endpoint (/api/v1/webhook).

This eliminates the need to run `smee -u ...` in a separate terminal process.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from patchwork.config import settings

logger = logging.getLogger("patchwork.smee")


class SmeeRunner:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running: bool = False
        self._connected: bool = False
        self._current_url: str = ""
        self._events_count: int = 0
        self._last_event_time: Optional[str] = None
        self._last_error: Optional[str] = None

    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    @property
    def is_connected(self) -> bool:
        return self._connected

    def get_status(self) -> Dict[str, Any]:
        """Return operational status for UI dashboard and diagnostics."""
        url = (settings.PUBLIC_WEBHOOK_URL or "").strip()
        is_smee = "smee.io" in url.lower()
        return {
            "active": self.is_running,
            "connected": self._connected,
            "is_smee": is_smee,
            "url": self._current_url or url,
            "events_count": self._events_count,
            "last_event_time": self._last_event_time,
            "last_error": self._last_error,
        }

    async def start(self) -> None:
        """Start the background SSE listener if a valid smee.io URL is configured."""
        url = (settings.PUBLIC_WEBHOOK_URL or "").strip()
        if not url or "smee.io" not in url.lower():
            logger.debug("Smee runner not started: PUBLIC_WEBHOOK_URL is not a smee.io URL.")
            return

        if self.is_running and self._current_url == url:
            logger.debug("Smee runner already running for %s", url)
            return

        await self.stop()

        self._running = True
        self._current_url = url
        self._last_error = None
        self._task = asyncio.create_task(self._run_stream_loop(url), name="smee-client-runner")
        logger.info("Automatic Smee runner started for %s", url)

    async def stop(self) -> None:
        """Gracefully stop the background SSE listener."""
        self._running = False
        self._connected = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        logger.info("Automatic Smee runner stopped.")

    async def restart(self) -> None:
        """Restart the runner (e.g. after settings update)."""
        await self.stop()
        await self.start()

    async def _forward_payload(self, event_type: str, headers: Dict[str, str], body: Any) -> None:
        """Forward an incoming webhook event from Smee to the local webhook endpoint."""
        target_port = getattr(settings, "PORT", 8000)
        target_url = f"http://127.0.0.1:{target_port}/api/v1/webhook"

        # Prepare raw body bytes
        if isinstance(body, (dict, list)):
            body_bytes = json.dumps(body, separators=(",", ":")).encode("utf-8")
        elif isinstance(body, str):
            body_bytes = body.encode("utf-8")
        elif isinstance(body, bytes):
            body_bytes = body
        else:
            body_bytes = json.dumps(body or {}).encode("utf-8")

        # Build forwarding headers
        post_headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": "Patchwork-Smee-Runner/1.0",
        }

        # Transfer relevant GitHub headers from Smee
        for k, v in headers.items():
            k_lower = k.lower()
            if k_lower.startswith("x-github-") or k_lower in ("x-hub-signature-256", "x-hub-signature"):
                post_headers[k] = str(v)

        # Ensure event header is set
        if "X-GitHub-Event" not in post_headers and "x-github-event" not in post_headers:
            if event_type and event_type != "message":
                post_headers["X-GitHub-Event"] = event_type
            elif "x-github-event" in headers:
                post_headers["X-GitHub-Event"] = str(headers["x-github-event"])

        # Re-sign with webhook secret if configured so HMAC validation passes
        secret = settings.GITHUB_WEBHOOK_SECRET
        if secret:
            mac = hmac.new(secret.encode("utf-8"), msg=body_bytes, digestmod=hashlib.sha256)
            post_headers["X-Hub-Signature-256"] = f"sha256={mac.hexdigest()}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(target_url, content=body_bytes, headers=post_headers)
                self._events_count += 1
                self._last_event_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                logger.info(
                    "Smee forwarded %s event (status %d, %d bytes)",
                    post_headers.get("X-GitHub-Event", "unknown"),
                    resp.status_code,
                    len(body_bytes),
                )
        except Exception as exc:
            self._last_error = f"Forwarding failed: {exc}"
            logger.error("Failed to forward Smee payload to %s: %s", target_url, exc)

    async def _run_stream_loop(self, url: str) -> None:
        """Main loop that maintains the SSE connection to smee.io with auto-reconnect."""
        backoff = 2
        max_backoff = 30

        while self._running:
            try:
                logger.info("Connecting to Smee stream: %s", url)
                req_headers = {"Accept": "text/event-stream", "User-Agent": "Patchwork-Smee/1.0"}

                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream("GET", url, headers=req_headers) as response:
                        if response.status_code != 200:
                            raise RuntimeError(f"Smee stream returned status {response.status_code}")

                        self._connected = True
                        self._last_error = None
                        backoff = 2
                        logger.info("Connected to Smee stream successfully.")

                        current_event: Optional[str] = None
                        current_data: list[str] = []

                        async for raw_line in response.aiter_lines():
                            if not self._running:
                                break

                            line = raw_line.rstrip("\r\n")

                            if line.startswith("event:"):
                                current_event = line[len("event:"):].strip()
                            elif line.startswith("data:"):
                                current_data.append(line[len("data:"):].strip())
                            elif line == "":
                                # End of an SSE event
                                if current_data:
                                    raw_payload = "\n".join(current_data)
                                    try:
                                        data_obj = json.loads(raw_payload)
                                        if isinstance(data_obj, dict):
                                            # Smee sends headers as top-level keys or under 'headers',
                                            # and the webhook body under 'body'.
                                            headers = data_obj.get("headers", {})
                                            if not isinstance(headers, dict):
                                                headers = {}

                                            # Copy top-level headers like x-github-event if present
                                            for k, v in data_obj.items():
                                                if k not in ("body", "headers", "query") and isinstance(v, (str, int)):
                                                    headers[k] = str(v)

                                            body = data_obj.get("body", {})
                                            event_name = current_event or headers.get("x-github-event") or "message"

                                            if current_event != "ready" and body:
                                                await self._forward_payload(event_name, headers, body)
                                    except json.JSONDecodeError:
                                        logger.debug("Ignored non-JSON Smee SSE message: %s", raw_payload[:60])
                                    except Exception as exc:
                                        logger.warning("Error processing Smee payload: %s", exc)

                                current_event = None
                                current_data = []

            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._connected = False
                self._last_error = str(exc)
                logger.warning("Smee connection dropped (%s). Reconnecting in %ds...", exc, backoff)
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    break
                backoff = min(backoff * 2, max_backoff)

        self._connected = False
        logger.info("Smee stream loop exited.")


# Singleton instance
smee_runner = SmeeRunner()


async def start_smee() -> None:
    """Start Smee runner if URL is configured."""
    await smee_runner.start()


async def stop_smee() -> None:
    """Stop running Smee runner."""
    await smee_runner.stop()


async def restart_smee() -> None:
    """Restart Smee runner with current settings."""
    await smee_runner.restart()


def get_smee_status() -> Dict[str, Any]:
    """Get status of the Smee runner."""
    return smee_runner.get_status()
