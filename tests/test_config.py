from __future__ import annotations

import pytest

from text_wall_breaker.config import Config, parse_args


def test_no_arguments_give_the_defaults():
    assert parse_args([]) == Config(opener=None, accent=None, log_level="info", glossary=True)


def test_every_option_is_parsed():
    config = parse_args(
        [
            "--opener",
            "firefox --new-tab",
            "--accent",
            "#2563eb",
            "--log-level",
            "debug",
            "--no-glossary",
        ]
    )
    assert config == Config(
        opener="firefox --new-tab", accent="#2563eb", log_level="debug", glossary=False
    )


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--accent", "blue"], "'blue' is not a #rrggbb hex colour"),
        (["--log-level", "verbose"], "invalid choice: 'verbose'"),
        (["--acent", "#2563eb"], "unrecognized arguments: --acent"),
    ],
)
def test_a_bad_option_refuses_to_start(argv, message, capsys):
    with pytest.raises(SystemExit) as exit_info:
        parse_args(argv)
    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err
