"""Direct fallback adapters for limit-up and limit-down pools.

The endpoint contracts are based on the Apache-2.0 a-stock-data project
(simonlin1212/a-stock-data, V3.6.0), adapted to this project's provider model.
"""
from __future__ import annotations

from datetime import datetime
import re
import time

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


SOURCE_COLUMNS = ["code", "name", "limit_count"]
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json,text/plain,*/*",
}
THS_FIELDS = (
    "199112,10,9001,330323,330324,330325,9002,330329,"
    "133971,133970,1968584,3475914,9003,9004"
)


def _retry_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=SOURCE_COLUMNS)


def _parse_limit_count(value) -> int:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 1
    if isinstance(value, (int, float)):
        return max(1, int(value))
    matches = re.findall(r"(\d+)\s*板", str(value))
    return max(1, int(matches[-1])) if matches else 1


def _rows_to_frame(rows, *, count_key: str | None) -> pd.DataFrame:
    if not isinstance(rows, list):
        raise ValueError("pool rows must be a list")
    if not rows:
        return _empty_frame()

    normalized = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        code = str(item.get("c", item.get("code", ""))).strip()
        if not re.fullmatch(r"\d{6}", code):
            continue
        name = str(item.get("n", item.get("name", "")) or "").strip()
        count = _parse_limit_count(item.get(count_key) if count_key else None)
        normalized.append({"code": code, "name": name, "limit_count": count})
    if not normalized:
        raise ValueError("pool contains no valid stock rows")
    return pd.DataFrame(normalized, columns=SOURCE_COLUMNS)


class EastmoneyLimitPoolSource:
    """Direct push2ex adapter for the Eastmoney limit pools."""

    BASE_URL = "https://push2ex.eastmoney.com"
    UT = "7eea3edcaed734bea9cbfc24409ed989"

    def __init__(self, session=None, min_interval: float = 1.0,
                 clock=None, sleep=None):
        self.session = session or _retry_session()
        self.min_interval = max(0.0, float(min_interval))
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self._last_call = None

    def fetch_zt(self, date: str) -> pd.DataFrame:
        return self._fetch("getTopicZTPool", "fbt:asc", date, "lbc")

    def fetch_dt(self, date: str) -> pd.DataFrame:
        return self._fetch("getTopicDTPool", "fund:asc", date, None)

    def _fetch(self, endpoint: str, sort: str, date: str,
               count_key: str | None) -> pd.DataFrame:
        self._throttle()
        params = {
            "ut": self.UT,
            "dpt": "wz.ztzt",
            "Pageindex": 0,
            "pagesize": 10000,
            "sort": sort,
            "date": datetime.strptime(date[:10], "%Y-%m-%d").strftime("%Y%m%d"),
        }
        response = self.session.get(
            f"{self.BASE_URL}/{endpoint}",
            params=params,
            headers={**DEFAULT_HEADERS, "Referer": "https://quote.eastmoney.com/"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or "pool" not in data:
            raise ValueError("response missing data.pool")
        return _rows_to_frame(data["pool"], count_key=count_key)

    def _throttle(self) -> None:
        if self._last_call is not None:
            wait = self.min_interval - (self.clock() - self._last_call)
            if wait > 0:
                self.sleep(wait)
        self._last_call = self.clock()


class ThsLimitUpSource:
    """Independent-domain fallback for the THS limit-up explanation pool."""

    URL = "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"

    def __init__(self, session=None):
        self.session = session or requests.Session()

    def fetch_zt(self, date: str) -> pd.DataFrame:
        response = self.session.get(
            self.URL,
            params={
                "page": 1,
                "limit": 200,
                "field": THS_FIELDS,
                "filter": "HS,GEM2STAR",
                "order_field": "330324",
                "order_type": "0",
                "date": datetime.strptime(date[:10], "%Y-%m-%d").strftime("%Y%m%d"),
            },
            headers=DEFAULT_HEADERS,
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or "info" not in data:
            raise ValueError("response missing data.info")
        rows = []
        for item in data["info"]:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code", "")).strip()
            if not re.fullmatch(r"\d{6}", code):
                continue
            rows.append({
                "c": code,
                "n": str(item.get("name", "") or "").strip(),
                "limit_count": _parse_limit_count(item.get("high_days")),
            })
        return _rows_to_frame(rows, count_key="limit_count")
