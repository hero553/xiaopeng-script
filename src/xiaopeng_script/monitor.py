from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime

from playwright.sync_api import Browser, Page, TimeoutError as PlaywrightTimeoutError

from .config import MonitorConfig
from .constants import (
    CURRENT_FILTER_SERIES_CODE,
    CURRENT_FILTER_SERIES_NAME,
    NO_DATA_TEXTS,
)
from .notifier import Notifier
from .state import NotifiedStore

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Hit:
    tab_name: str
    series_name: str
    series_code: str
    summary_lines: list[str]
    detected_at: datetime
    notify_reason: str = "first_seen"

    @property
    def content_key(self) -> str:
        summary_text = "\n".join(self.summary_lines)
        digest = hashlib.sha1(summary_text.encode("utf-8")).hexdigest()[:16]
        return f"{self.tab_name}::{self.series_code}::{digest}"

    @property
    def series_key(self) -> str:
        return f"{self.tab_name}::{self.series_code}"

    @property
    def message(self) -> str:
        summary = "\n".join(self.summary_lines)
        if self.series_code == CURRENT_FILTER_SERIES_CODE or not self.series_name:
            target_name = self.tab_name
        else:
            target_name = f"{self.tab_name} - {self.series_name}"
        return f"{_format_notify_reason_title(self.notify_reason)} {target_name}\n{summary}"


