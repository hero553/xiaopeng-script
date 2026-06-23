from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class NotifyDecision:
    should_notify: bool
    reason: str
    previous_summary: str
    previous_notified_at: float | None


class NotifiedStore:
    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file
        self._lock = threading.Lock()
        self._records = self._load()

    def decide_notification(
        self,
        *,
        series_key: str,
        content_key: str,
        summary_text: str,
        cooldown_seconds: int,
    ) -> NotifyDecision:
        with self._lock:
            record = self._records.get(series_key)
            if not isinstance(record, dict):
                return NotifyDecision(
                    should_notify=True,
                    reason="first_seen",
                    previous_summary="",
                    previous_notified_at=None,
                )

            last_content_key = str(record.get("content_key", "")).strip()
            last_summary = str(record.get("summary_text", "")).strip()
            last_notified_at_raw = record.get("notified_at")
            try:
                last_notified_at = (
                    float(last_notified_at_raw)
                    if last_notified_at_raw is not None
                    else None
                )
            except (TypeError, ValueError):
                last_notified_at = None

            if last_content_key != content_key:
                return NotifyDecision(
                    should_notify=True,
                    reason="content_changed",
                    previous_summary=last_summary,
                    previous_notified_at=last_notified_at,
                )

            if last_notified_at is None:
                return NotifyDecision(
                    should_notify=True,
                    reason="missing_timestamp",
                    previous_summary=last_summary,
                    previous_notified_at=None,
                )

            if (time.time() - last_notified_at) >= cooldown_seconds:
                return NotifyDecision(
                    should_notify=True,
                    reason="cooldown_elapsed",
                    previous_summary=last_summary,
                    previous_notified_at=last_notified_at,
                )

            return NotifyDecision(
                should_notify=False,
                reason="cooldown_active",
                previous_summary=last_summary,
                previous_notified_at=last_notified_at,
            )

    def mark_notified(
        self,
        *,
        series_key: str,
        content_key: str,
        summary_text: str,
    ) -> None:
        with self._lock:
            self._records[series_key] = {
                "content_key": content_key,
                "summary_text": summary_text,
                "notified_at": time.time(),
            }
            self._save()

    def _load(self) -> dict[str, dict[str, object]]:
        if not self.state_file.exists():
            return {}
        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

        if isinstance(payload, dict):
            result: dict[str, dict[str, object]] = {}
            for key, value in payload.items():
                if isinstance(value, dict):
                    result[str(key)] = {
                        "content_key": str(value.get("content_key", "")).strip(),
                        "summary_text": str(value.get("summary_text", "")),
                        "notified_at": value.get("notified_at"),
                    }
                    continue
                try:
                    notified_at = float(value)
                except (TypeError, ValueError):
                    continue
                result[str(key)] = {
                    "content_key": "",
                    "summary_text": "",
                    "notified_at": notified_at,
                }
            return result

        if isinstance(payload, list):
            # 兼容旧版本只保存 key 列表的格式。
            now = time.time()
            return {
                str(item): {
                    "content_key": "",
                    "summary_text": "",
                    "notified_at": now,
                }
                for item in payload
            }

        return {}

    def _save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps(self._records, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
