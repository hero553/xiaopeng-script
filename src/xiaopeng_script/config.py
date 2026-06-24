from __future__ import annotations

import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .constants import DEFAULT_EXCLUDED_SERIES_NAMES, TAB_NAMES, TARGET_URL


@dataclass(frozen=True)
class ChromeConfig:
    remote_debugging_port: int = 9222
    user_data_dir: Path = Path("data/chrome-profile")
    executable_path: str = ""
    incognito: bool = False
    window_width: int = 1440
    window_height: int = 900
    extra_args: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MonitorConfig:
    target_url: str = TARGET_URL
    tabs: list[str] = field(default_factory=lambda: list(TAB_NAMES))
    manual_query_only: bool = True
    parallel_tabs: bool = True
    parallel_series_per_tab: int = 4
    result_preview_limit: int = 3
    poll_interval_seconds: int = 30
    poll_interval_min_seconds: int = 30
    poll_interval_max_seconds: int = 30
    query_timeout_ms: int = 15_000
    notify_once: bool = True
    notify_cooldown_seconds: int = 7_200
    state_file: Path = Path("data/notified.json")
    series_name_exclude: list[str] = field(
        default_factory=lambda: list(DEFAULT_EXCLUDED_SERIES_NAMES)
    )


@dataclass(frozen=True)
class WechatConfig:
    enabled: bool = True
    provider: str = "bark"
    providers: list[str] = field(default_factory=list)
    bark_server_url: str = "https://api.day.app"
    bark_title: str = "小鹏库存提醒"
    bark_device_key: str = ""
    bark_device_keys: list[str] = field(default_factory=list)
    bark_device_keys_file: Path | None = Path("config/bark_keys.txt")
    bark_group: str = "小鹏库存"
    bark_level: str = "timeSensitive"
    bark_sound: str = "alarm"
    bark_badge: int = 1
    ntfy_server_url: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    ntfy_token: str = ""
    ntfy_priority: str = "high"
    ntfy_tags: str = "car,warning"
    pushplus_token: str = ""
    pushplus_topic: str = ""
    serverchan_sendkey: str = ""
    work_wechat_webhook_url: str = ""
    users_file: Path = Path("config/wechat_users.txt")


