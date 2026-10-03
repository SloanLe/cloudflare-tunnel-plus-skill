#!/usr/bin/env python3
"""Offline unit tests for tunnel_helper.py.

No cloudflared binary and no network are required: the tests exercise URL
normalization, name sanitization, config rendering, state-file handling,
process liveness, and stale-state cleanup. Run from anywhere:

    python3 test_helper_unit.py
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="cft-plus-unit-"))

# Point the helper's state directory at our temp dir BEFORE importing it.
os.environ["CFT_STATE_DIR"] = str(TMP)
sys.path.insert(0, str(SCRIPT_DIR))

import tunnel_helper as h  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"PASS  {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL  {name}  {detail}")


def ns(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def test_normalize_url() -> None:
    cases = [
        (ns(url="http://localhost:3000"), "http://localhost:3000"),
        (ns(url="localhost:3000"), "http://localhost:3000"),
        (ns(url="https://127.0.0.1:8443/"), "https://127.0.0.1:8443"),
        (ns(url=None, port=5173), "http://localhost:5173"),
        (ns(url="  http://localhost:8080  "), "http://localhost:8080"),
    ]
    for args, expected in cases:
        try:
            check(f"normalize_url -> {expected}", h.normalize_url(args) == expected, h.normalize_url(args))
        except RuntimeError as exc:
            check(f"normalize_url -> {expected}", False, str(exc))
    try:
        h.normalize_url(ns(url=None, port=None))
        check("normalize_url raises without url/port", False, "no exception raised")
    except RuntimeError:
        check("normalize_url raises without url/port", True)


def test_safe_name() -> None:
    check("safe_name keeps simple name", h.safe_name("my-app") == "my-app")
    check("safe_name sanitizes spaces", h.safe_name("My App!") == "My-App")
    check("safe_name trims hyphens", h.safe_name("--x--") == "x")
    check("safe_name empty for junk", h.safe_name("!!!") == "")


def test_url_pattern() -> None:
    m = h.URL_PATTERN.search("INFOTAINMENT https://abc-123.trycloudflare.com started")
    check("URL_PATTERN matches subdomain", bool(m) and m.group(0) == "https://abc-123.trycloudflare.com")
    check("URL_PATTERN rejects wrong domain", h.URL_PATTERN.search("https://abc.example.com") is None)


def test_render_named_config() -> None:
    body = h.render_named_config("my-app", "app.example.com", "http://localhost:3000", None)
    expected = (
        "tunnel: my-app\n\n"
        "ingress:\n"
        "  - hostname: app.example.com\n"
        "    service: http://localhost:3000\n"
        "  - service: http_status:404\n"
    )
    check("render_named_config matches expected yaml", body == expected, repr(body))
    with_creds = h.render_named_config("t", "h.example.com", "http://localhost:1", "/Users/x/.cloudflared/abc.json")
    check("render_named_config includes credentials-file", "credentials-file: /Users/x/.cloudflared/abc.json" in with_creds)


def test_tail_lines() -> None:
    log = TMP / "tail.log"
    log.write_text("\n".join(f"line{i}" for i in range(40)), encoding="utf-8")
    tail = h.tail_lines(log, n=25)
    lines = tail.splitlines()
    check("tail_lines returns last 25 lines", len(lines) == 25 and lines[0] == "line15" and lines[-1] == "line39", repr(tail))
    check("tail_lines handles missing file", "unavailable" in h.tail_lines(TMP / "nope.log"))


def test_state_lifecycle() -> None:
    # Spawn a real long-lived child to stand in for cloudflared.
    proc = subprocess.Popen(["sleep", "60"])
    try:
        h.QUICK_PID.write_text(str(proc.pid), encoding="utf-8")
        h.QUICK_LOCAL.write_text("http://localhost:9999", encoding="utf-8")
        h.QUICK_URL.write_text("https://abc.trycloudflare.com", encoding="utf-8")

        state = h.load_quick_state()
        check(
            "load_quick_state sees running tunnel",
            state is not None
            and state["pid"] == proc.pid
            and state["local_url"] == "http://localhost:9999"
            and state["public_url"] == "https://abc.trycloudflare.com",
            repr(state),
        )

        # status must report running and keep the pid file.
        rc = h.cmd_status(ns())
        check("cmd_status returns 0", rc == 0)
        check("cmd_status keeps running pid file", h.QUICK_PID.exists())

        # stop must terminate the child and remove the pid file.
        rc = h.cmd_stop(ns())
        check("cmd_stop returns 0", rc == 0)
        check("cmd_stop removes pid file", not h.QUICK_PID.exists())
        try:
            proc.wait(timeout=8)
            check("cmd_stop terminates process", True)
        except subprocess.TimeoutExpired:
            proc.kill()
            check("cmd_stop terminates process", False, "process still alive")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_stale_state_cleanup() -> None:
    h.QUICK_PID.write_text("999999999", encoding="utf-8")  # pid that cannot exist
    rc = h.cmd_status(ns())
    check("cmd_status cleans stale pid file", rc == 0 and not h.QUICK_PID.exists())

    h.QUICK_PID.write_text("999999999", encoding="utf-8")
    rc = h.cmd_stop(ns())
    check("cmd_stop cleans stale pid file", rc == 0 and not h.QUICK_PID.exists())


def test_cmd_list() -> None:
    h.QUICK_LOCAL.write_text("http://localhost:1111", encoding="utf-8")
    h.QUICK_URL.write_text("https://q.trycloudflare.com", encoding="utf-8")
    h.QUICK_PID.write_text("999999999", encoding="utf-8")  # stale -> listed as not running
    (TMP / "demo.pid").write_text("999999998", encoding="utf-8")
    (TMP / "demo.hostname.txt").write_text("demo.example.com", encoding="utf-8")

    rc = h.cmd_list(ns())
    check("cmd_list returns 0", rc == 0)


def main() -> int:
    test_normalize_url()
    test_safe_name()
    test_url_pattern()
    test_render_named_config()
    test_tail_lines()
    test_state_lifecycle()
    test_stale_state_cleanup()
    test_cmd_list()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} failure(s): {', '.join(FAILURES)}")
        return 1
    print("All unit tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
