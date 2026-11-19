from __future__ import annotations

import logging
import stat

import pytest

from text_wall_breaker import log
from text_wall_breaker.ask import not_delivered

_TOKEN = "0123456789abcdef" * 2


@pytest.fixture(autouse=True)
def state_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv(log.LOG_ENV_VAR, raising=False)
    yield tmp_path
    for handler in list(log.logger.handlers):
        handler.close()
        log.logger.removeHandler(handler)


def _flush() -> None:
    for handler in log.logger.handlers:
        handler.flush()


def test_the_session_token_never_reaches_the_file():
    path = log.configure()
    assert path is not None
    log.logger.info("opening http://127.0.0.1:1/?t=%s for generation 1", _TOKEN)
    log.logger.info("opener %s", ["xdg-open", f"http://127.0.0.1:1/asset?t={_TOKEN}&path=x"])
    _flush()
    text = path.read_text()
    assert _TOKEN not in text
    assert text.count("t=…") == 2


def test_the_file_and_its_directory_are_private():
    path = log.configure()
    assert path is not None
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_an_existing_loose_directory_is_tightened(state_home):
    directory = state_home / "text-wall-breaker"
    directory.mkdir(mode=0o775)
    directory.chmod(0o775)
    log.configure()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_a_large_log_is_rotated_when_the_server_starts():
    path = log.log_path()
    path.parent.mkdir(parents=True)
    path.write_text("x" * 1_100_000)
    log.configure()
    assert path.with_name(path.name + ".1").stat().st_size == 1_100_000
    assert path.stat().st_size == 0


def test_reconfiguring_closes_the_previous_handler():
    log.configure()
    (first,) = log.logger.handlers
    log.configure()
    assert isinstance(first, logging.FileHandler)
    assert first.stream is None


def test_an_unknown_level_is_reported_and_info_is_used(monkeypatch, capsys):
    monkeypatch.setenv(log.LOG_ENV_VAR, "verbose")
    log.configure()
    assert log.logger.level == logging.INFO
    assert "verbose" in capsys.readouterr().err


def test_not_delivered_names_the_log_in_use():
    path = log.configure()
    assert str(path) in not_delivered()["reason"]


def test_not_delivered_says_when_logging_is_off(monkeypatch):
    monkeypatch.setenv(log.LOG_ENV_VAR, "off")
    log.configure()
    reason = not_delivered()["reason"]
    assert "local/state" not in reason
    assert log.LOG_ENV_VAR in reason