@dataclass(frozen=True)
class AppConfig:
    chrome: ChromeConfig = field(default_factory=ChromeConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    wechat: WechatConfig = field(default_factory=WechatConfig)


def get_runtime_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


def resolve_default_config_path(raw_config_path: str | None = None) -> Path:
    if raw_config_path:
        return Path(raw_config_path).expanduser()

    if getattr(sys, "frozen", False):
        runtime_base_dir = get_runtime_base_dir()
        standalone_config_path = runtime_base_dir / "config.json"
        legacy_config_path = runtime_base_dir / "config" / "config.json"
        if standalone_config_path.exists() or not legacy_config_path.exists():
            return standalone_config_path
        return legacy_config_path

    local_config_path = Path("config/config.local.json").expanduser()
    if local_config_path.exists():
        return local_config_path
    return Path("config/config.json").expanduser()


def get_config_base_dir(config_path: Path) -> Path:
    return _guess_base_dir(config_path.expanduser().resolve())


def load_app_config(config_path: Path) -> AppConfig:
    config_path = config_path.expanduser().resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    base_dir = _guess_base_dir(config_path)

    chrome_raw = raw.get("chrome", {})
    monitor_raw = raw.get("monitor", {})
    wechat_raw = raw.get("wechat", {})
    poll_interval_seconds = int(
        monitor_raw.get(
            "poll_interval_seconds",
            MonitorConfig.poll_interval_seconds,
        )
    )

    chrome = ChromeConfig(
        remote_debugging_port=int(
            chrome_raw.get("remote_debugging_port", ChromeConfig.remote_debugging_port)
        ),
        user_data_dir=_resolve_path(
            chrome_raw.get("user_data_dir", str(ChromeConfig.user_data_dir)), base_dir
        ),
        executable_path=str(chrome_raw.get("executable_path", "")),
        incognito=bool(chrome_raw.get("incognito", False)),
        window_width=int(chrome_raw.get("window_width", ChromeConfig.window_width)),
        window_height=int(chrome_raw.get("window_height", ChromeConfig.window_height)),
        extra_args=list(chrome_raw.get("extra_args", [])),
    )

    monitor = MonitorConfig(
        target_url=str(monitor_raw.get("target_url", TARGET_URL)),
        tabs=list(monitor_raw.get("tabs", TAB_NAMES)),
        manual_query_only=bool(
            monitor_raw.get(
                "manual_query_only",
                MonitorConfig.manual_query_only,
            )
        ),
        parallel_tabs=bool(
            monitor_raw.get("parallel_tabs", MonitorConfig.parallel_tabs)
        ),
        parallel_series_per_tab=int(
            monitor_raw.get(
                "parallel_series_per_tab",
                MonitorConfig.parallel_series_per_tab,
            )
        ),
        result_preview_limit=int(
            monitor_raw.get(
                "result_preview_limit",
                MonitorConfig.result_preview_limit,
            )
        ),
        poll_interval_seconds=poll_interval_seconds,
        poll_interval_min_seconds=int(
            monitor_raw.get(
                "poll_interval_min_seconds",
                poll_interval_seconds,
            )
        ),
        poll_interval_max_seconds=int(
            monitor_raw.get(
                "poll_interval_max_seconds",
                poll_interval_seconds,
            )
        ),
        query_timeout_ms=int(
            monitor_raw.get("query_timeout_ms", MonitorConfig.query_timeout_ms)
        ),
        notify_once=bool(monitor_raw.get("notify_once", True)),
        notify_cooldown_seconds=int(
            monitor_raw.get(
                "notify_cooldown_seconds",
                MonitorConfig.notify_cooldown_seconds,
            )
        ),
        state_file=_resolve_path(
            monitor_raw.get("state_file", str(MonitorConfig.state_file)), base_dir
        ),
        series_name_exclude=list(
            monitor_raw.get("series_name_exclude", DEFAULT_EXCLUDED_SERIES_NAMES)
        ),
    )

    wechat = WechatConfig(
        enabled=bool(wechat_raw.get("enabled", True)),
        provider=str(_detect_wechat_provider(wechat_raw)),
        providers=_normalize_wechat_providers(
            wechat_raw.get("providers"),
            fallback_provider=str(_detect_wechat_provider(wechat_raw)),
        ),
        bark_server_url=str(
            wechat_raw.get("bark_server_url", WechatConfig.bark_server_url)
        ),
        bark_title=str(wechat_raw.get("bark_title", WechatConfig.bark_title)),
        bark_device_key=str(wechat_raw.get("bark_device_key", "")),
        bark_device_keys=_normalize_string_list(
            wechat_raw.get("bark_device_keys", [])
        ),
        bark_device_keys_file=_resolve_optional_path(
            wechat_raw.get(
                "bark_device_keys_file",
                str(WechatConfig.bark_device_keys_file or ""),
            ),
            base_dir,
        ),
        bark_group=str(wechat_raw.get("bark_group", WechatConfig.bark_group)),
        bark_level=str(wechat_raw.get("bark_level", WechatConfig.bark_level)),
        bark_sound=str(wechat_raw.get("bark_sound", WechatConfig.bark_sound)),
        bark_badge=int(wechat_raw.get("bark_badge", WechatConfig.bark_badge)),
        ntfy_server_url=str(wechat_raw.get("ntfy_server_url", WechatConfig.ntfy_server_url)),
        ntfy_topic=str(wechat_raw.get("ntfy_topic", "")),
        ntfy_token=str(wechat_raw.get("ntfy_token", "")),
        ntfy_priority=str(wechat_raw.get("ntfy_priority", WechatConfig.ntfy_priority)),
        ntfy_tags=str(wechat_raw.get("ntfy_tags", WechatConfig.ntfy_tags)),
        pushplus_token=str(wechat_raw.get("pushplus_token", "")),
        pushplus_topic=str(wechat_raw.get("pushplus_topic", "")),
        serverchan_sendkey=str(wechat_raw.get("serverchan_sendkey", "")),
        work_wechat_webhook_url=str(
            wechat_raw.get(
                "work_wechat_webhook_url",
                wechat_raw.get("webhook_url", ""),
            )
        ),
        users_file=_resolve_path(
            wechat_raw.get("users_file", str(WechatConfig.users_file)), base_dir
        ),
    )

    return AppConfig(chrome=chrome, monitor=monitor, wechat=wechat)


def write_default_config(config_path: Path, overwrite: bool = False) -> None:
    config_path = config_path.expanduser()
    if config_path.exists() and not overwrite:
        return

    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _build_default_config_payload(
        is_standalone_config=config_path.parent.name != "config"
    )
    config_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def ensure_default_files(config_path: Path) -> None:
    write_default_config(config_path=config_path, overwrite=False)
    base_dir = _guess_base_dir(config_path.expanduser().resolve())
    if config_path.parent.name == "config":
        bark_keys_file = _resolve_path("config/bark_keys.txt", base_dir)
        bark_keys_file.parent.mkdir(parents=True, exist_ok=True)
        if not bark_keys_file.exists():
            bark_keys_file.write_text(
                "# Bark 接收设备 key，每行一个。\n"
                "# iPhone 安装 Bark 后，首页会显示类似 https://api.day.app/你的key 的测试地址。\n"
                "# 把最后的 key 填到这里。\n",
                encoding="utf-8",
            )

    users_file = _resolve_path(
        "config/wechat_users.txt" if config_path.parent.name == "config" else "wechat_users.txt",
        base_dir,
    )
    users_file.parent.mkdir(parents=True, exist_ok=True)
    if not users_file.exists():
        users_file.write_text(
            "# 默认 providers=[\"bark\",\"ntfy\"] 时不需要这个文件。\n"
            "# 只有 providers 包含 work_wechat 企业微信机器人模式下才需要：\n"
            "# 每行一个企业微信 user_id、手机号，或 @all。\n",
            encoding="utf-8",
        )


def _resolve_path(raw_path: str | Path, base_dir: Path) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _resolve_optional_path(raw_path: str | Path | None, base_dir: Path) -> Path | None:
    if raw_path is None:
        return None

    normalized_raw_path = str(raw_path).strip()
    if not normalized_raw_path:
        return None
    return _resolve_path(normalized_raw_path, base_dir)


def get_poll_interval_range(config: MonitorConfig) -> tuple[int, int]:
    min_seconds = max(1, int(config.poll_interval_min_seconds))
    max_seconds = max(1, int(config.poll_interval_max_seconds))
    return min(min_seconds, max_seconds), max(min_seconds, max_seconds)


def pick_poll_interval_seconds(config: MonitorConfig) -> int:
    min_seconds, max_seconds = get_poll_interval_range(config)
    return random.randint(min_seconds, max_seconds)


def _guess_base_dir(config_path: Path) -> Path:
    if config_path.parent.name == "config":
        return config_path.parent.parent
    return config_path.parent


def _detect_wechat_provider(wechat_raw: dict[str, Any]) -> str:
    provider = str(wechat_raw.get("provider", "")).strip()
    if provider:
        return provider
    providers = _normalize_wechat_providers(wechat_raw.get("providers"), "")
    if providers:
        return providers[0]
    if wechat_raw.get("bark_device_key") or wechat_raw.get("bark_device_keys_file"):
        return "bark"
    if wechat_raw.get("ntfy_topic"):
        return "ntfy"
    if wechat_raw.get("webhook_url"):
        return "work_wechat"
    return "bark"


def _normalize_wechat_providers(
    raw_providers: Any,
    fallback_provider: str,
) -> list[str]:
    normalized_values = [
        value.lower() for value in _normalize_string_list(raw_providers)
    ]

    filtered_values = [value for value in normalized_values if value]
    if filtered_values:
        return list(dict.fromkeys(filtered_values))

    fallback_value = fallback_provider.strip().lower()
    if fallback_value:
        return [fallback_value]
    return []


def _normalize_string_list(raw_values: Any) -> list[str]:
    if isinstance(raw_values, list):
        return [str(value).strip() for value in raw_values if str(value).strip()]
    if isinstance(raw_values, str):
        normalized_value = raw_values.strip()
        return [normalized_value] if normalized_value else []
    return []


def _build_default_config_payload(is_standalone_config: bool) -> dict[str, Any]:
    if is_standalone_config:
        return {
            "monitor": {
                "parallel_tabs": False,
                "parallel_series_per_tab": 4,
                "poll_interval_seconds": 30,
                "poll_interval_min_seconds": 30,
                "poll_interval_max_seconds": 30,
                "notify_cooldown_seconds": 7200,
            },
            "wechat": {
                "enabled": True,
                "providers": ["bark", "ntfy"],
                "bark_title": "小鹏库存提醒",
                "bark_device_keys": [],
                "bark_device_keys_file": "",
                "bark_level": "timeSensitive",
                "ntfy_topic": "",
                "ntfy_priority": "high",
            },
        }

    return {
        "chrome": {
            "remote_debugging_port": 9222,
            "user_data_dir": "data/chrome-profile",
            "executable_path": "",
            "incognito": False,
            "window_width": 1440,
            "window_height": 900,
            "extra_args": [],
        },
        "monitor": {
            "target_url": TARGET_URL,
            "tabs": TAB_NAMES,
            "manual_query_only": True,
            "parallel_tabs": False,
            "parallel_series_per_tab": 4,
            "result_preview_limit": 3,
            "poll_interval_seconds": 30,
            "poll_interval_min_seconds": 30,
            "poll_interval_max_seconds": 30,
            "query_timeout_ms": 15000,
            "notify_once": True,
            "notify_cooldown_seconds": 7200,
            "state_file": "data/notified.json",
            "series_name_exclude": DEFAULT_EXCLUDED_SERIES_NAMES,
        },
        "wechat": {
            "enabled": True,
            "provider": "bark",
            "providers": ["bark", "ntfy"],
            "bark_server_url": "https://api.day.app",
            "bark_title": "小鹏库存提醒",
            "bark_device_key": "",
            "bark_device_keys": [],
            "bark_device_keys_file": "config/bark_keys.txt",
            "bark_group": "小鹏库存",
            "bark_level": "timeSensitive",
            "bark_sound": "alarm",
            "bark_badge": 1,
            "ntfy_server_url": "https://ntfy.sh",
            "ntfy_topic": "",
            "ntfy_token": "",
            "ntfy_priority": "high",
            "ntfy_tags": "car,warning",
            "pushplus_token": "",
            "pushplus_topic": "",
            "serverchan_sendkey": "",
            "work_wechat_webhook_url": "",
            "users_file": "config/wechat_users.txt",
        },
    }
