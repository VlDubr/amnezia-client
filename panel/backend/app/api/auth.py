import re
import secrets

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audit import audit
from app.api.deps import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    SESSION_TTL,
    ClockDep,
    Db,
    PrincipalDep,
    SettingsDep,
    check_rate,
    client_ip,
)
from app.db.models import ROLE_USER, InviteKey, Session, User
from app.domain.clock import Clock
from app.errors import ApiError
from app.security.passwords import hash_password, validate_password, verify_password
from app.security.tokens import new_token, normalize_invite_key, sha256_hex

router = APIRouter(prefix="/api/auth", tags=["auth"])

LOGIN_RE = re.compile(r"^[A-Za-z0-9._-]{3,64}$")
# Burn comparable CPU time for unknown logins so response timing does not reveal which logins exist.
_DUMMY_HASH = hash_password("dummy-password-for-timing")


class LoginIn(BaseModel):
    # No role: the account decides it. A "role" field sent by an older client is ignored.
    login: str
    password: str


class InviteCheckIn(BaseModel):
    key: str


class InviteRedeemIn(BaseModel):
    key: str
    login: str
    password: str


class TokenOut(BaseModel):
    token: str
    role: str


async def start_session(db: AsyncSession, response: Response, account: User, clock: Clock,
                        secure: bool) -> TokenOut:
    token = new_token()
    now = clock.now()
    ttl = SESSION_TTL[account.role]
    db.add(Session(user_id=account.id, token_hash=sha256_hex(token), last_used_at=now, expires_at=now + ttl))
    max_age = int(ttl.total_seconds())
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True, secure=secure, samesite="strict",
                        path="/api")
    response.set_cookie(CSRF_COOKIE, secrets.token_urlsafe(16), max_age=max_age, httponly=False, secure=secure,
                        samesite="strict", path="/")
    return TokenOut(token=token, role=account.role)


def raise_password_errors(password: str, user_inputs: list[str]) -> None:
    errors = validate_password(password, user_inputs)
    if errors:
        raise ApiError(422, f"password_{errors[0]}", "password does not meet the security policy")


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response, db: Db, clock: ClockDep,
                settings: SettingsDep) -> TokenOut:
    check_rate(request, "login_ip", client_ip(request))
    check_rate(request, "login_name", body.login.lower())
    account = (await db.execute(select(User).where(User.login == body.login))).scalar_one_or_none()
    password_hash = (account.password_hash if account else None) or _DUMMY_HASH
    valid = verify_password(password_hash, body.password) and account is not None
    if not valid or account.deleting_at is not None:
        raise ApiError(401, "invalid_credentials", "wrong login or password")
    out = await start_session(db, response, account, clock, settings.cookie_secure)
    actor = f"{account.role}:{account.id}"
    audit(db, actor, "login", actor, ip=client_ip(request))
    await db.commit()
    return out


@router.post("/logout", status_code=204)
async def logout(principal: PrincipalDep, response: Response, db: Db, clock: ClockDep) -> None:
    session = await db.get(Session, principal.session_id)
    session.revoked_at = clock.now()
    await db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/api")
    response.delete_cookie(CSRF_COOKIE, path="/")


@router.get("/session")
async def session_info(principal: PrincipalDep, db: Db) -> dict:
    account = await db.get(User, principal.subject_id)
    return {"role": principal.role, "id": principal.subject_id, "login": account.login}


async def _find_invite(db: AsyncSession, key: str) -> InviteKey:
    invite = (await db.execute(
        select(InviteKey).where(InviteKey.key_hash == sha256_hex(normalize_invite_key(key))).with_for_update()
    )).scalar_one_or_none()
    if invite is None or invite.used_at is not None or invite.revoked_at is not None:
        raise ApiError(404, "invite_invalid", "the key is invalid or already used")
    user = await db.get(User, invite.user_id)
    if user.role != ROLE_USER or user.deleting_at is not None or user.login is not None:
        raise ApiError(404, "invite_invalid", "the key is invalid or already used")
    return invite


async def _login_taken(db: AsyncSession, login: str) -> bool:
    return (await db.execute(select(User.id).where(User.login == login))).first() is not None


@router.post("/invite/check")
async def invite_check(body: InviteCheckIn, request: Request, db: Db) -> dict:
    check_rate(request, "invite_ip", client_ip(request))
    await _find_invite(db, body.key)
    return {"ok": True}


@router.post("/invite/redeem")
async def invite_redeem(body: InviteRedeemIn, request: Request, response: Response, db: Db, clock: ClockDep,
                        settings: SettingsDep) -> TokenOut:
    check_rate(request, "invite_ip", client_ip(request))
    invite = await _find_invite(db, body.key)
    if not LOGIN_RE.fullmatch(body.login):
        raise ApiError(422, "login_invalid", "login must be 3-64 characters: letters, digits, '.', '_' or '-'")
    user = await db.get(User, invite.user_id)
    raise_password_errors(body.password, [body.login, user.display_name])
    if await _login_taken(db, body.login):
        raise ApiError(409, "login_taken", "this login is already taken")
    user.login = body.login
    user.password_hash = hash_password(body.password)
    invite.used_at = clock.now()
    try:
        await db.flush()
    except IntegrityError as e:  # another invite took the same login after our check
        await db.rollback()
        raise ApiError(409, "login_taken", "this login is already taken") from e
    out = await start_session(db, response, user, clock, settings.cookie_secure)
    audit(db, f"user:{user.id}", "invite_redeem", f"user:{user.id}", ip=client_ip(request))
    await db.commit()
    return out
