from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import BRAVE_BIN, CHROME_BIN, CHROMIUM_BIN, DEFAULT_CDP_PORT, PROFILE_DIR, ensure_dirs


class BrowserError(RuntimeError):
    pass


def detect_browser() -> Path:
    for binary in (BRAVE_BIN, CHROME_BIN, CHROMIUM_BIN):
        if binary.exists():
            return binary
    raise BrowserError(
        "No supported browser found. Install Brave or Google Chrome, then retry."
    )


def port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _profile_pids(profile_dir: Path) -> list[int]:
    marker = f"--user-data-dir={profile_dir}"
    found = subprocess.run(["pgrep", "-f", "--", marker], capture_output=True, text=True)
    pids: list[int] = []
    for token in found.stdout.split():
        try:
            pid = int(token)
        except ValueError:
            continue
        cmd = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
        ).stdout
        if marker in cmd.split():
            pids.append(pid)
    return pids


def wait_for_cdp(port: int, timeout: float = 45) -> dict:
    endpoint = f"http://127.0.0.1:{port}/json/version"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(endpoint, timeout=2) as response:
                return json.loads(response.read().decode())
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
            time.sleep(0.2)
    raise BrowserError(f"CDP on port {port} never became ready")


def launch_or_attach(
    *,
    port: int = DEFAULT_CDP_PORT,
    profile_dir: Path = PROFILE_DIR,
    binary: Path | None = None,
) -> tuple[str, subprocess.Popen | None]:
    ensure_dirs()
    binary = binary or detect_browser()
    existing = _profile_pids(profile_dir)
    if existing:
        if not port_busy(port):
            raise BrowserError(
                f"Browser already using {profile_dir} (pids={existing}) but port {port} is silent"
            )
        wait_for_cdp(port)
        return f"http://127.0.0.1:{port}", None

    if port_busy(port):
        raise BrowserError(f"port {port} is already in use — pass --cdp-port")

    proc = subprocess.Popen(
        [
            str(binary),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-crash-restore-bubble",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        wait_for_cdp(port)
    except BrowserError:
        proc.kill()
        raise
    return f"http://127.0.0.1:{port}", proc


def shutdown(proc: subprocess.Popen | None, profile_dir: Path = PROFILE_DIR) -> None:
    deadline = time.time() + 3
    while _profile_pids(profile_dir) and time.time() < deadline:
        time.sleep(0.2)

    for sig in (signal.SIGTERM, signal.SIGKILL):
        pids = _profile_pids(profile_dir)
        if not pids:
            break
        for pid in pids:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        time.sleep(2.5 if sig is signal.SIGTERM else 1.0)

    if proc is not None:
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
