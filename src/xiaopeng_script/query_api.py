from __future__ import annotations

import logging
from dataclasses import dataclass

from playwright.sync_api import Page


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueryApiConfig:
    endpoint: str
    token: str
    extra_payload: dict[str, object]


@dataclass(frozen=True)
class SeriesQueryTask:
    series_name: str
    series_code: str


TAB_QUERY_API_MAP: dict[str, QueryApiConfig] = {
    "普通库存": QueryApiConfig(
        endpoint="/api/car-admin/admin/carPreLockOperation/queryCarSourceCommon",
        token="MDJL04",
        extra_payload={},
    ),
    "限量车": QueryApiConfig(
        endpoint="/api/car-admin/admin/carPreLockOperation/queryCarSourceEopLimitNew",
        token="MDJL04",
        extra_payload={},
    ),
    "专项车": QueryApiConfig(
        endpoint="/api/car-admin/admin/carPreLockOperation/queryCarSourceSpecial",
        token="MDJL04",
        extra_payload={
            "storeAgeBegin": None,
            "storeAgeEnd": None,
            "purchaseDiscountMin": None,
            "purchaseDiscountMax": None,
            "saleDiscountMin": None,
            "saleDiscountMax": None,
        },
    ),
    "可售展车": QueryApiConfig(
        endpoint="/api/car-admin/admin/carPreLockOperation/queryCarSourceRetired",
        token="MDJL04",
        extra_payload={
            "storeAgeBegin": None,
            "storeAgeEnd": None,
            "purchaseDiscountMin": None,
            "purchaseDiscountMax": None,
            "saleDiscountMin": None,
            "saleDiscountMax": None,
        },
    ),
}


def query_car_source_service(
    *,
    page: Page,
    tab_name: str,
    series_code: str,
    page_size: int = 40,
    page_index: int = 1,
    token: str | None = None,
) -> list[dict[str, object]]:
    api_config = _get_query_api_config(tab_name)
    payload = _build_payload(
        series_code=series_code,
        page_size=page_size,
        page_index=page_index,
        api_config=api_config,
        token=token,
    )
    response_payload = _post_query(page=page, endpoint=api_config.endpoint, payload=payload)
    rows = _extract_rows(
        response_payload=response_payload,
        tab_name=tab_name,
        series_code=series_code,
    )
    LOGGER.debug("接口查询完成: %s / %s -> %s 条", tab_name, series_code, len(rows))
    return rows


def batch_query_car_source_service(
    *,
    page: Page,
    tab_name: str,
    tasks: list[SeriesQueryTask],
    page_size: int = 40,
    page_index: int = 1,
    token: str | None = None,
) -> dict[str, list[dict[str, object]]]:
    api_config = _get_query_api_config(tab_name)
    request_payloads = [
        {
            "series_name": task.series_name,
            "series_code": task.series_code,
            "payload": _build_payload(
                series_code=task.series_code,
                page_size=page_size,
                page_index=page_index,
                api_config=api_config,
                token=token,
            ),
        }
        for task in tasks
    ]
    response_items = page.evaluate(
        BATCH_QUERY_API_EVALUATE_SCRIPT,
        {
            "endpoint": api_config.endpoint,
            "requests": request_payloads,
        },
    )
    if not isinstance(response_items, list):
        raise RuntimeError(f"批量接口查询返回异常: {tab_name}")

    result: dict[str, list[dict[str, object]]] = {}
    for item in response_items:
        if not isinstance(item, dict):
            continue
        series_code = str(item.get("series_code", "")).strip()
        if not series_code:
            continue
        response_payload = item.get("response")
        if isinstance(response_payload, dict) and response_payload.get("error"):
            LOGGER.warning(
                "批量接口查询失败，已跳过: %s / %s: %s",
                tab_name,
                series_code,
                response_payload.get("error"),
            )
            result[series_code] = []
            continue
        rows = _extract_rows(
            response_payload=response_payload,
            tab_name=tab_name,
            series_code=series_code,
        )
        result[series_code] = rows
    return result


def _get_query_api_config(tab_name: str) -> QueryApiConfig:
    api_config = TAB_QUERY_API_MAP.get(tab_name)
    if api_config is None:
        raise RuntimeError(f"未配置栏目接口: {tab_name}")
    return api_config


def _build_payload(
    *,
    series_code: str,
    page_size: int,
    page_index: int,
    api_config: QueryApiConfig,
    token: str | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "pageSize": page_size,
        "pageIndex": page_index,
        "carSeriesCode": series_code,
        "smallCarTypeCode": "",
        "carConfigCode": "",
        "carIncolorNameList": [],
        "carColorNameList": [],
        "vin": "",
        "bigAreaIdList": [],
        "cityCodeList": [],
        "deliveryDlrCodeList": [],
        "warehouseIdList": [],
        "vinDiscountRemark": "",
        "token": token or api_config.token,
    }
    payload.update(api_config.extra_payload)
    return payload


def _post_query(
    *,
    page: Page,
    endpoint: str,
    payload: dict[str, object],
) -> dict[str, object]:
    response_payload = page.evaluate(
        QUERY_API_EVALUATE_SCRIPT,
        {"endpoint": endpoint, "payload": payload},
    )
    if not isinstance(response_payload, dict):
        raise RuntimeError(f"接口查询返回异常: {endpoint}")
    return response_payload


def _extract_rows(
    *,
    response_payload: object,
    tab_name: str,
    series_code: str,
) -> list[dict[str, object]]:
    if not isinstance(response_payload, dict):
        raise RuntimeError(f"接口查询返回异常: {tab_name} / {series_code}")

    if response_payload.get("error"):
        raise RuntimeError(
            f"接口查询失败: {tab_name} / {series_code}: {response_payload['error']}"
        )

    data = response_payload.get("data", {})
    if not isinstance(data, dict):
        return []
    rows = data.get("rows", [])
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


QUERY_API_EVALUATE_SCRIPT = """
async ({ endpoint, payload }) => {
  const ajax = window.XDRAGON?.ajax;
  if (!ajax) {
    return { error: 'window.XDRAGON.ajax 不存在' };
  }
  try {
    const response = await ajax.post(endpoint, payload);
    return response || {};
  } catch (error) {
    const message =
      error?.response?.data?.msg
      || error?.response?.data?.message
      || error?.message
      || String(error);
    return {
      error: message,
      status: error?.response?.status ?? null,
      data: error?.response?.data ?? null,
    };
  }
}
"""


BATCH_QUERY_API_EVALUATE_SCRIPT = """
async ({ endpoint, requests }) => {
  const ajax = window.XDRAGON?.ajax;
  if (!ajax) {
    return [{
      series_code: '',
      response: { error: 'window.XDRAGON.ajax 不存在' }
    }];
  }

  const results = await Promise.all(
    requests.map(async (requestItem) => {
      try {
        const response = await ajax.post(endpoint, requestItem.payload);
        return {
          series_name: requestItem.series_name,
          series_code: requestItem.series_code,
          response: response || {},
        };
      } catch (error) {
        const message =
          error?.response?.data?.msg
          || error?.response?.data?.message
          || error?.message
          || String(error);
        return {
          series_name: requestItem.series_name,
          series_code: requestItem.series_code,
          response: {
            error: message,
            status: error?.response?.status ?? null,
            data: error?.response?.data ?? null,
          },
        };
      }
    })
  );

  return results;
}
"""
