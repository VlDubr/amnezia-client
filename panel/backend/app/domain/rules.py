"""Pure business rules shared by the API, reconcile and scheduled jobs (spec §5)."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class UserState:
    blocked_by: str | None
    deleting_at: datetime | None
    expires_at: datetime | None


def is_expired(expires_at: datetime | None, now: datetime) -> bool:
    return expires_at is not None and expires_at <= now


def is_user_active(user: UserState, now: datetime) -> bool:
    return user.blocked_by is None and user.deleting_at is None and not is_expired(user.expires_at, now)


def is_config_active(cfg_blocked_by: str | None, cfg_deleted_at: datetime | None,
                     user: UserState | None, now: datetime) -> bool:
    """A config works on the server only if neither it nor its owner is blocked, deleted or expired.

    A config without an owner (imported orphan) depends only on its own state.
    """
    if cfg_blocked_by is not None or cfg_deleted_at is not None:
        return False
    return user is None or is_user_active(user, now)


def user_status(user: UserState, now: datetime) -> str:
    if user.deleting_at is not None:
        return "deleting"
    if user.blocked_by == "admin":
        return "blocked"
    if user.blocked_by == "expiry" or is_expired(user.expires_at, now):
        return "expired"
    return "active"


def user_can_unblock(cfg_blocked_by: str | None) -> bool:
    return cfg_blocked_by == "user"


def expiry_instant(last_day: date, tz: str) -> datetime:
    """The chosen date is the last day of access: access ends at 00:00 of the next day in the panel timezone."""
    local = datetime.combine(last_day + timedelta(days=1), time.min, tzinfo=ZoneInfo(tz))
    return local.astimezone(UTC)


def after_expiry_change(blocked_by: str | None, new_expires_at: datetime | None, now: datetime) -> str | None:
    """User block reason after the admin changes the expiry; an admin block is never lifted here."""
    if blocked_by == "admin":
        return "admin"
    return "expiry" if is_expired(new_expires_at, now) else None


def traffic_delta(last: int | None, last_session: str | None, new: int, new_session: str | None) -> int:
    """Traffic since the previous sample; a counter reset or a new session counts the new value in full."""
    if last is None or new < last or last_session != new_session:
        return new
    return new - last
