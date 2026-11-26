from sqlalchemy import Column, DateTime, Text

from ..database import Base


class JobHeartbeat(Base):
    """Last start, success and error of each scheduled job (created by 0002).

    `last_error` holds redacted exception text only, never quantities."""
    __tablename__ = "job_heartbeats"

    job_name = Column(Text, primary_key=True)
    last_started_at = Column(DateTime(timezone=True))
    last_success_at = Column(DateTime(timezone=True))
    last_error_at = Column(DateTime(timezone=True))
    last_error = Column(Text)
