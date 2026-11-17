from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String
from sqlalchemy.orm import Session

from ..database import Base

CUTOVER_MARKER = "minor_units_cutover"
ALERTS_CONVERTED_MARKER = "minor_unit_alerts_converted"


class MaintenanceRun(Base):
    __tablename__ = "maintenance_runs"

    name = Column(String, primary_key=True)
    ran_at = Column(DateTime(timezone=True), nullable=False)


def get_marker(db: Session, name: str) -> datetime | None:
    row = db.get(MaintenanceRun, name)
    return row.ran_at if row else None


def set_marker(db: Session, name: str, at: datetime | None = None) -> None:
    """Record a marker once; an existing row is left unchanged."""
    if db.get(MaintenanceRun, name) is not None:
        return
    db.add(MaintenanceRun(name=name, ran_at=at or datetime.now(timezone.utc)))
    db.commit()