class InventoryMonitor:
    def __init__(
        self,
        page: Page,
        config: MonitorConfig,
        notifier: Notifier,
        notified_store: NotifiedStore,
    ) -> None:
        self.page = page
        self.config = config
        self.notifier = notifier
        self.notified_store = notified_store

    def run_forever(self) -> None:
        LOGGER.info("开始巡检，间隔 %s 秒", self.config.poll_interval_seconds)
        while True:
            self.run_once()
            time.sleep(self.config.poll_interval_seconds)

    def run_once(self) -> list[Hit]:
        if self.config.manual_query_only:
            return self._run_manual_query_once()
        return self._run_series_query_once()

    def _run_manual_query_once(self) -> list[Hit]:
        hits: list[Hit] = []
        self._ensure_page_ready()

        for tab_name in self.config.tabs:
            LOGGER.info("切换栏目: %s", tab_name)
            if not self._click_tab(tab_name):
                LOGGER.warning("没有找到栏目: %s", tab_name)
                continue

            hit = self._query_current_filters(tab_name)
            if hit:
                hits.append(hit)

        return hits

    def _run_series_query_once(self) -> list[Hit]:
        hits: list[Hit] = []
        self._ensure_page_ready()

        for tab_name in self.config.tabs:
            LOGGER.info("切换栏目: %s", tab_name)
            if not self._click_tab(tab_name):
                LOGGER.warning("没有找到栏目: %s", tab_name)
                continue

            series_names = self._collect_series_names()
            if not series_names:
                LOGGER.warning("栏目 %s 没有读取到车系列表", tab_name)
                continue

            LOGGER.info("栏目 %s 读取到 %s 个车系", tab_name, len(series_names))
            for series_name in series_names:
                hit = self._query_series(tab_name, series_name)
                if hit:
                    hits.append(hit)

        return hits

    def query_current_tab(self, tab_name: str) -> Hit | None:
        self._ensure_page_ready()
        LOGGER.info("切换栏目: %s", tab_name)
        if not self._click_tab(tab_name):
            LOGGER.warning("没有找到栏目: %s", tab_name)
            return None
        return self._query_current_filters(tab_name)

    def list_series_by_tab(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        self._ensure_page_ready()

        for tab_name in self.config.tabs:
            LOGGER.info("读取栏目车系: %s", tab_name)
            if not self._click_tab(tab_name):
                LOGGER.warning("没有找到栏目: %s", tab_name)
                result[tab_name] = []
                continue
            result[tab_name] = self._collect_series_names()

        return result

    def list_series_for_tab(self, tab_name: str) -> list[str]:
        self._ensure_page_ready()
        LOGGER.info("读取栏目车系: %s", tab_name)
        if not self._click_tab(tab_name):
            LOGGER.warning("没有找到栏目: %s", tab_name)
            return []
        return self._collect_series_names()

    def list_series_options_for_tab(self, tab_name: str) -> list[dict[str, str]]:
        self._ensure_page_ready()
        LOGGER.info("读取栏目车系选项: %s", tab_name)
        if not self._click_tab(tab_name):
            LOGGER.warning("没有找到栏目: %s", tab_name)
            return []
        series_options = self._collect_runtime_series_options()
        if series_options:
            return series_options
        return self._collect_series_options()

    def get_selected_tab_token(self, tab_name: str) -> str:
        self._ensure_page_ready()
        if not self._click_tab(tab_name):
            LOGGER.warning("没有找到栏目: %s", tab_name)
            return ""
        token = self.page.evaluate(GET_SELECTED_TAB_TOKEN_SCRIPT)
        if not isinstance(token, str):
            return ""
        return token.strip()

    def get_runtime_query_context(self, tab_name: str) -> dict[str, object]:
        self._ensure_page_ready()
        if not self._click_tab(tab_name):
            LOGGER.warning("没有找到栏目: %s", tab_name)
            return {"series_options": [], "tab_token": ""}

        runtime_context = self.page.evaluate(GET_RUNTIME_QUERY_CONTEXT_SCRIPT)
        if not isinstance(runtime_context, dict):
            return {"series_options": [], "tab_token": ""}
        series_options = runtime_context.get("series_options", [])
        normalized_series_options: list[dict[str, str]] = []
        if isinstance(series_options, list):
            for item in series_options:
                if not isinstance(item, dict):
                    continue
                series_name = str(item.get("name", "")).strip()
                series_code = str(item.get("code", "")).strip()
                if not series_name or not series_code:
                    continue
                if series_name in self.config.series_name_exclude:
                    continue
                normalized_series_options.append(
                    {"name": series_name, "code": series_code}
                )
        return {
            "series_options": normalized_series_options,
            "tab_token": str(runtime_context.get("tab_token", "")).strip(),
        }

    def diagnose_page(self) -> dict[str, object]:
        self._ensure_page_ready()
        visible_tabs = {
            tab_name: self._is_text_visible(tab_name) for tab_name in self.config.tabs
        }
        query_button_count = self.page.locator("button:has-text('查询')").count()
        series_by_tab = self.list_series_by_tab()
        has_result_data = self._has_result_data()

        return {
            "url": self.page.url,
            "title": self.page.title(),
            "visible_tabs": visible_tabs,
            "query_button_count": query_button_count,
            "series_by_tab": series_by_tab,
            "has_result_data": has_result_data,
        }

    def _query_series(self, tab_name: str, series_name: str) -> Hit | None:
        LOGGER.info("查询: %s / %s", tab_name, series_name)
        if not self._select_series(series_name):
            LOGGER.warning("车系选择失败: %s / %s", tab_name, series_name)
            return None

        selected_series_name = self._get_selected_series_name()
        if selected_series_name != series_name:
            LOGGER.warning(
                "车系回填校验失败: 期望=%s 实际=%s",
                series_name,
                selected_series_name or "空",
            )
            return None

        LOGGER.info("点击查询按钮: %s / %s", tab_name, series_name)
        if not self._click_query():
            LOGGER.warning("查询按钮点击失败: %s / %s", tab_name, series_name)
            return None

        result_rows = self._extract_result_rows()
        LOGGER.info(
            "查询结果行数: %s / %s -> %s",
            tab_name,
            series_name,
            len(result_rows),
        )
        if not result_rows:
            return None

        return self._notify_result_rows(
            tab_name=tab_name,
            series_name=series_name,
            series_code=_guess_series_code(series_name),
            result_rows=result_rows,
        )

    def _query_current_filters(self, tab_name: str) -> Hit | None:
        LOGGER.info("查询: %s / %s", tab_name, CURRENT_FILTER_SERIES_NAME)
        LOGGER.info("点击查询按钮: %s / %s", tab_name, CURRENT_FILTER_SERIES_NAME)
        if not self._click_query():
            LOGGER.warning(
                "查询按钮点击失败: %s / %s",
                tab_name,
                CURRENT_FILTER_SERIES_NAME,
            )
            return None

        result_rows = self._extract_result_rows()
        LOGGER.info(
            "查询结果行数: %s / %s -> %s",
            tab_name,
            CURRENT_FILTER_SERIES_NAME,
            len(result_rows),
        )
        if not result_rows:
            return None

        return self._notify_result_rows(
            tab_name=tab_name,
            series_name=CURRENT_FILTER_SERIES_NAME,
            series_code=CURRENT_FILTER_SERIES_CODE,
            result_rows=result_rows,
        )

    def _notify_result_rows(
        self,
        *,
        tab_name: str,
        series_name: str,
        series_code: str,
        result_rows: list[dict[str, str]],
    ) -> Hit:
        hit = Hit(
            tab_name=tab_name,
            series_name=series_name,
            series_code=series_code,
            summary_lines=_format_result_summary(
                result_rows=result_rows,
                preview_limit=self.config.result_preview_limit,
            ),
            detected_at=datetime.now(),
        )
        LOGGER.info("命中数据: %s", hit.message)

        decision = self.notified_store.decide_notification(
            series_key=hit.series_key,
            content_key=hit.content_key,
            summary_text="\n".join(hit.summary_lines),
            cooldown_seconds=self.config.notify_cooldown_seconds,
        )
        hit = Hit(
            tab_name=hit.tab_name,
            series_name=hit.series_name,
            series_code=hit.series_code,
            summary_lines=hit.summary_lines,
            detected_at=hit.detected_at,
            notify_reason=decision.reason,
        )
        if self.config.notify_once and not decision.should_notify:
            LOGGER.info("冷却时间内已通知过，跳过重复通知: %s", hit.content_key)
            return hit

        self.notifier.send(hit.message)
        self.notified_store.mark_notified(
            series_key=hit.series_key,
            content_key=hit.content_key,
            summary_text="\n".join(hit.summary_lines),
        )
        return hit

    def _ensure_page_ready(self) -> None:
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except PlaywrightTimeoutError:
            LOGGER.debug("等待 domcontentloaded 超时，继续尝试操作页面")
        self.page.wait_for_timeout(500)

    def _click_tab(self, tab_name: str) -> bool:
        locators = [
            self.page.get_by_text(tab_name, exact=True).first,
            self.page.locator(f"button:has-text('{tab_name}')").first,
            self.page.locator(f"[role='tab']:has-text('{tab_name}')").first,
        ]
        for locator in locators:
            try:
                locator.click(timeout=3_000)
                self.page.wait_for_timeout(600)
                return True
            except PlaywrightTimeoutError:
                continue
        return False

    def _collect_series_names(self) -> list[str]:
        options = self._collect_series_options()
        return [option["name"] for option in options]

    def _collect_series_options(self) -> list[dict[str, str]]:
        if not self._open_series_dropdown():
            return []

        names: list[str] = []
        for _ in range(20):
            names = self.page.evaluate(COLLECT_VISIBLE_OPTIONS_SCRIPT)
            names = [
                name
                for name in names
                if name and name not in self.config.series_name_exclude
            ]
            if names:
                break
            self.page.wait_for_timeout(200)

        self._press_escape()
        return [
            {"name": name, "code": _guess_series_code(name)}
            for name in list(dict.fromkeys(names))
        ]

    def _collect_runtime_series_options(self) -> list[dict[str, str]]:
        series_options = self.page.evaluate(GET_RUNTIME_SERIES_OPTIONS_SCRIPT)
        if not isinstance(series_options, list):
            return []

        normalized_options: list[dict[str, str]] = []
        seen_pairs: set[tuple[str, str]] = set()
        for item in series_options:
            if not isinstance(item, dict):
                continue
            series_name = str(item.get("name", "")).strip()
            series_code = str(item.get("code", "")).strip()
            if not series_name or not series_code:
                continue
            if series_name in self.config.series_name_exclude:
                continue
            pair = (series_name, series_code)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            normalized_options.append({"name": series_name, "code": series_code})
        return normalized_options

    def _select_series(self, series_name: str) -> bool:
        if not self._open_series_dropdown():
            return False

        clicked = False
        for _ in range(20):
            clicked = bool(self.page.evaluate(CLICK_VISIBLE_OPTION_SCRIPT, series_name))
            if clicked:
                break
            self.page.wait_for_timeout(200)

        self.page.wait_for_timeout(500)
        self._press_escape()
        return clicked and self._get_selected_series_name() == series_name

    def _open_series_dropdown(self) -> bool:
        self._press_escape()
        locators = [
            self.page.locator(
                "xpath=//*[normalize-space(text())='车系']"
                "/following::input[@placeholder='请选择车系'][1]"
            ),
            self.page.locator(
                "xpath=//*[normalize-space(text())='车系']"
                "/following::*[contains(@class, 'el-select')][1]"
            ),
            self.page.locator(
                "xpath=//*[normalize-space(text())='车系']/following::input[1]"
            ),
        ]

        for locator in locators:
            try:
                locator.click(timeout=2_000)
                self.page.wait_for_timeout(400)
                return True
            except PlaywrightTimeoutError:
                continue
        return False

    def _click_query(self) -> bool:
        self._press_escape()
        locators = [
            self.page.locator("xpath=//button[normalize-space()='查询']").first,
            self.page.get_by_role("button", name="查询").first,
            self.page.locator("button:has-text('查询')").first,
        ]
        for locator in locators:
            try:
                locator.click(timeout=3_000, force=True)
                LOGGER.debug("查询按钮点击成功")
                self._wait_after_query()
                return True
            except PlaywrightTimeoutError:
                continue
        return False

    def _wait_after_query(self) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=self.config.query_timeout_ms)
        except PlaywrightTimeoutError:
            LOGGER.debug("等待 networkidle 超时，使用当前页面状态判断结果")
        self.page.wait_for_timeout(700)

    def _has_result_data(self) -> bool:
        return bool(self.page.evaluate(HAS_RESULT_DATA_SCRIPT, NO_DATA_TEXTS))

    def _extract_result_rows(self) -> list[dict[str, str]]:
        result_rows = self.page.evaluate(EXTRACT_RESULT_ROWS_SCRIPT, NO_DATA_TEXTS)
        if not isinstance(result_rows, list):
            return []
        normalized_rows: list[dict[str, str]] = []
        for row in result_rows:
            if not isinstance(row, dict):
                continue
            normalized_row = {
                str(key): str(value)
                for key, value in row.items()
                if str(key).strip() and str(value).strip()
            }
            if normalized_row:
                normalized_rows.append(normalized_row)
        return normalized_rows

    def _get_selected_series_name(self) -> str:
        series_name = self.page.evaluate(GET_SELECTED_SERIES_NAME_SCRIPT)
        if not isinstance(series_name, str):
            return ""
        return series_name.strip()

    def _press_escape(self) -> None:
        try:
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(100)
        except PlaywrightTimeoutError:
            pass

    def _is_text_visible(self, text: str) -> bool:
        try:
            return self.page.get_by_text(text, exact=True).first.is_visible(timeout=1_000)
        except PlaywrightTimeoutError:
            return False


