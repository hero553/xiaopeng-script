from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.header import Header
from pathlib import Path

from .config import WechatConfig

LOGGER = logging.getLogger(__name__)
MOBILE_RE = re.compile(r"^\+?\d{6,20}$")


@dataclass(frozen=True)
class NotifyTarget:
    mentioned_list: list[str]
    mentioned_mobile_list: list[str]


class Notifier:
    def send(self, content: str) -> None:
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    def send(self, content: str) -> None:
        LOGGER.warning("通知未配置，控制台输出: %s", content)


class CompositeNotifier(Notifier):
    def __init__(self, notifiers: list[Notifier]) -> None:
        self.notifiers = notifiers

    def send(self, content: str) -> None:
        errors: list[str] = []
        for notifier in self.notifiers:
            try:
                notifier.send(content)
            except RuntimeError as exc:
                errors.append(str(exc))

        if errors:
            raise RuntimeError("；".join(errors))


class WorkWechatWebhookNotifier(Notifier):
    def __init__(self, webhook_url: str, target: NotifyTarget) -> None:
        self.webhook_url = webhook_url
        self.target = target

    def send(self, content: str) -> None:
        text_payload: dict[str, object] = {"content": content}
        if self.target.mentioned_list:
            text_payload["mentioned_list"] = self.target.mentioned_list
        if self.target.mentioned_mobile_list:
            text_payload["mentioned_mobile_list"] = self.target.mentioned_mobile_list

        payload = json.dumps(
            {"msgtype": "text", "text": text_payload},
            ensure_ascii=False,
        ).encode("utf-8")

        request = urllib.request.Request(
            self.webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"企业微信通知发送失败: {exc}") from exc

        if response_payload.get("errcode") != 0:
            raise RuntimeError(f"企业微信通知发送失败: {response_payload}")

        LOGGER.info("微信通知已发送: %s", content)


