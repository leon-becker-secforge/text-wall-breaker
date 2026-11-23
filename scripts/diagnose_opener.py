#!/usr/bin/env python3
"""Find out why text-wall-breaker's page sometimes never appears in the browser.

Standalone, standard library only: copy it to the machine in question and run

    python3 diagnose_opener.py                # test webbrowser.open and xdg-open
    python3 diagnose_opener.py --repeat 5     # five rounds each, to catch flakiness
    python3 diagnose_opener.py --cmd "firefox --new-tab"   # also try your own command

It serves a tiny page on 127.0.0.1, hands its URL to each opener the way text-wall-breaker
does, and records whether the browser actually requested the page, and how fast. Paste
the whole output back. Nothing leaves the machine; nothing is installed.
"""

# ruff: noqa: S603, S607
# A diagnostic: it runs the opener commands the user names, on purpose.
from __future__ import annotations

import argparse
import http.server
import os
import platform
import shlex
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable

WAIT_SECONDS = 30.0  # text-wall-breaker's DELIVERY_SECONDS
OPEN_TIMEOUT = 20.0  # text-wall-breaker's _OPEN_TIMEOUT_SECONDS

requests: list[tuple[float, str, str]] = []
lock = threading.Lock()


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        with lock:
            requests.append((time.monotonic(), self.path, self.headers.get("User-Agent", "")))
        body = f"<h1>opener test {self.path}</h1><p>You can close this tab.</p>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


def run(argv: list[str], timeout: float = 5.0) -> str:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"(failed: {exc})"
    return (done.stdout + done.stderr).strip() or "(no output)"


def environment() -> None:
    print("== Environment")
    print(f"platform: {platform.platform()}")
    for name in (
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XDG_CURRENT_DESKTOP",
        "DESKTOP_SESSION",
        "XDG_SESSION_TYPE",
        "BROWSER",
    ):
        print(f"{name}={os.environ.get(name, '')!r}")
    print(f"xdg-open: {shutil.which('xdg-open')}")
    if shutil.which("xdg-settings"):
        print(f"default browser: {run(['xdg-settings', 'get', 'default-web-browser'])}")
    if shutil.which("xdg-mime"):
        print(f"http handler: {run(['xdg-mime', 'query', 'default', 'x-scheme-handler/http'])}")
    if shutil.which("wmctrl"):
        print(f"window manager: {run(['wmctrl', '-m']).splitlines()[0]}")
    else:
        print("window manager: wmctrl not installed")
    try:
        print(f"webbrowser default: {webbrowser.get().__class__.__name__}")
    except webbrowser.Error as exc:
        print(f"webbrowser default: none ({exc})")
    if shutil.which("pgrep"):
        names = "firefox|chromium|chrome|brave|librewolf"
        print(f"browser processes: {run(['pgrep', '-a', '-i', '-x', names])}")
    print()


def attempt(
    label: str, opener: Callable[[str], object], path: str, base: str, ask: bool
) -> dict[str, object]:
    start = time.monotonic()
    outcome = ""
    try:
        outcome = str(opener(base + path))
    except Exception as exc:  # report everything: this is a diagnostic
        outcome = f"raised {exc!r}"
    opener_seconds = time.monotonic() - start
    seen_at = None
    deadline = start + WAIT_SECONDS
    while time.monotonic() < deadline:
        with lock:
            hit = [t for t, p, _ in requests if p == path and t >= start]
        if hit:
            seen_at = hit[0] - start
            break
        time.sleep(0.1)
    appeared = ""
    if seen_at is None and ask and sys.stdin.isatty():
        appeared = input("   No request arrived. Did a tab or window appear anyway? [y/n] ")
    row = {
        "label": label,
        "opener_returned": outcome,
        "opener_seconds": round(opener_seconds, 1),
        "page_requested_after": None if seen_at is None else round(seen_at, 1),
        "tab_appeared_without_request": appeared.strip().lower() or "not asked",
    }
    tab_appeared = row["tab_appeared_without_request"]
    verdict = (
        f"page requested after {seen_at:.1f}s"
        if seen_at is not None
        else f"NO REQUEST within {WAIT_SECONDS:.0f}s (tab appeared: {tab_appeared})"
    )
    print(f"{label}: opener {outcome} in {opener_seconds:.1f}s; {verdict}")
    return row


def xdg_open(url: str) -> str:
    done = subprocess.run(["xdg-open", url], capture_output=True, text=True, timeout=OPEN_TIMEOUT)
    return f"exit {done.returncode}" + (
        f" stderr={done.stderr.strip()!r}" if done.stderr.strip() else ""
    )


def custom(command: str) -> Callable[[str], str]:
    def open_with(url: str) -> str:
        done = subprocess.run(
            [*shlex.split(command), url], capture_output=True, text=True, timeout=OPEN_TIMEOUT
        )
        return f"exit {done.returncode}" + (
            f" stderr={done.stderr.strip()!r}" if done.stderr.strip() else ""
        )

    return open_with


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeat", type=int, default=2, help="rounds per opener (default 2)")
    parser.add_argument("--cmd", action="append", default=[], help="extra opener command line")
    parser.add_argument("--no-ask", action="store_true", help="never prompt on a missing request")
    args = parser.parse_args()

    environment()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    openers: list[tuple[str, Callable[[str], object]]] = [("webbrowser.open", webbrowser.open)]
    if shutil.which("xdg-open"):
        openers.append(("xdg-open", xdg_open))
    openers += [(f"cmd: {command}", custom(command)) for command in args.cmd]

    print(f"== Tests (serving {base}, waiting up to {WAIT_SECONDS:.0f}s each)")
    print("Watch the screen. Close each test tab when it appears.\n")
    rows = []
    for round_number in range(1, args.repeat + 1):
        # One path per round and opener: two --cmd openers must not answer for each other.
        for index, (label, opener) in enumerate(openers):
            rows.append(
                attempt(
                    f"round {round_number} {label}",
                    opener,
                    f"/t{round_number}-{index}",
                    base,
                    not args.no_ask,
                )
            )
    print("\n== Summary")
    for label in dict.fromkeys(str(row["label"]).split(" ", 2)[2] for row in rows):
        mine = [r for r in rows if str(r["label"]).endswith(label)]
        ok = sum(1 for r in mine if r["page_requested_after"] is not None)
        print(f"{label}: {ok} of {len(mine)} pages reached the browser")
    server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
