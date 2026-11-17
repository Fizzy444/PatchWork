"""
Account pages: landing screen, sign in, registration, and sign out.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from patchwork.auth import (
    REGISTRATION_CLOSED_MESSAGE,
    SESSION_COOKIE,
    SESSION_DAYS,
    authenticate,
    create_session,
    create_user,
    delete_session,
    is_registration_open,
)
from patchwork.dashboard.templating import render_page

router = APIRouter(tags=["Account"])


def _safe_next(value: str) -> str:
    """Keep post-login redirects on this site."""
    target = (value or "").strip()
    if target.startswith("/") and not target.startswith("//"):
        return target
    return "/overview"


def _session_response(target: str, token: str) -> RedirectResponse:
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_DAYS * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/", response_class=HTMLResponse)
async def home_page(request: Request):
    """Public landing page; signed-in users go straight to the dashboard."""
    if getattr(request.state, "user", None):
        return RedirectResponse("/overview", status_code=303)
    return render_page(
        "home.html",
        request,
        active="home",
        registration_open=await is_registration_open(),
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/overview"):
    """Show the sign-in form."""
    if getattr(request.state, "user", None):
        return RedirectResponse(_safe_next(next), status_code=303)
    return render_page("login.html", request, active="login", next=_safe_next(next), error=None)


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    """Verify credentials and start a session."""
    form = await request.form()
    identifier = str(form.get("username") or "")
    password = str(form.get("password") or "")
    target = _safe_next(str(form.get("next") or ""))

    user = await authenticate(identifier, password)
    if not user:
        return render_page(
            "login.html",
            request,
            active="login",
            status_code=401,
            next=target,
            error="Check your username or password.",
            username=identifier,
        )

    token = await create_session(user.id)
    return _session_response(target, token)


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    """Show the account creation form."""
    if getattr(request.state, "user", None):
        return RedirectResponse("/overview", status_code=303)
    closed = not await is_registration_open()
    return render_page(
        "register.html",
        request,
        active="register",
        error=REGISTRATION_CLOSED_MESSAGE if closed else None,
        closed=closed,
        values={},
    )


@router.post("/register", response_class=HTMLResponse)
async def register_submit(request: Request):
    """Create an account and sign the new user in."""
    form = await request.form()
    username = str(form.get("username") or "")
    email = str(form.get("email") or "")
    password = str(form.get("password") or "")
    confirm = str(form.get("confirm") or "")

    values = {"username": username, "email": email}
    if not await is_registration_open():
        return render_page(
            "register.html",
            request,
            active="register",
            status_code=403,
            error=REGISTRATION_CLOSED_MESSAGE,
            closed=True,
            values=values,
        )
    if password != confirm:
        return render_page(
            "register.html",
            request,
            active="register",
            status_code=400,
            error="The passwords do not match.",
            values=values,
        )

    user, error = await create_user(username, password, email)
    if error:
        return render_page(
            "register.html",
            request,
            active="register",
            status_code=400,
            error=error,
            values=values,
        )

    token = await create_session(user.id)
    return _session_response("/overview", token)


@router.post("/logout")
async def logout_submit(request: Request):
    """End the current session."""
    await delete_session(request.cookies.get(SESSION_COOKIE))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
