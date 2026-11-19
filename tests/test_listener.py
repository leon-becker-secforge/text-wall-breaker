from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from text_wall_breaker import listener as listener_module
from text_wall_breaker.listener import LoopbackListener, SessionListener, Submission


def post(port: int, query: str, payload: object, content_type: str = "text/plain"):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/submit{query}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": content_type},
        method="POST",
    )
    return urllib.request.urlopen(request, timeout=5)  # noqa: S310 — fixed loopback URL


@pytest.fixture(autouse=True)
def short_abandon_window(monkeypatch):
    """A silent page counts as abandoned after 0.5 s here instead of 3 minutes."""
    monkeypatch.setattr(listener_module, "ABANDON_SECONDS", 0.5)


def wait_in_thread(listener: LoopbackListener):
    """Run the blocking wait() off the test thread and hand back its result."""
    result: list[object] = []
    thread = threading.Thread(target=lambda: result.append(listener.wait()))
    thread.start()
    return thread, result


def test_binds_an_ephemeral_port():
    with LoopbackListener("tok") as listener:
        assert listener.port > 0


def test_accepts_a_token_matched_submission():
    with LoopbackListener("tok") as listener:
        thread, result = wait_in_thread(listener)
        response = post(listener.port, "?t=tok", {"kind": "submit", "data": {"selected": ["a"]}})
        assert response.status == 200
        thread.join(timeout=5)

    assert result[0].kind == "submit"
    assert result[0].data == {"selected": ["a"]}


def test_cancel_is_distinguishable_from_a_submission():
    with LoopbackListener("tok") as listener:
        thread, result = wait_in_thread(listener)
        post(listener.port, "?t=tok", {"kind": "cancel", "data": {}})
        thread.join(timeout=5)

    assert result[0].kind == "cancel"


def test_a_silent_page_is_abandoned():
    with LoopbackListener("tok") as listener:
        listener.mark_seen()
        assert listener.wait() is None


def test_a_heartbeat_keeps_the_wait_alive(monkeypatch):
    monkeypatch.setattr(listener_module, "ABANDON_SECONDS", 0.6)
    with LoopbackListener("tok") as listener:
        thread, result = wait_in_thread(listener)
        # Three probes 0.4 s apart: past the 0.6 s window in total, never within it alone.
        for _ in range(3):
            time.sleep(0.4)
            assert post(listener.port, "?t=tok", {"kind": "probe"}).status == 204
        post(listener.port, "?t=tok", {"kind": "submit", "data": {}})
        thread.join(timeout=5)

    assert result[0].kind == "submit"


# A probe proves the token and CORS response are readable from a real page load. If it
# consumed the submission, that verification would cost the round trip it verifies.
def test_probe_does_not_end_the_wait():
    with LoopbackListener("tok") as listener:
        thread, result = wait_in_thread(listener)
        assert post(listener.port, "?t=tok", {"kind": "probe"}).status == 204
        post(listener.port, "?t=tok", {"kind": "submit", "data": {}})
        thread.join(timeout=5)

    assert result[0].kind == "submit"


@pytest.mark.parametrize("query", ["", "?t=wrong"])
def test_wrong_token_is_rejected(query):
    with LoopbackListener("tok") as listener:
        thread, result = wait_in_thread(listener)
        with pytest.raises(urllib.error.HTTPError) as caught:
            post(listener.port, query, {"kind": "submit", "data": {}})
        assert caught.value.code == 403
        thread.join(timeout=5)

    assert result[0] is None


def test_rejection_leaves_the_connection_usable():
    # Rejecting without draining the body desyncs keep-alive and breaks the *next*
    # request with a 400, which would strand a page that retried after a stale token.
    with LoopbackListener("tok") as listener:
        thread, result = wait_in_thread(listener)
        with pytest.raises(urllib.error.HTTPError):
            post(listener.port, "?t=wrong", {"kind": "submit", "data": {}})
        assert post(listener.port, "?t=tok", {"kind": "submit", "data": {}}).status == 200
        thread.join(timeout=5)

    assert result[0].kind == "submit"


def test_malformed_body_is_rejected_without_ending_the_wait():
    with LoopbackListener("tok") as listener:
        thread, result = wait_in_thread(listener)
        request = urllib.request.Request(
            f"http://127.0.0.1:{listener.port}/submit?t=tok",
            data=b"not json",
            headers={"Content-Type": "text/plain"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)  # noqa: S310 — fixed loopback URL
        assert caught.value.code == 400
        post(listener.port, "?t=tok", {"kind": "submit", "data": {}})
        thread.join(timeout=5)

    assert result[0].kind == "submit"


def test_unknown_kind_is_rejected():
    with LoopbackListener("tok") as listener:
        thread, result = wait_in_thread(listener)
        with pytest.raises(urllib.error.HTTPError) as caught:
            post(listener.port, "?t=tok", {"kind": "whatever"})
        assert caught.value.code == 400
        thread.join(timeout=5)

    assert result[0] is None


def test_wrong_path_is_a_404():
    with LoopbackListener("tok") as listener:
        thread, _ = wait_in_thread(listener)
        request = urllib.request.Request(
            f"http://127.0.0.1:{listener.port}/nope?t=tok", data=b"{}", method="POST"
        )
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)  # noqa: S310 — fixed loopback URL
        assert caught.value.code == 404
        thread.join(timeout=5)


