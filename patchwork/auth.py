"""
Authentication for the dashboard.

Passwords are stored as PBKDF2 hashes. Login sessions are opaque random tokens
kept in SQLite, so there is no signing secret to manage and sessions can be
revoked by deleting a row. Secret values on the Settings page stay masked until
the signed-in user confirms their password again.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy import func, or_, select

from patchwork.config import settings
from patchwork.database import async_session
from patchwork.models import UserRecord, UserSessionRecord

logger = logging.getLogger("patchwork.auth")

SESSION_COOKIE = "patchwork_session"
SESSION_DAYS = 7
REVEAL_MINUTES = 5
REGISTRATION_CLOSED_MESSAGE = "Registration is closed. Ask the administrator for access."
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def hash_password(password: str) -> str:
    """Hash a password with a fresh salt."""
    salt = os.urandom(16)
    iterations = 200_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """Check a password against a stored PBKDF2 hash."""
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


async def is_registration_open() -> bool:
    """Registration is open only before the first account exists, unless enabled."""
    if settings.ALLOW_REGISTRATION:
        return True
    async with async_session() as session:
        count = (await session.execute(select(func.count(UserRecord.id)))).scalar_one()
    return count == 0


async def create_user(
    username: str,
    password: str,
    email: Optional[str] = None,
) -> Tuple[Optional[UserRecord], Optional[str]]:
    """Create a user, returning the user or a validation error."""
    username = (username or "").strip()
    email = (email or "").strip().lower() or None

    if not _USERNAME_RE.match(username):
        return None, "Use 3-32 letters, numbers, dots, dashes, or underscores."
    if len(password or "") < 8:
        return None, "Use at least 8 characters for the password."
    if email and (len(email) > 128 or not _EMAIL_RE.match(email)):
        return None, "Enter a valid email address."

    async with async_session() as session:
        existing = await session.execute(
            select(UserRecord).where(func.lower(UserRecord.username) == username.lower())
        )
        if existing.scalar_one_or_none():
            return None, "That username is already taken."

        if email:
            existing_email = await session.execute(
                select(UserRecord).where(func.lower(UserRecord.email) == email)
            )
            if existing_email.scalar_one_or_none():
                return None, "That email is already registered."

        user_count = (await session.execute(select(func.count(UserRecord.id)))).scalar_one()
        if user_count and not settings.ALLOW_REGISTRATION:
            return None, REGISTRATION_CLOSED_MESSAGE

        user = UserRecord(
            username=username,
            email=email,
            password_hash=hash_password(password),
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user, None


async def authenticate(identifier: str, password: str) -> Optional[UserRecord]:
    """Look up a user by username or email and verify the password."""
    identifier = (identifier or "").strip().lower()
    if not identifier or not password:
        return None

    async with async_session() as session:
        result = await session.execute(
            select(UserRecord).where(
                or_(
                    func.lower(UserRecord.username) == identifier,
                    func.lower(UserRecord.email) == identifier,
                )
            )
        )
        user = result.scalar_one_or_none()
        if not user or not verify_password(password, user.password_hash):
            return None
        return user


async def create_session(user_id: int) -> str:
    """Start a new login session and return its cookie token."""
    token = secrets.token_urlsafe(32)
    session_record = UserSessionRecord(
        token=token,
        user_id=user_id,
        expires_at=datetime.utcnow() + timedelta(days=SESSION_DAYS),
    )
    async with async_session() as session:
        session.add(session_record)
        await session.commit()
    return token


async def delete_session(token: Optional[str]) -> None:
    """End a login session."""
    if not token:
        return
    async with async_session() as session:
        record = await session.get(UserSessionRecord, token)
        if record:
            await session.delete(record)
            await session.commit()


async def get_session_user(token: Optional[str]) -> Tuple[Optional[UserRecord], bool]:
    """Resolve a session token to a user and whether secrets are revealed."""
    if not token:
        return None, False

    now = datetime.utcnow()
    async with async_session() as session:
        result = await session.execute(
            select(UserSessionRecord).where(UserSessionRecord.token == token)
        )
        record = result.scalar_one_or_none()
        if not record or record.expires_at <= now:
            return None, False
        user = await session.get(UserRecord, record.user_id)
        if not user:
            return None, False
        revealed = bool(record.revealed_until and record.revealed_until > now)
        return user, revealed


async def set_reveal(token: Optional[str], enabled: bool) -> None:
    """Allow or end the temporary secret reveal window for a session."""
    if not token:
        return
    async with async_session() as session:
        record = await session.get(UserSessionRecord, token)
        if not record:
            return
        record.revealed_until = (
            datetime.utcnow() + timedelta(minutes=REVEAL_MINUTES) if enabled else None
        )
        await session.commit()


async def get_current_user(request) -> Optional[UserRecord]:
    """Attach the signed-in user and reveal state to the request."""
    user, revealed = await get_session_user(request.cookies.get(SESSION_COOKIE))
    request.state.user = user
    request.state.revealed = revealed
    return user
