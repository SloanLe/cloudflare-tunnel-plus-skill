#!/usr/bin/env python3
"""Enhanced Cloudflare Tunnel helper for the cloudflare-tunnel-plus skill.

Manages the full lifecycle of Cloudflare Tunnels from the CLI:

  check           verify cloudflared is installed and report its version
  quick           start a temporary trycloudflare.com tunnel in the background
  status          show the managed quick tunnel status
  stop            stop the managed quick tunnel
  verify          verify that a public URL responds as expected
  named-config    write a named-tunnel ingress config file
  named-run       start a named tunnel in the background (PID tracked)
  named-status    show a managed named tunnel status
  named-stop      stop a managed named tunnel
  list            list all managed tunnels (quick + named)

State lives in .cloudflare-tunnel/ inside the current working directory
(override with the CFT_STATE_DIR environment variable). Run status/stop/list
from the same directory where the tunnel was started.

Key features:
  - named-run / named-status / named-stop / list: full background lifecycle
    for named tunnels instead of manual foreground commands
  - localhost -> 127.0.0.1 fallback when the local service binds IPv4 only
  - error messages include the tail of the cloudflared log
  - verify --expect-status for strict status checks
  - CFT_STATE_DIR override, stale-state cleanup, URL normalization
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen


STATE_DIR = Path(os.environ.get("CFT_STATE_DIR", ".cloudflare-tunnel"))
QUICK_PID = STATE_DIR / "quick.pid"
QUICK_URL = STATE_DIR / "quick-url.txt"
QUICK_LOCAL = STATE_DIR / "quick-local-url.txt"
QUICK_TARGET = STATE_DIR / "quick-target-url.txt"
QUICK_LOG = STATE_DIR / "quick.log"
EMPTY_CONFIG = STATE_DIR / "cloudflared-empty.yml"
DEFAULT_TIMEOUT = 120
START_ATTEMPTS = 3
LOG_TAIL_LINES = 25
URL_PATTERN = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
DOH_URL = "https://1.1.1.1/dns-query"
IS_WINDOWS = os.name == "nt"
SCRIPT_PATH = Path(__file__).resolve()

# cloudflared log fragments that indicate a transient edge/API failure worth retrying.
TRANSIENT_MARKERS = (
    "failed to request quick Tunnel",
    "context deadline exceeded",
    "Client.Timeout exceeded",
)


def fail(message: str, code: int = 1) -> int:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    return code


def print_json(data: dict[str, object]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def stop_command_hint() -> str:
    python = Path(sys.executable).name or "python3"
    return f"{python} {SCRIPT_PATH} stop"


def named_stop_command_hint(name: str) -> str:
    python = Path(sys.executable).name or "python3"
    return f"{python} {SCRIPT_PATH} named-stop --name {name}"


def require_cloudflared() -> str:
    path = shutil.which("cloudflared")
    if not path:
        raise RuntimeError(
            "cloudflared not found. Install it first: "
            "macOS `brew install cloudflared`; Windows `winget install --id Cloudflare.cloudflared`; "
            "Linux: use Cloudflare's official packages."
        )
    return path


def normalize_url(args: argparse.Namespace) -> str:
    """Build a local service URL from --url or --port, adding a scheme if missing."""
    raw = args.url if args.url else (f"http://localhost:{args.port}" if args.port else None)
    if not raw:
        raise RuntimeError("provide --url or --port")
    raw = raw.strip()
    if "://" not in raw:
        raw = "http://" + raw
    return raw.rstrip("/")


def run_curl(url: str, timeout: int, extra_args: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["curl", "--noproxy", "*", "-sS", "-L", "--max-time", str(timeout), *extra_args, "-w", "\n%{http_code}", url],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def fetch(url: str, timeout: int = 15) -> tuple[int | None, str]:
    """GET a URL, returning (status, body). Falls back to DNS-over-HTTPS when
    the system resolver cannot resolve a fresh trycloudflare.com hostname."""
    if shutil.which("curl"):
        result = run_curl(url, timeout)
        if result.returncode == 6:
            # DNS resolution failed. Fake-IP proxy resolvers and slow trycloudflare
            # propagation both break the system resolver, so retry via DNS-over-HTTPS.
            doh_result = run_curl(url, timeout, ("--doh-url", DOH_URL))
            if doh_result.returncode == 0:
                result = doh_result
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"curl exited {result.returncode}")
        body, _, status_text = result.stdout.rpartition("\n")
        try:
            status = int(status_text)
        except ValueError:
            status = None
        return status, body

    req = Request(url, headers={"User-Agent": "cloudflare-tunnel-plus/2.1"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except URLError as exc:
        raise RuntimeError(str(exc)) from exc


def verify_local(url: str) -> str:
    """Verify the local service responds, then return the URL to use as the
    tunnel target. Falls back from localhost to 127.0.0.1 because many apps
    bind IPv4 only while localhost may resolve to ::1 first."""
    status, _ = fetch(url, timeout=10)
    if status is None or 200 <= status < 500:
        return url

    parsed = urlparse(url)
    if parsed.hostname == "localhost":
        alt = urlunparse(parsed._replace(netloc=parsed.netloc.replace("localhost", "127.0.0.1", 1)))
        alt_status, _ = fetch(alt, timeout=10)
        if alt_status is not None and 200 <= alt_status < 500:
            return alt
        raise RuntimeError(f"local service returned HTTP {status}; also tried {alt} -> HTTP {alt_status}")
    raise RuntimeError(f"local service returned HTTP {status}")


def is_pid_running(pid: int) -> bool:
    if IS_WINDOWS:
        # os.kill(pid, 0) is not a safe liveness probe on Windows.
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return re.search(rf"\s{pid}\s", result.stdout) is not None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def send_terminate(pid: int, force: bool = False) -> None:
    if IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        # No process group with this pgid (process was not started in its own
        # session, e.g. spawned by a caller or a test); signal the process itself.
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass
    except PermissionError:
        os.kill(pid, sig)


def stop_pid(pid: int) -> None:
    send_terminate(pid)
    deadline = time.time() + 8
    while time.time() < deadline:
        if not is_pid_running(pid):
            return
        time.sleep(0.2)
    if is_pid_running(pid):
        send_terminate(pid, force=True)


def popen_kwargs() -> dict[str, object]:
    if IS_WINDOWS:
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flags}
    return {"start_new_session": True}


def read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def read_pid(path: Path) -> int | None:
    value = read_text(path)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def tail_lines(path: Path, n: int = LOG_TAIL_LINES) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:]) or "(empty log)"
    except OSError:
        return "(log unavailable)"


def launch_and_wait(
    cmd: list[str],
    log_path: Path,
    pid_path: Path,
    ready,
    transient_fn,
    timeout: int,
) -> tuple[str | None, str | None, bool]:
    """Start a background process once. Returns (ready_value, error, transient).

    ready(content) returns a truthy value once the tunnel is usable, or None.
    transient_fn(content) decides whether a failure looks retryable.
    """
    log = log_path.open("w", encoding="utf-8")
    try:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, text=True, **popen_kwargs())
        pid_path.write_text(str(proc.pid), encoding="utf-8")

        deadline = time.time() + timeout
        while time.time() < deadline:
            content = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
            if proc.poll() is not None:
                transient = transient_fn(content)
                return None, (
                    f"cloudflared exited early with code {proc.returncode}. Log tail:\n{tail_lines(log_path)}"
                ), transient
            value = ready(content)
            if value:
                return value, None, False
            time.sleep(0.5)
    finally:
        log.close()

    stop_pid(proc.pid)
    transient = transient_fn(log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else "")
    return None, f"timed out waiting for tunnel readiness. Log tail:\n{tail_lines(log_path)}", transient


def quick_ready(content: str) -> str | None:
    match = URL_PATTERN.search(content)
    if match and "Registered tunnel connection" in content:
        return match.group(0)
    return None


def quick_transient(content: str) -> bool:
    return bool(URL_PATTERN.search(content)) or any(marker in content for marker in TRANSIENT_MARKERS)


def named_ready(content: str) -> str | None:
    if "Registered tunnel connection" in content:
        return "registered"
    return None


def named_transient(_content: str) -> bool:
    # Named tunnel startup failures are usually configuration errors; do not auto-retry.
    return False


def load_quick_state() -> dict[str, object] | None:
    """Return state of a running quick tunnel for this directory, or None."""
    pid = read_pid(QUICK_PID)
    if pid is None or not is_pid_running(pid):
        return None
    return {
        "pid": pid,
        "local_url": read_text(QUICK_LOCAL),
        "public_url": read_text(QUICK_URL),
    }


def cmd_check(_: argparse.Namespace) -> int:
    try:
        path = require_cloudflared()
        result = subprocess.run([path, "--version"], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        print_json({"ok": True, "cloudflared": path, "version": result.stdout.strip()})
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI diagnostics
        return fail(str(exc))


def cmd_quick(args: argparse.Namespace) -> int:
    try:
        cloudflared = require_cloudflared()
        local_url = normalize_url(args)
        target = verify_local(local_url)

        existing = load_quick_state()
        if existing and existing.get("local_url") == local_url and not args.no_reuse:
            print_json(
                {
                    "ok": True,
                    "mode": "quick",
                    "reused": True,
                    "pid": existing["pid"],
                    "local_url": local_url,
                    "public_url": existing.get("public_url"),
                    "log_file": str(QUICK_LOG),
                    "stop_command": stop_command_hint(),
                }
            )
            return 0
        if existing:
            # A quick tunnel for a different local URL is running; replace it.
            stop_pid(int(existing["pid"]))

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        EMPTY_CONFIG.write_text("", encoding="utf-8")

        last_error = "quick tunnel failed"
        for attempt in range(1, START_ATTEMPTS + 1):
            cmd = [
                cloudflared,
                "--config",
                str(EMPTY_CONFIG),
                "tunnel",
                "--no-autoupdate",
                "--protocol",
                args.protocol,
            ]
            if args.no_tls_verify:
                cmd.append("--no-tls-verify")
            cmd.extend(["--url", target])

            public_url, error, transient = launch_and_wait(
                cmd, QUICK_LOG, QUICK_PID, quick_ready, quick_transient, args.timeout
            )
            if public_url:
                QUICK_URL.write_text(public_url + "\n", encoding="utf-8")
                QUICK_LOCAL.write_text(local_url + "\n", encoding="utf-8")
                QUICK_TARGET.write_text(target + "\n", encoding="utf-8")
                print_json(
                    {
                        "ok": True,
                        "mode": "quick",
                        "pid": read_pid(QUICK_PID),
                        "attempt": attempt,
                        "local_url": local_url,
                        "public_url": public_url,
                        "log_file": str(QUICK_LOG),
                        "stop_command": stop_command_hint(),
                    }
                )
                return 0
            last_error = error or last_error
            if not transient or attempt == START_ATTEMPTS:
                break
            time.sleep(2)
        return fail(last_error)
    except Exception as exc:  # noqa: BLE001 - CLI diagnostics
        return fail(str(exc))


def cmd_status(_: argparse.Namespace) -> int:
    pid = read_pid(QUICK_PID)
    running = bool(pid and is_pid_running(pid))
    if pid and not running:
        # Stale pid file from a crashed process: clean it up.
        try:
            QUICK_PID.unlink(missing_ok=True)
        except OSError:
            pass
    print_json(
        {
            "ok": True,
            "mode": "quick",
            "running": running,
            "pid": pid,
            "local_url": read_text(QUICK_LOCAL),
            "public_url": read_text(QUICK_URL),
        }
    )
    return 0


def cmd_stop(_: argparse.Namespace) -> int:
    pid = read_pid(QUICK_PID)
    if not pid:
        print_json({"ok": True, "stopped": False, "message": "no quick tunnel pid file found"})
        return 0
    if not is_pid_running(pid):
        QUICK_PID.unlink(missing_ok=True)
        print_json({"ok": True, "stopped": False, "message": "process is not running", "pid": pid})
        return 0
    stop_pid(pid)
    QUICK_PID.unlink(missing_ok=True)
    print_json({"ok": True, "stopped": True, "pid": pid})
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        status, body = fetch(args.url, timeout=args.timeout)
        contains = args.contains in body if args.contains else None
        if args.expect_status is not None:
            ok = status == args.expect_status
        else:
            ok = status is not None and 200 <= status < 500
        if args.contains:
            ok = ok and bool(contains)
        print_json({"ok": ok, "url": args.url, "status": status, "contains": contains})
        return 0 if ok else 1
    except Exception as exc:  # noqa: BLE001 - CLI diagnostics
        return fail(str(exc))


def safe_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", name).strip("-")


def render_named_config(name: str, hostname: str, service: str, credentials_file: str | None) -> str:
    lines = [f"tunnel: {name}"]
    if credentials_file:
        lines.append(f"credentials-file: {credentials_file}")
    lines.extend(
        [
            "",
            "ingress:",
            f"  - hostname: {hostname}",
            f"    service: {service}",
            "  - service: http_status:404",
            "",
        ]
    )
    return "\n".join(lines)


def write_named_config(name: str, hostname: str, service: str, credentials_file: str | None) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    config_path = STATE_DIR / f"{name}.yml"
    config_path.write_text(render_named_config(name, hostname, service, credentials_file), encoding="utf-8")
    return config_path


def cmd_named_config(args: argparse.Namespace) -> int:
    try:
        local_url = normalize_url(args)
        name = safe_name(args.name)
        if not name:
            raise RuntimeError("invalid tunnel name")
        if not args.hostname or "." not in args.hostname:
            raise RuntimeError("provide a valid --hostname")
        config_path = write_named_config(name, args.hostname, local_url, args.credentials_file)
        print_json(
            {
                "ok": True,
                "mode": "named",
                "config": str(config_path),
                "hostname": args.hostname,
                "local_url": local_url,
                "run_command": f"cloudflared tunnel --config {config_path} run {name}",
                "background_command": (
                    f"{Path(sys.executable).name or 'python3'} {SCRIPT_PATH} named-run --name {name} --url {local_url}"
                ),
            }
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI diagnostics
        return fail(str(exc))


def cmd_named_run(args: argparse.Namespace) -> int:
    try:
        cloudflared = require_cloudflared()
        name = safe_name(args.name)
        if not name:
            raise RuntimeError("invalid tunnel name")
        local_url = normalize_url(args)
        target = verify_local(local_url)

        config_path = STATE_DIR / f"{name}.yml"
        if not config_path.exists():
            if not args.hostname:
                raise RuntimeError(
                    f"no config found for tunnel {name!r}; run `named-config` first or pass --hostname"
                )
            write_named_config(name, args.hostname, target, args.credentials_file)

        pid_path = STATE_DIR / f"{name}.pid"
        existing_pid = read_pid(pid_path)
        if existing_pid and is_pid_running(existing_pid):
            print_json(
                {
                    "ok": True,
                    "mode": "named",
                    "name": name,
                    "reused": True,
                    "pid": existing_pid,
                    "hostname": args.hostname or read_text(STATE_DIR / f"{name}.hostname.txt"),
                    "config": str(config_path),
                    "log_file": str(STATE_DIR / f"{name}.log"),
                    "stop_command": named_stop_command_hint(name),
                }
            )
            return 0

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        cmd = [cloudflared, "tunnel", "--no-autoupdate", "--config", str(config_path), "run", name]
        value, error, _transient = launch_and_wait(
            cmd, STATE_DIR / f"{name}.log", pid_path, named_ready, named_transient, args.timeout
        )
        if value:
            if args.hostname:
                (STATE_DIR / f"{name}.hostname.txt").write_text(args.hostname + "\n", encoding="utf-8")
            print_json(
                {
                    "ok": True,
                    "mode": "named",
                    "name": name,
                    "pid": read_pid(pid_path),
                    "hostname": args.hostname or read_text(STATE_DIR / f"{name}.hostname.txt"),
                    "config": str(config_path),
                    "log_file": str(STATE_DIR / f"{name}.log"),
                    "stop_command": named_stop_command_hint(name),
                }
            )
            return 0
        return fail(error or "named tunnel failed to start")
    except Exception as exc:  # noqa: BLE001 - CLI diagnostics
        return fail(str(exc))


def cmd_named_status(args: argparse.Namespace) -> int:
    name = safe_name(args.name)
    if not name:
        return fail("invalid tunnel name")
    pid_path = STATE_DIR / f"{name}.pid"
    pid = read_pid(pid_path)
    running = bool(pid and is_pid_running(pid))
    if pid and not running:
        pid_path.unlink(missing_ok=True)
    print_json(
        {
            "ok": True,
            "mode": "named",
            "name": name,
            "running": running,
            "pid": pid,
            "hostname": read_text(STATE_DIR / f"{name}.hostname.txt"),
            "config": str(STATE_DIR / f"{name}.yml"),
            "log_file": str(STATE_DIR / f"{name}.log"),
        }
    )
    return 0


def cmd_named_stop(args: argparse.Namespace) -> int:
    name = safe_name(args.name)
    if not name:
        return fail("invalid tunnel name")
    pid_path = STATE_DIR / f"{name}.pid"
    pid = read_pid(pid_path)
    if not pid:
        print_json({"ok": True, "mode": "named", "name": name, "stopped": False, "message": "no pid file found"})
        return 0
    if not is_pid_running(pid):
        pid_path.unlink(missing_ok=True)
        print_json({"ok": True, "mode": "named", "name": name, "stopped": False, "message": "process is not running", "pid": pid})
        return 0
    stop_pid(pid)
    pid_path.unlink(missing_ok=True)
    print_json({"ok": True, "mode": "named", "name": name, "stopped": True, "pid": pid})
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    tunnels: list[dict[str, object]] = []
    if STATE_DIR.exists():
        for pid_file in sorted(STATE_DIR.glob("*.pid")):
            pid = read_pid(pid_file)
            running = bool(pid and is_pid_running(pid))
            if pid_file.name == "quick.pid":
                tunnels.append(
                    {
                        "type": "quick",
                        "running": running,
                        "pid": pid,
                        "local_url": read_text(QUICK_LOCAL),
                        "public_url": read_text(QUICK_URL),
                    }
                )
            else:
                name = pid_file.stem
                tunnels.append(
                    {
                        "type": "named",
                        "name": name,
                        "running": running,
                        "pid": pid,
                        "hostname": read_text(STATE_DIR / f"{name}.hostname.txt"),
                        "log_file": str(STATE_DIR / f"{name}.log"),
                    }
                )
    print_json({"ok": True, "tunnels": tunnels})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cloudflare Tunnel skill helper (cloudflare-tunnel-plus)")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="check cloudflared")
    check.set_defaults(func=cmd_check)

    quick = sub.add_parser("quick", help="start a quick tunnel in the background")
    quick.add_argument("--url", help="local URL, for example http://localhost:3000")
    quick.add_argument("--port", type=int, help="local HTTP port")
    quick.add_argument("--protocol", default="http2", choices=["http2", "quic"], help="cloudflared protocol")
    quick.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    quick.add_argument("--no-tls-verify", action="store_true", help="allow self-signed local HTTPS")
    quick.add_argument("--no-reuse", action="store_true", help="stop any running quick tunnel and start fresh")
    quick.set_defaults(func=cmd_quick)

    status = sub.add_parser("status", help="show quick tunnel status")
    status.set_defaults(func=cmd_status)

    stop = sub.add_parser("stop", help="stop helper-started quick tunnel")
    stop.set_defaults(func=cmd_stop)

    verify = sub.add_parser("verify", help="verify a public URL")
    verify.add_argument("--url", required=True)
    verify.add_argument("--contains", help="optional response substring to require")
    verify.add_argument("--expect-status", type=int, help="optional exact HTTP status to require")
    verify.add_argument("--timeout", type=int, default=20)
    verify.set_defaults(func=cmd_verify)

    named = sub.add_parser("named-config", help="write a named tunnel config")
    named.add_argument("--name", required=True, help="tunnel name")
    named.add_argument("--hostname", required=True, help="public hostname")
    named.add_argument("--url", help="local URL")
    named.add_argument("--port", type=int, help="local HTTP port")
    named.add_argument("--credentials-file", help="optional Cloudflare tunnel credentials JSON")
    named.set_defaults(func=cmd_named_config)

    named_run = sub.add_parser("named-run", help="start a named tunnel in the background")
    named_run.add_argument("--name", required=True, help="tunnel name")
    named_run.add_argument("--url", help="local URL")
    named_run.add_argument("--port", type=int, help="local HTTP port")
    named_run.add_argument("--hostname", help="public hostname (writes config if missing)")
    named_run.add_argument("--credentials-file", help="optional Cloudflare tunnel credentials JSON")
    named_run.add_argument("--timeout", type=int, default=90)
    named_run.set_defaults(func=cmd_named_run)

    named_status = sub.add_parser("named-status", help="show a named tunnel status")
    named_status.add_argument("--name", required=True, help="tunnel name")
    named_status.set_defaults(func=cmd_named_status)

    named_stop = sub.add_parser("named-stop", help="stop a helper-started named tunnel")
    named_stop.add_argument("--name", required=True, help="tunnel name")
    named_stop.set_defaults(func=cmd_named_stop)

    lst = sub.add_parser("list", help="list all managed tunnels")
    lst.set_defaults(func=cmd_list)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
