from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import ChromeConfig

LOGGER = logging.getLogger(__name__)


class ChromeLaunchError(RuntimeError):
    """Raised when Chrome cannot be found or started."""


def ensure_debug_chrome(config: ChromeConfig, startup_url: str) -> subprocess.Popen | None:
    if is_debug_port_open(config.remote_debugging_port):
        LOGGER.info("检测到 Chrome 调试端口已开启: %s", config.remote_debugging_port)
        return None

    process = launch_chrome(config=config, startup_url=startup_url)
    wait_for_debug_port(config.remote_debugging_port)
    return process


def launch_chrome(config: ChromeConfig, startup_url: str) -> subprocess.Popen:
    executable = find_chrome_executable(config.executable_path)
    config.user_data_dir.mkdir(parents=True, exist_ok=True)

    args = [
        executable,
        f"--remote-debugging-port={config.remote_debugging_port}",
        f"--user-data-dir={config.user_data_dir}",
        f"--window-size={config.window_width},{config.window_height}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if config.incognito:
        args.append("--incognito")
    args.extend(config.extra_args)
    args.append(startup_url)

    LOGGER.info("启动 Chrome: %s", executable)
    try:
        return subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise ChromeLaunchError(f"Chrome 启动失败: {exc}") from exc


def wait_for_debug_port(port: int, timeout_seconds: int = 15) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if is_debug_port_open(port):
            return
        time.sleep(0.3)
    raise ChromeLaunchError(f"等待 Chrome 调试端口超时: {port}")


def is_debug_port_open(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=0.7
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return bool(payload.get("webSocketDebuggerUrl"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return False


def find_chrome_executable(override: str = "") -> str:
    if override:
        path = Path(override).expanduser()
        if path.exists():
            return str(path)
        raise ChromeLaunchError(f"配置的 Chrome 路径不存在: {path}")

    candidates = _chrome_candidates()
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    raise ChromeLaunchError(
        "没有找到 Google Chrome。请在 config/config.json 里配置 chrome.executable_path。"
    )


def _chrome_candidates() -> list[Path]:
    system_name = platform.system().lower()
    home = Path.home()

    if system_name == "darwin":
        return [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            home / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            Path("/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary"),
        ]

    if system_name == "windows":
        env_paths = [
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        ]
        roots = [Path(value) for value in env_paths if value]
        return [
            root / "Google/Chrome/Application/chrome.exe"
            for root in roots
        ] + [
            root / "Google/Chrome SxS/Application/chrome.exe"
            for root in roots
        ]

    return [
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/google-chrome-stable"),
        Path("/usr/bin/chromium"),
        Path("/usr/bin/chromium-browser"),
    ]

