import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..services import health as health_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    """Shallow liveness: no DB, no I/O."""
    return {"status": "ok", "checks": {"process": {"ok": True, "detail": "up"}}}


@router.get("/health/deep")
def health_deep(db: Session = Depends(get_db)):
    """Sync `def` so FastAPI runs it in the threadpool. 503 only for `down`."""
    health_service.arm_statement_timeout(db)
    checks = health_service.deep_checks(db, health_service._utcnow())
    status = health_service.overall_status(checks)
    body = {
        "status": status,
        "checks": {k: {"ok": c.ok, "detail": c.detail} for k, c in checks.items()},
    }
    return JSONResponse(body, status_code=503 if status == "down" else 200)
