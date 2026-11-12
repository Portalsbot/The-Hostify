from __future__ import annotations

import logging
from collections import deque
from threading import Lock

_LINES: deque[str] = deque(maxlen=2_000)
_LOCK = Lock()
_HANDLER: logging.Handler | None = None


class _RingBufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            with _LOCK:
                _LINES.append(line)
        except Exception:
            self.handleError(record)


def install_log_capture() -> None:
    """Attach a bounded in-memory controller log buffer exactly once."""
    global _HANDLER
    if _HANDLER is not None:
        return
    handler = _RingBufferHandler(level=logging.INFO)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    ))
    logging.getLogger().addHandler(handler)
    _HANDLER = handler


def get_recent_logs(lines: int = 40) -> list[str]:
    if not isinstance(lines, int) or not 1 <= lines <= 200:
        raise ValueError("Log count must be between 1 and 200.")
    with _LOCK:
        return list(_LINES)[-lines:]