def get_or_open_target_page(browser: Browser, target_url: str) -> Page:
    for context in browser.contexts:
        for page in context.pages:
            if target_url in page.url or "/vehicle/actuals/lock" in page.url:
                LOGGER.info("复用已打开页面: %s", page.url)
                page.bring_to_front()
                return page

    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.new_page()
    LOGGER.info("打开后台页面: %s", target_url)
    page.goto(target_url, wait_until="domcontentloaded", timeout=30_000)
    return page


def open_target_page(browser: Browser, target_url: str) -> Page:
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.new_page()
    LOGGER.info("打开独立后台页面: %s", target_url)
    page.goto(target_url, wait_until="domcontentloaded", timeout=30_000)
    return page


def _format_result_summary(
    result_rows: list[dict[str, str]],
    preview_limit: int,
) -> list[str]:
    preview_rows = result_rows[:preview_limit]
    summary_lines = [f"共 {len(result_rows)} 条车辆数据"]
    for index, row in enumerate(preview_rows, start=1):
        detail_parts = [
            _pick_result_value(row, ["车型配置名称"]),
            _pick_result_value(row, ["仓库"]),
            _pick_result_value(row, ["vin码", "锁定车辆Vin码"]),
            _pick_result_value(row, ["剩余未锁定车"]),
        ]
        filtered_parts = [part for part in detail_parts if part]
        if filtered_parts:
            summary_lines.append(f"{index}. " + " | ".join(filtered_parts))
    if len(result_rows) > preview_limit:
        summary_lines.append(f"其余 {len(result_rows) - preview_limit} 条请到后台查看")
    return summary_lines


