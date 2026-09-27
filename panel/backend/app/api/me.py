from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select, update

from app.api.audit import audit
from app.api.auth import raise_password_errors
from app.api.deps import ClockDep, Db, PrincipalDep, SettingsDep, UserDep
from app.db.models import Config, Session, User
from app.domain.rules import UserState, expires_on, user_status
from app.errors import ApiError
from app.security.passwords import hash_password, verify_password

router = APIRouter(prefix="/api/me", tags=["me"])


class PasswordIn(BaseModel):
    old: str
    new: str


def user_state(user: User) -> UserState:
    return UserState(user.blocked_by, user.deleting_at, user.expires_at)


@router.get("")
async def me(principal: UserDep, db: Db, clock: ClockDep, settings: SettingsDep) -> dict:
    user = await db.get(User, principal.subject_id)
    count = (await db.execute(
        select(func.count()).select_from(Config).where(Config.user_id == user.id, Config.deleted_at.is_(None))
    )).scalar_one()
    return {
        "id": user.id,
        "display_name": user.display_name,
        "login": user.login,
        "status": user_status(user_state(user), clock.now()),
        "expires_on": expires_on(user.expires_at, settings.tz),
        "max_configs": user.max_configs,
        "configs_count": count,
    }


@router.post("/password", status_code=204)
async def change_password(body: PasswordIn, principal: PrincipalDep, db: Db, clock: ClockDep) -> None:
    """Б.4, for users and administrators alike: other sessions of the account are signed out."""
    user = await db.get(User, principal.subject_id)
    if not verify_password(user.password_hash or "", body.old):
        raise ApiError(403, "wrong_password", "current password is wrong")
    raise_password_errors(body.new, [user.login or "", user.display_name])
    user.password_hash = hash_password(body.new)
    await db.execute(
        update(Session)
        .where(Session.user_id == user.id, Session.id != principal.session_id, Session.revoked_at.is_(None))
        .values(revoked_at=clock.now())
    )
    audit(db, principal.actor, "password_change", principal.actor)
    await db.commit()
