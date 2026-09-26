from datetime import date

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import AdminDep, Db
from app.db.models import TrafficDaily

router = APIRouter(prefix="/api/admin/traffic", tags=["admin"])


@router.get("")
async def traffic(_: AdminDep, db: Db, user_id: int | None = None, server_id: int | None = None,
                  from_: date | None = Query(default=None, alias="from"),
                  to: date | None = None) -> dict:
    query = (select(TrafficDaily.day, TrafficDaily.server_id, func.sum(TrafficDaily.rx), func.sum(TrafficDaily.tx))
             .group_by(TrafficDaily.day, TrafficDaily.server_id).order_by(TrafficDaily.day, TrafficDaily.server_id))
    if user_id is not None:
        query = query.where(TrafficDaily.user_id == user_id)
    if server_id is not None:
        query = query.where(TrafficDaily.server_id == server_id)
    if from_ is not None:
        query = query.where(TrafficDaily.day >= from_)
    if to is not None:
        query = query.where(TrafficDaily.day <= to)
    rows = [{"day": d.isoformat(), "server_id": sid, "rx": int(rx), "tx": int(tx)}
            for d, sid, rx, tx in await db.execute(query)]
    return {"rows": rows, "total": {"rx": sum(r["rx"] for r in rows), "tx": sum(r["tx"] for r in rows)}}
