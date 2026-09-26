from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Session, User
from app.domain.clock import Clock
from app.errors import ApiError
from app.security.secretbox import SecretBox
from app.security.tokens import sha256_hex

SESSION_COOKIE = "panel_session"
CSRF_COOKIE = "panel_csrf"
CSRF_HEADER = "X-CSRF-Token"
SESSION_TTL = {"admin": timedelta(hours=12), "user": timedelta(days=30)}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_clock(request: Request) -> Clock:
    return request.app.state.clock


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_secretbox(request: Request) -> SecretBox:
    return request.app.state.secretbox


Db = Annotated[AsyncSession, Depends(get_db)]
ClockDep = Annotated[Clock, Depends(get_clock)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
SecretBoxDep = Annotated[SecretBox, Depends(get_secretbox)]


@dataclass
class Principal:
    role: str
    subject_id: int
    session_id: int

    @property
    def actor(self) -> str:
        return f"{self.role}:{self.subject_id}"


def _extract_token(request: Request) -> tuple[str | None, bool]:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip(), False
    return request.cookies.get(SESSION_COOKIE), True


async def current_principal(request: Request, db: Db, clock: ClockDep) -> Principal:
    token, via_cookie = _extract_token(request)
    if not token:
        raise ApiError(401, "unauthorized", "authentication required")
    if via_cookie and request.method not in _SAFE_METHODS:
        csrf = request.cookies.get(CSRF_COOKIE)
        if not csrf or request.headers.get(CSRF_HEADER) != csrf:
            raise ApiError(403, "csrf", "missing or invalid CSRF token")
    now = clock.now()
    session = (await db.execute(select(Session).where(Session.token_hash == sha256_hex(token)))).scalar_one_or_none()
    if session is None or session.revoked_at is not None or session.expires_at <= now:
        raise ApiError(401, "unauthorized", "session expired")
    if session.subject == "user":
        user = await db.get(User, session.subject_id)
        if user is None or user.deleting_at is not None:
            raise ApiError(401, "unauthorized", "account removed")
    session.last_used_at = now
    session.expires_at = now + SESSION_TTL[session.subject]
    await db.commit()
    return Principal(session.subject, session.subject_id, session.id)


PrincipalDep = Annotated[Principal, Depends(current_principal)]


async def current_admin(principal: PrincipalDep) -> Principal:
    if principal.role != "admin":
        raise ApiError(403, "forbidden", "admin role required")
    return principal


async def current_user(principal: PrincipalDep) -> Principal:
    if principal.role != "user":
        raise ApiError(403, "forbidden", "user role required")
    return principal


AdminDep = Annotated[Principal, Depends(current_admin)]
UserDep = Annotated[Principal, Depends(current_user)]


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate(request: Request, limiter: str, key: str) -> None:
    if not request.app.state.limiters[limiter].hit(key):
        raise ApiError(429, "rate_limited", "too many attempts, try again later")
