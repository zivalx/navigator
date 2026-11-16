import hmac

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..schemas.export import PositionsExport
from ..services.export import build_positions_export

router = APIRouter()


def require_export_token(x_export_token: str = Header(default="")):
    configured = settings.export_token
    if not configured:
        raise HTTPException(status_code=503, detail="export disabled")
    if not hmac.compare_digest(x_export_token.encode(), configured.encode()):
        raise HTTPException(status_code=401, detail="invalid export token")


@router.get("/positions", response_model=PositionsExport,
            dependencies=[Depends(require_export_token)])
def get_positions(db: Session = Depends(get_db)):
    """Read-only holdings + watchlist export consumed by trender."""
    return build_positions_export(db)
