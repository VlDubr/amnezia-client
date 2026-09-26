from datetime import datetime
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminDep, Db
from app.db.models import AuditLog

router = APIRouter(prefix="/api/admin/audit", tags=["admin"])


def audit(db: AsyncSession, actor: str, action: str, target: str, **details: Any) -> None:
    """Adds an audit entry to the current transaction; the caller commits."""
    db.add(AuditLog(actor=actor, action=action, target=target, details_json=details))


@router.get("")
async def list_audit(_: AdminDep, db: Db, limit: int = 100, before: int | None = None) -> list[dict]:
    query = select(AuditLog).order_by(AuditLog.id.desc()).limit(min(max(limit, 1), 500))
    if before is not None:
        query = query.where(AuditLog.id < before)
    rows = (await db.execute(query)).scalars().all()
    return [_row(r) for r in rows]


def _row(r: AuditLog) -> dict:
    ts: datetime = r.ts
    return {"id": r.id, "actor": r.actor, "action": r.action, "target": r.target, "details": r.details_json,
            "ts": ts.isoformat()}
