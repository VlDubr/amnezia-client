from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.domain.clock import Clock
from app.services.sync import enqueue_user_sync


async def expire_users(db: AsyncSession, clock: Clock) -> list[int]:
    """Blocks users whose access period has ended and schedules removal of their configs from servers."""
    users = (await db.execute(
        select(User).where(User.blocked_by.is_(None), User.deleting_at.is_(None), User.expires_at <= clock.now())
        .with_for_update(skip_locked=True).order_by(User.id))).scalars().all()
    for user in users:
        user.blocked_by = "expiry"
        await enqueue_user_sync(db, user.id)
    await db.commit()
    return [u.id for u in users]