def _pick_result_value(
    row: dict[str, str],
    keys: list[str],
) -> str:
    for key in keys:
        value = row.get(key, "").strip()
        if value:
            return f"{key}: {value}"
    return ""


def _format_notify_reason_title(reason: str) -> str:
    if reason == "content_changed":
        return "库存变更"
    if reason == "cooldown_elapsed":
        return "库存再次提醒"
    return "抢到了"


def _guess_series_code(series_name: str) -> str:
    if series_name == "新P7":
        return "P7N"
    if series_name.startswith("小鹏"):
        return series_name.removeprefix("小鹏")
    return series_name


COLLECT_VISIBLE_OPTIONS_SCRIPT = """
() => {
  const optionSelectors = [
    '[role="listbox"] [role="option"]',
    '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option',
    '.el-select-dropdown .el-select-dropdown__item',
    '.arco-select-popup .arco-select-option',
    '.t-select__dropdown .t-select-option',
    '.n-select-menu .n-base-select-option',
    '.semi-select-popover .semi-select-option',
    '.vxe-select--panel .vxe-select-option'
  ];
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden'
      && style.display !== 'none'
      && rect.width > 0
      && rect.height > 0;
  };
  const normalize = (text) => text.replace(/\\s+/g, ' ').trim();
  const names = [];
  for (const selector of optionSelectors) {
    for (const el of document.querySelectorAll(selector)) {
      if (!isVisible(el)) continue;
      if (el.getAttribute('aria-disabled') === 'true') continue;
      if (el.className && String(el.className).includes('disabled')) continue;
      const text = normalize(el.innerText || el.textContent || '');
      if (text) names.push(text.split('\\n')[0].trim());
    }
  }
  return [...new Set(names)];
}
"""