class NtfyNotifier(Notifier):
    TITLE = "小鹏库存提醒"

    def __init__(
        self,
        server_url: str,
        title: str,
        topic: str,
        token: str = "",
        priority: str = "high",
        tags: str = "car,warning",
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.title = title
        self.topic = topic.strip("/")
        self.token = token
        self.priority = priority
        self.tags = tags

    def send(self, content: str) -> None:
        if not self.topic:
            raise RuntimeError("ntfy_topic 不能为空")

        headers = {
            "Title": Header(self.title, "utf-8").encode(),
            "Priority": self.priority,
            "Tags": self.tags,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        request = urllib.request.Request(
            f"{self.server_url}/{self.topic}",
            data=content.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                if response.status >= 400:
                    raise RuntimeError(f"ntfy 通知发送失败，HTTP {response.status}")
        except (OSError, urllib.error.URLError) as exc:
            raise RuntimeError(f"ntfy 通知发送失败: {exc}") from exc

        LOGGER.info("ntfy 通知已发送: %s", content)


class BarkNotifier(Notifier):
    def __init__(
        self,
        server_url: str,
        title: str,
        device_keys: list[str],
        group: str,
        level: str,
        sound: str,
        badge: int,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.title = title
        self.device_keys = device_keys
        self.group = group
        self.level = level
        self.sound = sound
        self.badge = badge

    def send(self, content: str) -> None:
        if not self.device_keys:
            raise RuntimeError("Bark device key 不能为空")

        for device_key in self.device_keys:
            request_url = _build_bark_url(
                server_url=self.server_url,
                device_key=device_key,
                title=self.title,
                body=content,
                group=self.group,
                level=self.level,
                sound=self.sound,
                badge=self.badge,
            )
            response_payload = _fetch_bark_response(request_url)
            code = response_payload.get("code")
            if code not in (200, "200"):
                raise RuntimeError(f"Bark 通知发送失败: {response_payload}")

        LOGGER.info("Bark 通知已发送: %s", content)


class PushPlusNotifier(Notifier):
    API_URL = "https://www.pushplus.plus/send"

    def __init__(self, token: str, topic: str = "") -> None:
        self.token = token
        self.topic = topic

    def send(self, content: str) -> None:
        payload: dict[str, object] = {
            "token": self.token,
            "title": "小鹏库存提醒",
            "content": content,
            "template": "txt",
        }
        if self.topic:
            payload["topic"] = self.topic

        response_payload = _post_json(self.API_URL, payload)
        code = response_payload.get("code")
        if code not in (200, "200"):
            raise RuntimeError(f"PushPlus 通知发送失败: {response_payload}")
        LOGGER.info("个人微信通知已发送: %s", content)


class ServerChanNotifier(Notifier):
    def __init__(self, sendkey: str) -> None:
        self.sendkey = sendkey

    def send(self, content: str) -> None:
        response_payload = _post_json(
            f"https://sctapi.ftqq.com/{self.sendkey}.send",
            {"title": "小鹏库存提醒", "desp": content},
        )
        code = response_payload.get("code")
        if code not in (0, "0"):
            raise RuntimeError(f"Server酱通知发送失败: {response_payload}")
        LOGGER.info("个人微信通知已发送: %s", content)


def create_notifier(config: WechatConfig) -> Notifier:
    if not config.enabled:
        return ConsoleNotifier()

    providers = config.providers or [config.provider.lower().strip()]
    notifiers: list[Notifier] = []

    for provider in providers:
        notifier = _create_provider_notifier(config, provider)
        if notifier is not None:
            notifiers.append(notifier)

    if not notifiers:
        return ConsoleNotifier()
    if len(notifiers) == 1:
        return notifiers[0]
    return CompositeNotifier(notifiers)


def _create_provider_notifier(
    config: WechatConfig,
    provider: str,
) -> Notifier | None:
    normalized_provider = provider.lower().strip()
    if normalized_provider == "bark":
        device_keys = load_bark_device_keys(
            config.bark_device_key,
            config.bark_device_keys,
            config.bark_device_keys_file,
        )
        if not device_keys:
            return None
        return BarkNotifier(
            server_url=config.bark_server_url,
            title=config.bark_title,
            device_keys=device_keys,
            group=config.bark_group,
            level=config.bark_level,
            sound=config.bark_sound,
            badge=config.bark_badge,
        )

    if normalized_provider == "ntfy":
        if not config.ntfy_topic:
            return None
        return NtfyNotifier(
            server_url=config.ntfy_server_url,
            title=config.bark_title,
            topic=config.ntfy_topic,
            token=config.ntfy_token,
            priority=config.ntfy_priority,
            tags=config.ntfy_tags,
        )

    if normalized_provider == "pushplus":
        if not config.pushplus_token:
            return None
        return PushPlusNotifier(
            token=config.pushplus_token,
            topic=config.pushplus_topic,
        )

    if normalized_provider == "serverchan":
        if not config.serverchan_sendkey:
            return None
        return ServerChanNotifier(sendkey=config.serverchan_sendkey)

    if normalized_provider == "work_wechat":
        if not config.work_wechat_webhook_url:
            return None
        return WorkWechatWebhookNotifier(
            webhook_url=config.work_wechat_webhook_url,
            target=load_notify_targets(config.users_file),
        )

    raise RuntimeError(f"不支持的微信通知 provider: {provider}")


def load_bark_device_keys(
    device_key: str,
    device_keys: list[str],
    keys_file: Path | None,
) -> list[str]:
    values: list[str] = []
    if device_key.strip():
        values.append(_normalize_bark_key(device_key))
    for raw_device_key in device_keys:
        if raw_device_key.strip():
            values.append(_normalize_bark_key(raw_device_key))

    if keys_file is not None:
        keys_file.parent.mkdir(parents=True, exist_ok=True)
        if not keys_file.exists():
            keys_file.write_text("", encoding="utf-8")

        for raw_line in keys_file.read_text(encoding="utf-8").splitlines():
            value = raw_line.strip()
            if not value or value.startswith("#"):
                continue
            values.append(_normalize_bark_key(value))

    return _dedupe([value for value in values if value])


def load_notify_targets(users_file: Path) -> NotifyTarget:
    users_file.parent.mkdir(parents=True, exist_ok=True)
    if not users_file.exists():
        users_file.write_text("", encoding="utf-8")

    mentioned_list: list[str] = []
    mentioned_mobile_list: list[str] = []

    for raw_line in users_file.read_text(encoding="utf-8").splitlines():
        value = raw_line.strip()
        if not value or value.startswith("#"):
            continue
        if MOBILE_RE.match(value):
            mentioned_mobile_list.append(value)
        else:
            mentioned_list.append(value)

    return NotifyTarget(
        mentioned_list=_dedupe(mentioned_list),
        mentioned_mobile_list=_dedupe(mentioned_mobile_list),
    )


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _normalize_bark_key(value: str) -> str:
    value = value.strip()
    if "://" not in value:
        return value.strip("/")

    path = urllib.parse.urlparse(value).path.strip("/")
    return path.split("/")[0] if path else ""


def _post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"通知发送失败: {exc}") from exc


def _build_bark_url(
    server_url: str,
    device_key: str,
    title: str,
    body: str,
    group: str,
    level: str,
    sound: str,
    badge: int,
) -> str:
    encoded_device_key = urllib.parse.quote(device_key.strip("/"), safe="")
    encoded_title = urllib.parse.quote(title, safe="")
    encoded_body = urllib.parse.quote(body, safe="")
    query_params = urllib.parse.urlencode(
        {
            "group": group,
            "level": level,
            "sound": sound,
            "badge": str(badge),
        }
    )
    return (
        f"{server_url}/{encoded_device_key}/{encoded_title}/{encoded_body}"
        f"?{query_params}"
    )


def _fetch_bark_response(url: str) -> dict[str, object]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Bark 通知发送失败: {exc}") from exc
