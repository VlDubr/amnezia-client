from fastapi import APIRouter

from app.api.deps import AdminDep, Db
from app.db.models import Job
from app.errors import ApiError

router = APIRouter(prefix="/api/jobs", tags=["admin"])


def job_out(job: Job) -> dict:
    return {"id": job.id, "kind": job.kind, "status": job.status, "attempts": job.attempts,
            "last_error": job.last_error}


@router.get("/{job_id}")
async def get_job(job_id: int, _: AdminDep, db: Db) -> dict:
    job = await db.get(Job, job_id)
    if job is None:
        raise ApiError(404, "not_found", "job not found")
    return job_out(job)
