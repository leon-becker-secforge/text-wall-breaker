from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

import pytest
from support import Reply, fetch

from text_wall_breaker import listener as listener_module
from text_wall_breaker.listener import LoopbackListener, SessionListener, Submission
from text_wall_breaker.markdown import render_markdown


def post(port: int, query: str, payload: object, content_type: str = "text/plain"):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/submit{query}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": content_type},
        method="POST",
    )
    # Closed before returning (status and headers stay readable), so no socket outlives it.
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 — loopback
            response.read()
            return response
    except urllib.error.HTTPError as error:
        error.close()
        raise


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
    monkeypatch.setattr(listener_module, "ABANDON_SECONDS", 1.0)
    with LoopbackListener("tok") as listener:
        thread, result = wait_in_thread(listener)
        # Three probes 0.4 s apart: past the 1 s window in total, well within it each.
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
        url = f"http://127.0.0.1:{listener.port}/submit?t=tok"
        assert fetch(url, raw=b"not json").status == 400
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
        assert fetch(f"http://127.0.0.1:{listener.port}/nope?t=tok", {}).status == 404
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


def _published_report(session: SessionListener, title: str = "Report") -> int:
    generation = session.publish(lambda generation: "<html>")
    session.mark_report(title)
    return generation


def test_no_call_can_claim_the_session_while_a_report_is_unread(session):
    generation = _published_report(session)
    assert not session.try_claim()
    # The refused claim must not leave the session locked.
    assert session.deliver(Submission(kind="acknowledge", data={}), generation)
    assert session.try_claim()
    session.release()


def test_acknowledging_the_report_frees_the_session_for_questions(session):
    generation = _published_report(session)
    assert session.deliver(Submission(kind="acknowledge", data={}), generation)
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


def test_a_page_no_tab_loaded_is_no_longer_open(session, monkeypatch):
    # The caller reports not_delivered and stops waiting: a tab that loads late must say
    # the question is closed instead of taking an answer nobody reads.
    monkeypatch.setattr(listener_module, "DELIVERY_SECONDS", 0.1)
    generation = session.publish(lambda generation: "<html>")
    assert session.status()["open"]
    assert not session.show_in_tab(generation, lambda url: None)
    assert not session.status()["open"]


def test_a_slow_page_build_does_not_stall_the_tab(session):
    first = session.publish(lambda generation: "old")
    building, release = threading.Event(), threading.Event()

    def slow(generation: int) -> str:
        building.set()
        release.wait(5)
        return "new"

    thread = threading.Thread(target=session.publish, args=(slow,))
    thread.start()
    try:
        assert building.wait(5)
        started = time.monotonic()
        # Until the new page exists, the tab keeps getting the old one, as a whole.
        assert session.status()["generation"] == first
        assert session.current_page() == (b"old", first)
        assert time.monotonic() - started < 0.5
    finally:
        release.set()
        thread.join(5)
    assert session.current_page() == (b"new", first + 1)


def _answer(session: SessionListener, generation: object) -> int:
    payload = {"kind": "submit", "data": {"selected": ["a"]}, "generation": generation}
    return fetch(f"http://127.0.0.1:{session.port}/submit?t=tok", payload).status


def test_an_answer_from_an_older_page_is_refused(session):
    first = session.publish(lambda generation: "<html>")
    second = session.publish(lambda generation: "<html>")
    assert _answer(session, first) == 409
    assert _answer(session, None) == 409
    assert _answer(session, second) == 200
    assert session.wait_for_answer() == Submission("submit", {"selected": ["a"]})


def test_an_answer_after_the_call_ended_is_refused(session):
    generation = session.publish(lambda generation: "<html>")
    session.end_question()
    assert _answer(session, generation) == 409


