from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from playwright.sync_api import Browser, Error as PlaywrightError, Page, sync_playwright

from .chrome import ChromeLaunchError, ensure_debug_chrome
from .config import (
    AppConfig,
    ensure_default_files,
    get_config_base_dir,
    get_poll_interval_range,
    load_app_config,
    pick_poll_interval_seconds,
    resolve_default_config_path,
)
from .constants import TAB_EXECUTION_MODE_PARALLEL
from .monitor import Hit, InventoryMonitor, get_or_open_target_page
from .notifier import Notifier, create_notifier
from .query_api import (
    SeriesQueryTask,
    batch_query_car_source_service,
    query_car_source_service,
)
from .state import NotifiedStore

LOGGER = logging.getLogger(__name__)
PARALLEL_PAGE_SLOT_PREFIX = "__xp_monitor_parallel_slot__"


@dataclass(frozen=True)
class SeriesOption:
    tab_name: str
    series_name: str
    series_code: str


@dataclass(frozen=True)
class TabRuntimeContext:
    tab_name: str
    page_index: int
    series_options: list[SeriesOption]
    tab_token: str


def main() -> int:
    args = parse_args()
    config_path = resolve_default_config_path(args.config)
    setup_logging(args.verbose, config_base_dir=get_config_base_dir(config_path))

    ensure_default_files(config_path)
    config = load_app_config(config_path)
    LOGGER.info("使用配置文件: %s", config_path.resolve())
    is_parallel_tab_mode = (
        config.monitor.tab_execution_mode == TAB_EXECUTION_MODE_PARALLEL
    )
    if config.monitor.tab_execution_mode == "fixed_current_tab":
        LOGGER.info("当前为固定当前栏目模式：脚本不会自动切换 tab，只查询你当前停留的栏目")
    elif config.monitor.manual_query_only and is_parallel_tab_mode:
        LOGGER.info(
            "当前为手动筛选并行模式：你手动维护筛选条件，脚本会并行点击 4 个栏目的查询按钮"
        )
    elif config.monitor.manual_query_only:
        LOGGER.info("当前为手动筛选模式：脚本只切换 4 个栏目并点击查询，筛选条件由你手动维护")

    if args.init:
        LOGGER.info("已初始化配置文件: %s", config_path.resolve())
        return 0

    if args.test_wechat or args.test_notify:
        notifier = create_notifier(config.wechat)
        notifier.send(args.test_message)
        return 0

    try:
        ensure_debug_chrome(config.chrome, startup_url=config.monitor.target_url)
    except ChromeLaunchError as exc:
        LOGGER.error("%s", exc)
        return 1

    endpoint = f"http://127.0.0.1:{config.chrome.remote_debugging_port}"
    LOGGER.info("连接 Chrome 调试端口: %s", endpoint)

    try:
        notifier = create_notifier(config.wechat)
        store = NotifiedStore(config.monitor.state_file)

        if args.diagnose or args.list_series or not is_parallel_tab_mode:
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(endpoint)
                page = get_or_open_target_page(browser, config.monitor.target_url)

                if args.wait_login:
                    input("请确认页面已经登录并停留在后台页面，然后按回车开始巡检...")

                monitor = InventoryMonitor(
                    page=page,
                    config=config.monitor,
                    notifier=notifier,
                    notified_store=store,
                )

                if args.diagnose:
                    diagnosis = monitor.diagnose_page()
                    LOGGER.info("页面地址: %s", diagnosis["url"])
                    LOGGER.info("页面标题: %s", diagnosis["title"])
                    LOGGER.info("栏目识别: %s", diagnosis["visible_tabs"])
                    LOGGER.info("查询按钮数量: %s", diagnosis["query_button_count"])
                    LOGGER.info("当前结果区是否有数据: %s", diagnosis["has_result_data"])
                    for tab_name, series_names in diagnosis["series_by_tab"].items():
                        LOGGER.info("%s: %s", tab_name, "、".join(series_names) or "未读取到车系")
                elif args.list_series:
                    series_by_tab = monitor.list_series_by_tab()
                    for tab_name, series_names in series_by_tab.items():
                        LOGGER.info("%s: %s", tab_name, "、".join(series_names) or "未读取到车系")
                elif args.once:
                    hits = monitor.run_once()
                    LOGGER.info("单次巡检完成，命中 %s 条", len(hits))
                else:
                    monitor.run_forever()
        else:
            if args.wait_login:
                _wait_for_login(endpoint, config)

            if args.once:
                hits = _run_parallel_once(endpoint, config, notifier, store)
                LOGGER.info("单次巡检完成，命中 %s 条", len(hits))
            else:
                poll_min_seconds, poll_max_seconds = get_poll_interval_range(
                    config.monitor
                )
                LOGGER.info(
                    "开始并行巡检，固定页面数 %s，随机间隔 %s-%s 秒",
                    len(config.monitor.tabs),
                    poll_min_seconds,
                    poll_max_seconds,
                )
                while True:
                    _run_parallel_once(endpoint, config, notifier, store)
                    sleep_seconds = pick_poll_interval_seconds(config.monitor)
                    LOGGER.info("下一轮将在 %s 秒后开始", sleep_seconds)
                    time.sleep(sleep_seconds)
    except KeyboardInterrupt:
        LOGGER.info("已停止巡检")
    except PlaywrightError as exc:
        LOGGER.error("浏览器自动化失败: %s", exc)
        return 1
    except RuntimeError as exc:
        LOGGER.error("%s", exc)
        return 1

    return 0


