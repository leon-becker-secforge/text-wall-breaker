"""The one `SessionListener` shared by every `ask` and `show` call in this process.

Factored out of `ask.py` so both tools reuse the same already-open browser tab instead
of each keeping its own — a `show` right after an `ask` (or vice versa) lands in the
same tab rather than opening a second one. Created lazily, not at import time, so
importing this module never binds a port as a side effect (tests import it freely).
"""

from __future__ import annotations

import secrets
import threading

from .listener import SessionListener

__all__ = ["get_shared_session"]

_lock = threading.Lock()
_session: SessionListener | None = None


def get_shared_session() -> SessionListener:
    global _session
    with _lock:
        if _session is None:
            _session = SessionListener(secrets.token_hex(16))
        return _session
