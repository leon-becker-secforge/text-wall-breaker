from __future__ import annotations

import pytest

from text_wall_breaker import session as session_module


@pytest.fixture(autouse=True)
def _isolated_sessions():
    """Give every test its own shared-tab session and live registry, closed after.

    Both are process-wide: without this, one test's claimed or leaked session would make
    a later one fall back to a one-off tab, and live sessions would pile up between tests.
    """
    original, live = session_module._session, dict(session_module._live)
    session_module._session = None
    session_module._live.clear()
    yield
    if session_module._session is not None:
        session_module._session.close()
    session_module._session = original
    session_module._live.clear()
    session_module._live.update(live)