def _wait_for_login(endpoint: str, config: AppConfig) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(endpoint)
        if config.monitor.tab_execution_mode == TAB_EXECUTION_MODE_PARALLEL:
            _ensure_parallel_pages(
                browser=browser,
                target_url=config.monitor.target_url,
                count=len(config.monitor.tabs),
            )
            LOGGER.info("已准备 %s 个固定页面，请分别手动设置筛选条件。", len(config.monitor.tabs))
            for index, tab_name in enumerate(config.monitor.tabs, start=1):
                LOGGER.info("固定页面 %s 对应栏目: %s", index, tab_name)
            input(
                "请确认这 4 个固定页面都已登录，并且分别设置好了对应栏目的筛选条件，然后按回车开始巡检..."
            )
            return

        get_or_open_target_page(browser, config.monitor.target_url)
        if config.monitor.tab_execution_mode == "fixed_current_tab":
            input("请确认页面已经登录，并且已经停留在你要固定查询的栏目，然后按回车开始巡检...")
            return
        input("请确认页面已经登录并停留在后台页面，然后按回车开始巡检...")


def _run_parallel_once(
    endpoint: str,
    config: AppConfig,
    notifier: Notifier,
    store: NotifiedStore,
) -> list[Hit]:
    LOGGER.info("并行巡检已启用，使用 %s 个固定页面", len(config.monitor.tabs))
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(endpoint)
        _ensure_parallel_pages(
            browser=browser,
            target_url=config.monitor.target_url,
            count=len(config.monitor.tabs),
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(endpoint)
        tab_contexts = _prepare_tab_runtime_contexts(browser=browser, config=config)
        hits: list[Hit] = []

        with ThreadPoolExecutor(
            max_workers=len(tab_contexts),
            thread_name_prefix="tab-monitor",
        ) as executor:
            future_map = {
                executor.submit(
                    _run_single_tab_parallel_once,
                    endpoint,
                    config,
                    notifier,
                    store,
                    context,
                ): context.tab_name
                for context in tab_contexts
            }
            for future in as_completed(future_map):
                tab_name = future_map[future]
                tab_hits = future.result()
                LOGGER.info("栏目 %s 本轮命中 %s 条", tab_name, len(tab_hits))
                hits.extend(tab_hits)

        return hits


def _prepare_tab_runtime_contexts(browser: Browser, config: AppConfig) -> list[TabRuntimeContext]:
    pages = _ensure_parallel_pages(browser=browser, target_url=config.monitor.target_url, count=len(config.monitor.tabs))
    contexts: list[TabRuntimeContext] = []
    if config.monitor.manual_query_only:
        for index, tab_name in enumerate(config.monitor.tabs):
            LOGGER.info("固定页面 %s 已绑定栏目 %s，等待并行点击查询", index + 1, tab_name)
            contexts.append(
                TabRuntimeContext(
                    tab_name=tab_name,
                    page_index=index,
                    series_options=[],
                    tab_token="",
                )
            )
        return contexts

    for index, tab_name in enumerate(config.monitor.tabs):
        page = pages[index]
        monitor = InventoryMonitor(
            page=page,
            config=config.monitor,
            notifier=create_notifier(config.wechat),
            notified_store=NotifiedStore(config.monitor.state_file),
        )
        runtime_context = monitor.get_runtime_query_context(tab_name)
        raw_series_options = runtime_context.get("series_options", [])
        series_options = [
            SeriesOption(
                tab_name=tab_name,
                series_name=str(item["name"]),
                series_code=str(item["code"]),
            )
            for item in raw_series_options
            if isinstance(item, dict) and item.get("name") and item.get("code")
        ]
        LOGGER.info("栏目 %s 已读取 %s 个车系", tab_name, len(series_options))
        contexts.append(
            TabRuntimeContext(
                tab_name=tab_name,
                page_index=index,
                series_options=series_options,
                tab_token=str(runtime_context.get("tab_token", "")).strip(),
            )
        )
    return contexts


def _ensure_parallel_pages(browser: Browser, target_url: str, count: int) -> list[Page]:
    matched_pages: list[Page] = []
    for context in browser.contexts:
        for page in context.pages:
            if target_url in page.url or "/vehicle/actuals/lock" in page.url:
                matched_pages.append(page)

    ordered_pages = _order_parallel_pages(matched_pages=matched_pages, count=count)
    base_page = ordered_pages[0] if ordered_pages else get_or_open_target_page(browser, target_url)
    if not ordered_pages:
        ordered_pages.append(base_page)

    context = base_page.context
    while len(ordered_pages) < count:
        page = context.new_page()
        LOGGER.info("补齐固定后台页面: %s", target_url)
        page.goto(target_url, wait_until="domcontentloaded", timeout=30_000)
        ordered_pages.append(page)

    ordered_pages = _order_parallel_pages(matched_pages=ordered_pages, count=count)
    for index, page in enumerate(ordered_pages):
        _set_parallel_page_slot(page=page, slot_index=index)
    return ordered_pages


def _run_single_tab_parallel_once(
    endpoint: str,
    config: AppConfig,
    notifier: Notifier,
    store: NotifiedStore,
    context: TabRuntimeContext,
) -> list[Hit]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(endpoint)
        pages = _ensure_parallel_pages(
            browser=browser,
            target_url=config.monitor.target_url,
            count=len(config.monitor.tabs),
        )
        page = pages[context.page_index]
        return _run_single_tab_parallel_once_on_page(
            page=page,
            config=config,
            notifier=notifier,
            store=store,
            context=context,
        )


def _run_single_tab_parallel_once_on_page(
    *,
    page: Page,
    config: AppConfig,
    notifier: Notifier,
    store: NotifiedStore,
    context: TabRuntimeContext,
) -> list[Hit]:
    page_monitor = InventoryMonitor(
        page=page,
        config=config.monitor,
        notifier=notifier,
        notified_store=store,
    )
    if config.monitor.manual_query_only:
        LOGGER.info("栏目 %s 使用固定页面并行点击查询", context.tab_name)
        hit = page_monitor.query_current_tab(context.tab_name)
        return [hit] if hit is not None else []

    if not context.series_options:
        LOGGER.warning("栏目 %s 未读取到车系，跳过", context.tab_name)
        return []

    batch_size = _resolve_parallel_series_batch_size(
        configured_size=config.monitor.parallel_series_per_tab,
        total_size=len(context.series_options),
    )
    LOGGER.info(
        "栏目 %s 本轮车系总数 %s，并发批大小 %s",
        context.tab_name,
        len(context.series_options),
        batch_size,
    )
    series_chunks = _chunk_series_options(context.series_options, batch_size)
    hits: list[Hit] = []
    has_api_result_state = False

    for series_chunk in series_chunks:
        tasks = [
            SeriesQueryTask(
                series_name=series_option.series_name,
                series_code=series_option.series_code,
            )
            for series_option in series_chunk
        ]
        try:
            rows_by_series_code = batch_query_car_source_service(
                page=page,
                tab_name=context.tab_name,
                tasks=tasks,
                token=context.tab_token or None,
            )
        except RuntimeError as exc:
            LOGGER.warning("栏目 %s 批量接口查询失败，回退逐个查询: %s", context.tab_name, exc)
            rows_by_series_code = _query_series_chunk_sequentially(
                page=page,
                tab_name=context.tab_name,
                series_chunk=series_chunk,
                tab_token=context.tab_token,
            )

        for series_option in series_chunk:
            rows = rows_by_series_code.get(series_option.series_code, [])
            LOGGER.info(
                "接口结果行数: %s / %s -> %s",
                series_option.tab_name,
                series_option.series_name,
                len(rows),
            )
            if not rows:
                continue
            has_api_result_state = True

            hit = Hit(
                tab_name=series_option.tab_name,
                series_name=series_option.series_name,
                series_code=series_option.series_code,
                summary_lines=_format_api_result_summary(
                    rows,
                    config.monitor.result_preview_limit,
                ),
                detected_at=time_now(),
            )
            decision = store.decide_notification(
                series_key=hit.series_key,
                content_key=hit.content_key,
                summary_text="\n".join(hit.summary_lines),
                cooldown_seconds=config.monitor.notify_cooldown_seconds,
            )
            hit = Hit(
                tab_name=hit.tab_name,
                series_name=hit.series_name,
                series_code=hit.series_code,
                summary_lines=hit.summary_lines,
                detected_at=hit.detected_at,
                notify_reason=decision.reason,
            )
            if config.monitor.notify_once and not decision.should_notify:
                LOGGER.info("冷却时间内已通知过，跳过重复通知: %s", hit.content_key)
                hits.append(hit)
                continue

            notifier.send(hit.message)
            store.mark_notified(
                series_key=hit.series_key,
                content_key=hit.content_key,
                summary_text="\n".join(hit.summary_lines),
            )
            hits.append(hit)

    if has_api_result_state:
        return hits

    LOGGER.warning(
        "栏目 %s 接口模式本轮全部为 0，回退页面点击查询模式",
        context.tab_name,
    )
    fallback_hits = _run_single_tab_page_fallback(
        monitor=page_monitor,
        tab_name=context.tab_name,
        series_options=context.series_options,
    )
    if fallback_hits:
        LOGGER.info(
            "栏目 %s 页面兜底命中 %s 条",
            context.tab_name,
            len(fallback_hits),
        )
    return fallback_hits


def _run_single_tab_page_fallback(
    *,
    monitor: InventoryMonitor,
    tab_name: str,
    series_options: list[SeriesOption],
) -> list[Hit]:
    hits: list[Hit] = []
    for series_option in series_options:
        hit = monitor._query_series(tab_name, series_option.series_name)
        if hit is not None:
            hits.append(hit)
    return hits


def _query_series_chunk_sequentially(
    *,
    page: Page,
    tab_name: str,
    series_chunk: list[SeriesOption],
    tab_token: str,
) -> dict[str, list[dict[str, object]]]:
    rows_by_series_code: dict[str, list[dict[str, object]]] = {}
    for series_option in series_chunk:
        rows_by_series_code[series_option.series_code] = query_car_source_service(
            page=page,
            tab_name=tab_name,
            series_code=series_option.series_code,
            token=tab_token or None,
        )
    return rows_by_series_code


def _chunk_series_options(
    series_options: list[SeriesOption],
    chunk_size: int,
) -> list[list[SeriesOption]]:
    return [
        series_options[index : index + chunk_size]
        for index in range(0, len(series_options), chunk_size)
    ]


def _resolve_parallel_series_batch_size(
    *,
    configured_size: int,
    total_size: int,
) -> int:
    if total_size <= 0:
        return 1
    if configured_size <= 0:
        return total_size
    return min(configured_size, total_size)


def _format_api_result_summary(
    rows: list[dict[str, object]],
    preview_limit: int,
) -> list[str]:
    preview_rows = rows[:preview_limit]
    summary_lines = [f"共 {len(rows)} 条车辆数据"]
    for index, row in enumerate(preview_rows, start=1):
        detail_parts = [
            _pick_api_value(row, ["carConfigCn", "carConfigName"]),
            _pick_api_value(row, ["carHouseName", "warehouseName"]),
            _pick_api_value(row, ["vin", "vinCode", "lockVin"]),
            _pick_api_value(row, ["notLockNum"]),
        ]
        summary_lines.append(f"{index}. " + " | ".join([part for part in detail_parts if part]))
    if len(rows) > preview_limit:
        summary_lines.append(f"其余 {len(rows) - preview_limit} 条请到后台查看")
    return summary_lines


def _pick_api_value(
    row: dict[str, object],
    keys: list[str],
) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return f"{key}: {text}"
    return ""


def time_now() -> datetime:
    return datetime.now()


def _order_parallel_pages(
    *,
    matched_pages: list[Page],
    count: int,
) -> list[Page]:
    ordered_pages: list[Page | None] = [None] * count
    fallback_pages: list[Page] = []

    for page in matched_pages:
        slot_index = _get_parallel_page_slot(page)
        if slot_index is None or slot_index >= count or ordered_pages[slot_index] is not None:
            fallback_pages.append(page)
            continue
        ordered_pages[slot_index] = page

    fallback_iter = iter(fallback_pages)
    normalized_pages: list[Page] = []
    for page in ordered_pages:
        if page is not None:
            normalized_pages.append(page)
            continue
        next_page = next(fallback_iter, None)
        if next_page is not None:
            normalized_pages.append(next_page)

    return normalized_pages[:count]


def _get_parallel_page_slot(page: Page) -> int | None:
    try:
        slot_name = page.evaluate("() => window.name || ''")
    except PlaywrightError:
        return None
    if not isinstance(slot_name, str):
        return None
    if not slot_name.startswith(PARALLEL_PAGE_SLOT_PREFIX):
        return None
    slot_value = slot_name.removeprefix(PARALLEL_PAGE_SLOT_PREFIX)
    if not slot_value.isdigit():
        return None
    return int(slot_value)


def _set_parallel_page_slot(page: Page, slot_index: int) -> None:
    try:
        page.evaluate(
            "(slotName) => { window.name = slotName; }",
            f"{PARALLEL_PAGE_SLOT_PREFIX}{slot_index}",
        )
    except PlaywrightError:
        LOGGER.debug("固定页面标记写入失败，继续使用当前页面顺序: slot=%s", slot_index)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="小鹏后台车系库存巡检工具")
    parser.add_argument(
        "-c",
        "--config",
        default=None,
        help="配置文件路径；打包版默认读取可执行文件同级的 config.json",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="只生成默认配置和微信用户文件，不启动巡检",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="只巡检一轮后退出",
    )
    parser.add_argument(
        "--list-series",
        action="store_true",
        help="只读取并打印每个栏目的车系列表，不查询、不通知",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="诊断当前页面识别情况，不发通知",
    )
    parser.add_argument(
        "--test-wechat",
        action="store_true",
        help="只发送一条通知测试消息，不启动浏览器",
    )
    parser.add_argument(
        "--test-notify",
        action="store_true",
        help="只发送一条通知测试消息，不启动浏览器",
    )
    parser.add_argument(
        "--test-message",
        default="小鹏库存巡检测试通知",
        help="测试通知内容",
    )
    parser.add_argument(
        "--wait-login",
        action="store_true",
        help="启动后等待你手动确认已登录，再开始巡检",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="输出更详细日志",
    )
    return parser.parse_args()


def setup_logging(verbose: bool, config_base_dir: Path) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    log_format = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(log_format)
    root_logger.addHandler(console_handler)

    log_dir = config_base_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "monitor.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(log_format)
    root_logger.addHandler(file_handler)


if __name__ == "__main__":
    raise SystemExit(main())
