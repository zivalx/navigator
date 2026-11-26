"""configure_logging(): app INFO logs emitted once; chatty libs stay quiet."""
import io
import logging

import pytest

from app.logging_config import configure_logging


@pytest.fixture()
def bare_root():
    """Root with no handlers, like a fresh gunicorn/uvicorn worker."""
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    levels = {n: logging.getLogger(n).level for n in ("app", "httpx", "urllib3")}
    for n in levels:
        logging.getLogger(n).setLevel(logging.NOTSET)
    yield root
    root.handlers[:] = saved[0]
    root.setLevel(saved[1])
    for n, lvl in levels.items():
        logging.getLogger(n).setLevel(lvl)


def _strip_root(root):
    # pytest re-installs its capture handlers on root for each phase, so the
    # fixture can't do this; the test body must.
    root.handlers.clear()


def test_app_info_emitted_once_and_httpx_suppressed(bare_root):
    _strip_root(bare_root)
    configure_logging()
    configure_logging()  # idempotent
    assert len(bare_root.handlers) == 1
    buf = io.StringIO()
    bare_root.handlers[0].setStream(buf)

    logging.getLogger("app.tasks.leader").info("hello-app")
    logging.getLogger("httpx").info("hello-httpx")
    logging.getLogger("httpx").warning("warn-httpx")

    lines = buf.getvalue().splitlines()
    assert sum("hello-app" in ln for ln in lines) == 1
    assert "INFO app.tasks.leader: hello-app" in buf.getvalue()
    assert "hello-httpx" not in buf.getvalue()
    assert "warn-httpx" in buf.getvalue()


def test_existing_root_handler_is_not_duplicated(bare_root):
    _strip_root(bare_root)
    existing = logging.StreamHandler(io.StringIO())
    bare_root.addHandler(existing)
    configure_logging()
    assert bare_root.handlers == [existing]
    assert logging.getLogger("app").getEffectiveLevel() == logging.INFO
