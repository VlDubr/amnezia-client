from datetime import timedelta

from fastapi import APIRouter
from sqlalchemy import select, update

from app.api.audit import audit
from app.api.deps import AdminDep, ClockDep, Db
from app.db.models import KEY_RESET, ROLE_ADMIN, InviteKey, User
from app.errors import ApiError
from app.security.tokens import new_invite_key, normalize_invite_key, sha256_hex

router = APIRouter(prefix="/api/admin", tags=["admin"])

RESET_KEY_TTL = timedelta(hours=24)


@router.get("/admins")
async def list_admins(_: AdminDep, db: Db) -> list[dict]:
    admins = (await db.execute(select(User).where(User.role == ROLE_ADMIN).order_by(User.login))).scalars().all()
    return [{"id": a.id, "login": a.login, "display_name": a.display_name, "created_at": a.created_at}
            for a in admins]


@router.post("/accounts/{account_id}/reset-key")
async def issue_reset_key(account_id: int, admin: AdminDep, db: Db, clock: ClockDep) -> dict:
    """Any registered account, users and administrators alike. The current password keeps working until used."""
    account = (await db.execute(select(User).where(User.id == account_id).with_for_update())).scalar_one_or_none()
    if account is None or account.deleting_at is not None:
        raise ApiError(404, "not_found", "account not found")
    if account.login is None:
        raise ApiError(409, "not_registered", "the account has not registered yet")
    now = clock.now()
    await db.execute(update(InviteKey).where(InviteKey.user_id == account.id, InviteKey.purpose == KEY_RESET,
                                             InviteKey.used_at.is_(None), InviteKey.revoked_at.is_(None))
                     .values(revoked_at=now))
    key = new_invite_key()
    expires_at = now + RESET_KEY_TTL
    db.add(InviteKey(user_id=account.id, key_hash=sha256_hex(normalize_invite_key(key)), purpose=KEY_RESET,
                     expires_at=expires_at))
    audit(db, admin.actor, "password_reset_key", f"{account.role}:{account.id}")
    await db.commit()
    return {"reset_key": key, "expires_at": expires_at}