CLICK_VISIBLE_OPTION_SCRIPT = """
(seriesName) => {
  const optionSelectors = [
    '[role="listbox"] [role="option"]',
    '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option',
    '.el-select-dropdown .el-select-dropdown__item',
    '.arco-select-popup .arco-select-option',
    '.t-select__dropdown .t-select-option',
    '.n-select-menu .n-base-select-option',
    '.semi-select-popover .semi-select-option',
    '.vxe-select--panel .vxe-select-option'
  ];
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden'
      && style.display !== 'none'
      && rect.width > 0
      && rect.height > 0;
  };
  const normalize = (text) => text.replace(/\\s+/g, ' ').trim();
  const candidates = [];
  for (const selector of optionSelectors) {
    for (const el of document.querySelectorAll(selector)) {
      if (!isVisible(el)) continue;
      if (el.getAttribute('aria-disabled') === 'true') continue;
      if (el.className && String(el.className).includes('disabled')) continue;
      candidates.push(el);
    }
  }
  const target = candidates.find((el) => normalize(el.innerText || el.textContent || '') === seriesName)
    || candidates.find((el) => normalize(el.innerText || el.textContent || '').split('\\n')[0].trim() === seriesName);
  if (!target) return false;
  target.click();
  return true;
}
"""


HAS_RESULT_DATA_SCRIPT = """
(noDataTexts) => {
  const excludedContainers = [
    '[role="listbox"]',
    '.ant-select-dropdown',
    '.el-select-dropdown',
    '.arco-select-popup',
    '.t-select__dropdown',
    '.n-select-menu',
    '.semi-select-popover',
    '.vxe-select--panel'
  ].join(',');
  const rowSelectors = [
    '.ant-table-tbody tr',
    '.el-table__body-wrapper tbody tr',
    '.vxe-table--body-wrapper .vxe-body--row',
    'tbody tr',
    '[role="row"]'
  ];
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden'
      && style.display !== 'none'
      && rect.width > 0
      && rect.height > 0;
  };
  const normalize = (text) => text.replace(/\\s+/g, ' ').trim();
  const rows = [];
  for (const selector of rowSelectors) {
    for (const row of document.querySelectorAll(selector)) {
      if (row.closest(excludedContainers)) continue;
      if (!isVisible(row)) continue;
      rows.push(row);
    }
  }
  for (const row of rows) {
    const text = normalize(row.innerText || row.textContent || '');
    if (!text) continue;
    if (noDataTexts.some((emptyText) => text.includes(emptyText))) continue;
    const cells = row.querySelectorAll('td,[role="cell"],.ant-table-cell,.el-table__cell,.vxe-body--column');
    if (cells.length >= 2) return true;
  }
  return false;
}
"""