def test_theme_kind_persists_and_does_not_end_the_wait(monkeypatch):
    saved = []
    monkeypatch.setattr(listener_module, "save_theme", saved.append)
    with LoopbackListener("tok") as listener:
        thread, result = wait_in_thread(listener)
        response = post(listener.port, "?t=tok", {"kind": "theme", "data": {"theme": "dark"}})
        assert response.status == 204
        thread.join(timeout=5)

    assert saved == ["dark"]
    assert result[0] is None


def test_theme_kind_with_a_non_string_theme_is_ignored(monkeypatch):
    saved = []
    monkeypatch.setattr(listener_module, "save_theme", saved.append)
    with LoopbackListener("tok") as listener:
        thread, _ = wait_in_thread(listener)
        response = post(listener.port, "?t=tok", {"kind": "theme", "data": {"theme": 3}})
        assert response.status == 204
        thread.join(timeout=5)

    assert saved == []


def test_response_carries_the_cors_header():
    # Without it the browser treats the response as opaque, so the page can never tell a
    # rejected submission from an accepted one.
    with LoopbackListener("tok") as listener:
        thread, _ = wait_in_thread(listener)
        response = post(listener.port, "?t=tok", {"kind": "submit", "data": {}})
        assert response.headers["Access-Control-Allow-Origin"] == "*"
        thread.join(timeout=5)


@pytest.fixture
def session():
    listener = SessionListener("tok")
    yield listener
    listener.close()


def _published_report(session: SessionListener, title: str = "Report") -> None:
    session.publish(lambda generation: "<html>")
    session.mark_report(title)


def test_no_call_can_claim_the_session_while_a_report_is_unread(session):
    _published_report(session)
    assert not session.try_claim()
    # The refused claim must not leave the session locked.
    session.deliver(Submission(kind="acknowledge", data={}))
    assert session.try_claim()
    session.release()


def test_acknowledging_the_report_frees_the_session_for_questions(session):
    _published_report(session)
    session.deliver(Submission(kind="acknowledge", data={}))
    assert session.report_status() == {"state": "acknowledged", "title": "Report"}
    assert session.try_claim()
    session.release()


def test_a_report_whose_tab_stopped_polling_no_longer_blocks_questions(session, monkeypatch):
    _published_report(session)
    monkeypatch.setattr(listener_module, "REUSE_GRACE_SECONDS", -1.0)
    assert session.try_claim()
    session.release()


def test_status_polls_keep_an_unread_report_held(session):
    _published_report(session)
    session.status()
    assert session.holds_unread_report()


def test_publishing_anything_else_clears_the_report_status(session):
    assert session.report_status() == {"state": "none"}
    _published_report(session)
    assert session.report_status() == {"state": "unacknowledged", "title": "Report"}
    session.publish(lambda generation: "<html>")
    assert session.report_status() == {"state": "none"}


def _idle_connection(port: int) -> socket.socket:
    """A connection that sends nothing, as a browser's speculative preconnect does."""
    return socket.create_connection(("127.0.0.1", port))


def test_an_idle_connection_does_not_block_the_session(session):
    with _idle_connection(session.port):
        url = f"http://127.0.0.1:{session.port}/status?t=tok"
        with urllib.request.urlopen(url, timeout=2) as response:
            assert response.status == 200


def test_an_idle_connection_does_not_hold_off_abandonment():
    with LoopbackListener("tok") as listener, _idle_connection(listener.port):
        listener.mark_seen()
        thread, result = wait_in_thread(listener)
        thread.join(timeout=3)
        assert not thread.is_alive()
        assert result == [None]


def test_a_post_without_the_token_is_no_activity(monkeypatch):
    monkeypatch.setattr(listener_module, "_POLL_SECONDS", 0.05)
    with LoopbackListener("tok") as listener:
        listener.mark_seen()
        started = time.monotonic()
        thread, result = wait_in_thread(listener)
        time.sleep(0.3)
        with pytest.raises(urllib.error.HTTPError):
            post(listener.port, "?t=wrong", {"kind": "probe"})
        # Counted as activity, the post would move abandonment from 0.5 s to past 0.8 s.
        thread.join(timeout=max(0.0, started + 0.7 - time.monotonic()))
        assert result == [None]


def test_a_session_post_without_the_token_is_no_activity(session):
    session.note_activity()
    time.sleep(0.2)
    with pytest.raises(urllib.error.HTTPError):
        post(session.port, "?t=wrong", {"kind": "probe"})
    assert session.idle_seconds() >= 0.2


@pytest.mark.parametrize(("length", "status"), [("abc", 400), (str(2 << 20), 413)])
def test_a_bad_content_length_gets_an_error_status(session, length, status):
    with socket.create_connection(("127.0.0.1", session.port)) as conn:
        conn.sendall(f"POST /submit?t=tok HTTP/1.0\r\nContent-Length: {length}\r\n\r\n".encode())
        conn.settimeout(3)
        assert conn.recv(64).split(b" ")[1] == str(status).encode()