def test_only_the_current_pages_progress_becomes_the_draft(session):
    first = session.publish(lambda generation: "<html>")
    second = session.publish(lambda generation: "<html>")
    url = f"http://127.0.0.1:{session.port}/submit?t=tok"
    fetch(url, {"kind": "progress", "data": {"mine": True}, "generation": second})
    assert (
        fetch(url, {"kind": "progress", "data": {"stale": True}, "generation": first}).status == 204
    )
    assert session.progress() == {"mine": True}


def test_a_second_answer_to_a_one_off_page_is_refused():
    with LoopbackListener("tok") as listener:
        assert listener.deliver(Submission("submit", {"selected": ["a"]}))
        assert not listener.deliver(Submission("submit", {"selected": ["b"]}))
        assert listener._submission == Submission("submit", {"selected": ["a"]})


def test_an_interrupted_call_stops_waiting_for_its_tab(session, monkeypatch):
    monkeypatch.setattr(listener_module, "DELIVERY_SECONDS", 30.0)
    generation = session.publish(lambda generation: "<html>")
    cancel = threading.Event()
    cancel.set()
    started = time.monotonic()
    with listener_module.cancellable(cancel):
        assert not session.show_in_tab(generation, lambda url: None)
    assert time.monotonic() - started < 2


def test_an_idle_connection_does_not_block_the_session(session):
    with _idle_connection(session.port):
        assert fetch(f"http://127.0.0.1:{session.port}/status?t=tok", timeout=2).status == 200


def test_an_idle_connection_does_not_hold_off_abandonment():
    with LoopbackListener("tok") as listener, _idle_connection(listener.port):
        listener.mark_seen()
        thread, result = wait_in_thread(listener)
        thread.join(timeout=3)
        assert not thread.is_alive()
        assert result == [None]


def test_a_post_without_the_token_is_no_activity(monkeypatch):
    monkeypatch.setattr(listener_module, "_POLL_SECONDS", 0.05)
    monkeypatch.setattr(listener_module, "ABANDON_SECONDS", 1.0)
    with LoopbackListener("tok") as listener:
        listener.mark_seen()
        started = time.monotonic()
        thread, result = wait_in_thread(listener)
        time.sleep(0.6)
        with pytest.raises(urllib.error.HTTPError):
            post(listener.port, "?t=wrong", {"kind": "probe"})
        # Counted as activity, the post would move abandonment from 1 s to past 1.6 s.
        thread.join(timeout=max(0.0, started + 1.3 - time.monotonic()))
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


_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _get_asset(session: SessionListener, path: Path) -> Reply:
    return fetch(f"{session.asset_base_url}&path={quote(str(path), safe='')}")


def test_asset_serves_an_image_a_page_references(session, tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(_PNG)
    render_markdown(f"![s]({image})", asset_base_url=session.asset_base_url)
    response = _get_asset(session, image)
    assert response.status == 200
    assert response.body == _PNG
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in response.headers["Content-Security-Policy"]
    assert response.headers["Access-Control-Allow-Origin"] is None


def test_asset_refuses_an_image_no_page_references(session, tmp_path):
    image = tmp_path / "other.png"
    image.write_bytes(_PNG)
    assert _get_asset(session, image).status == 404


def test_asset_refuses_an_image_only_another_sessions_page_references(session, tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(_PNG)
    other = SessionListener("other")
    try:
        render_markdown(f"![s]({image})", asset_base_url=other.asset_base_url)
        assert _get_asset(session, image).status == 404
    finally:
        other.close()


def test_asset_refuses_an_image_of_a_replaced_page(session, tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(_PNG)
    session.publish(
        lambda g: render_markdown(f"![s]({image})", asset_base_url=session.asset_base_url)
    )
    assert _get_asset(session, image).status == 200
    session.publish(lambda g: "<p>next question</p>")
    assert _get_asset(session, image).status == 404


def test_asset_refuses_a_symlink(session, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    link = tmp_path / "innocent.png"
    link.symlink_to(secret)
    render_markdown(f"![s]({link})", asset_base_url=session.asset_base_url)
    assert _get_asset(session, link).status == 404
