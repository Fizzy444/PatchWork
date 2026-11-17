"""
FastAPI Server Gateway: Webhooks, REST API, and the multi-page dashboard.
"""

import os
import asyncio
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from patchwork.auth import get_current_user
from patchwork.config import settings
from patchwork.database import init_db
from patchwork.settings_store import load_and_apply
from patchwork.queue.worker import worker
from patchwork.smee_runner import start_smee, stop_smee
from patchwork.gateway.webhook import router as webhook_router
from patchwork.dashboard.api import router as dashboard_router
from patchwork.dashboard.auth_pages import router as auth_router
from patchwork.dashboard.pages import router as pages_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB tables, then apply settings saved from the dashboard
    await init_db()
    await load_and_apply()
    # Start background task worker
    worker_task = asyncio.create_task(worker.start())
    # Start automatic Smee webhook runner if configured
    await start_smee()
    try:
        yield
    finally:
        await stop_smee()
        await worker.stop()
        worker_task.cancel()


app = FastAPI(
    title=settings.APP_NAME,
    description="Autonomous CI Self-Healing & PR Remediation Engine",
    version="1.0.0",
    lifespan=lifespan
)

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static assets for the dashboard shell
static_dir = os.path.join(os.path.dirname(__file__), "..", "dashboard", "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Mount Routers
app.include_router(webhook_router)
app.include_router(dashboard_router)
app.include_router(auth_router)
app.include_router(pages_router)

PUBLIC_PATHS = {"/", "/guide", "/login", "/register", "/healthz", "/health"}
PUBLIC_PREFIXES = ("/static", "/api/v1/", "/docs", "/openapi.json", "/redoc")


def _is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


@app.middleware("http")
async def dashboard_auth(request: Request, call_next):
    """Require a login for dashboard pages, but never for webhooks or APIs."""
    user = await get_current_user(request)
    if user is None and not _is_public(request.url.path):
        return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)
    return await call_next(request)


@app.get("/healthz", tags=["System"])
async def health_check():
    """Health check probe."""
    return {"status": "healthy", "service": "patchwork", "version": "1.0.0"}


@app.get("/health", include_in_schema=False)
async def health_check_alias():
    """Alias for uptime probes that expect /health."""
    return await health_check()