EXTRACT_RESULT_ROWS_SCRIPT = """
(noDataTexts) => {
  const excludedContainers = [
    '[role="listbox"]',
    '.ant-select-dropdown',
    '.el-select-dropdown',
    '.arco-select-popup',
    '.t-select__dropdown',
    '.n-select-menu',
    '.semi-select-popover',
    '.vxe-select--panel'
  ].join(',');
  const normalize = (text) => (text || '').replace(/\\s+/g, ' ').trim();
  const findVisibleTable = () => {
    const tableSelectors = [
      '.ant-table',
      '.el-table',
      '.vxe-table',
      'table'
    ];
    for (const selector of tableSelectors) {
      for (const table of document.querySelectorAll(selector)) {
        if (table.closest(excludedContainers)) continue;
        const rect = table.getBoundingClientRect();
        const style = window.getComputedStyle(table);
        if (style.display === 'none' || style.visibility === 'hidden') continue;
        if (rect.width <= 0 || rect.height <= 0) continue;
        return table;
      }
    }
    return null;
  };
  const table = findVisibleTable();
  if (!table) return [];

  const headers = [...table.querySelectorAll('thead th, .ant-table-thead th, [role="columnheader"]')]
    .map((el) => normalize(el.innerText || el.textContent || ''))
    .filter(Boolean);
  if (!headers.length) return [];

  const rows = [];
  for (const row of table.querySelectorAll('tbody tr, .ant-table-tbody tr, [role="row"]')) {
    if (row.closest(excludedContainers)) continue;
    const rect = row.getBoundingClientRect();
    const style = window.getComputedStyle(row);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    if (rect.width <= 0 || rect.height <= 0) continue;

    const values = [...row.querySelectorAll('td,[role="cell"],.ant-table-cell,.el-table__cell,.vxe-body--column')]
      .map((cell) => normalize(cell.innerText || cell.textContent || ''));
    const joinedText = values.join(' ');
    if (!joinedText) continue;
    if (noDataTexts.some((emptyText) => joinedText.includes(emptyText))) continue;
    if (values.length < 2) continue;

    const record = {};
    for (let index = 0; index < Math.min(headers.length, values.length); index += 1) {
      if (!headers[index] || !values[index]) continue;
      record[headers[index]] = values[index];
    }
    if (Object.keys(record).length > 0) {
      rows.push(record);
    }
  }
  return rows;
}
"""


GET_SELECTED_SERIES_NAME_SCRIPT = """
() => {
  const input = document.evaluate(
    "//*[normalize-space(text())='车系']/following::input[@placeholder='请选择车系'][1]",
    document,
    null,
    XPathResult.FIRST_ORDERED_NODE_TYPE,
    null
  ).singleNodeValue;
  if (!input) return '';
  return String(input.value || '').trim();
}
"""


GET_SELECTED_TAB_TOKEN_SCRIPT = """
() => {
  const activeTab = document.querySelector('.el-tabs__item.is-active, [role="tab"][aria-selected="true"], .is-active');
  if (!activeTab) return '';
  const text = (activeTab.innerText || activeTab.textContent || '').replace(/\\s+/g, ' ').trim();
  if (text.includes('普通库存')) return 'MDJL04';
  if (text.includes('限量车')) return 'MDJL06';
  if (text.includes('专项车')) return 'MDJL05';
  if (text.includes('可售展车')) return 'MDJL07';
  return '';
}
"""


GET_RUNTIME_SERIES_OPTIONS_SCRIPT = """
() => {
  const root = document.querySelector('.pre_lock_wrapper')?.__vue__?.$options?.parent
    || document.querySelector('.new-layout-table-wrapper')?.__vue__?.$options?.parent;
  const rows = root?._data?.filterOpts?.carSeries?.rows;
  if (!Array.isArray(rows)) return [];

  return rows
    .map((item) => ({
      name: String(item?.carSeriesCn || '').trim(),
      code: String(item?.carSeriesCode || '').trim(),
    }))
    .filter((item) => item.name && item.code);
}
"""


GET_RUNTIME_QUERY_CONTEXT_SCRIPT = """
() => {
  const root = document.querySelector('.pre_lock_wrapper')?.__vue__?.$options?.parent
    || document.querySelector('.new-layout-table-wrapper')?.__vue__?.$options?.parent;
  const rows = root?._data?.filterOpts?.carSeries?.rows;
  const seriesOptions = Array.isArray(rows)
    ? rows
        .map((item) => ({
          name: String(item?.carSeriesCn || '').trim(),
          code: String(item?.carSeriesCode || '').trim(),
        }))
        .filter((item) => item.name && item.code)
    : [];

  const methods = root?.$options?.methods || {};
  let tabToken = '';
  for (const methodName of ['fetchListData', 'fetchFilterParams']) {
    const method = methods[methodName];
    if (!method) continue;
    const source = String(method);
    const match = source.match(/token:"([^"]+)"/);
    if (match?.[1]) {
      tabToken = match[1];
      break;
    }
  }

  return {
    series_options: seriesOptions,
    tab_token: tabToken,
  };
}
"""
