"""One-shot logging setup so application INFO logs reach the container log.

gunicorn and uvicorn configure only their own loggers (gunicorn.*, uvicorn.*)
and leave the root logger without handlers, so `logging.getLogger("app....")`
INFO records were dropped (only WARNING+ leaked through logging.lastResort).
"""
import logging

from .config import settings

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_HANDLER_NAME = "navigator-stderr"
# Chatty libraries stay at WARNING unless something already set their level.
_NOISY = ("httpx", "httpcore", "apscheduler", "sqlalchemy", "urllib3", "yfinance")


def _resolve_level(name: str) -> int:
    level = logging.getLevelName(str(name).strip().upper())
    return level if isinstance(level, int) else logging.INFO


def configure_logging() -> None:
    """Idempotent. Safe under gunicorn workers, uvicorn dev and pytest."""
    root = logging.getLogger()
    # Add our handler to root only when nothing is installed there yet; if a
    # host (pytest, a custom dictConfig) already put handlers on root, adding
    # another would print every line twice. Server loggers don't propagate to
    # root, so access/error lines are unaffected either way.
    if not root.handlers:
        handler = logging.StreamHandler()  # stderr
        handler.set_name(_HANDLER_NAME)
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(handler)
    logging.getLogger("app").setLevel(_resolve_level(settings.log_level))
    for name in _NOISY:
        lg = logging.getLogger(name)
        if lg.level == logging.NOTSET:
            lg.setLevel(logging.WARNING)
