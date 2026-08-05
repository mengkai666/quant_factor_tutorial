from __future__ import annotations

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import time

from .models import FetchResult, normalize_code


PLATE_COLUMNS = ["date", "code", "plate_name", "source"]


class PlateProvider:
    def __init__(self, fetcher=None, status_store=None, now=None, max_workers: int = 8,
                 retry: int = 3, retry_delay: float = 0.3):
        self.fetcher = fetcher or self._eastmoney_fetcher
        self.status_store = status_store
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.max_workers = max(1, max_workers)
        self.retry = max(1, retry)
        self.retry_delay = max(0.0, retry_delay)

    @staticmethod
    def _eastmoney_fetcher(code: str, _date: str):
        code = normalize_code(code)
        market = {"sh": "1", "sz": "0", "bj": "0"}[code[:2]]
        params = {
            "spt": 3, "secid": f"{market}.{code[2:]}", "fields": "f12,f13,f14",
            "po": 1, "pz": 300, "pi": 0, "np": 1, "fltt": 2, "invt": 2,
            "ut": "f057cbcbce2a86e2866ab8877db1d059",
        }
        session = requests.Session()
        session.trust_env = False
        response = session.get("https://push2.eastmoney.com/api/qt/slist/get", params=params,
                               headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        response.raise_for_status()
        payload = response.json()
        if payload.get("rc") == 102:
            return []
        data = payload.get("data")
        if data is None:
            return None
        diff = data.get("diff") or []
        items = diff.values() if isinstance(diff, dict) else diff
        return [item.get("f14") for item in items if isinstance(item, dict) and item.get("f14")]

    def fetch_codes(self, codes, date: str) -> FetchResult:
        started = self.now()
        canonical = [normalize_code(code) for code in codes]
        rows = []
        failed = []
        completed = 0

        def fetch_one(code):
            last_error = None
            for attempt in range(self.retry):
                try:
                    names = self.fetcher(code, date)
                    if names is None:
                        raise ValueError("empty response object")
                    return code, names, None
                except Exception as exc:
                    last_error = exc
                    if attempt < self.retry - 1 and self.retry_delay:
                        time.sleep(self.retry_delay * (attempt + 1))
            return code, None, last_error

        fetched_items = []
        if canonical:
            with ThreadPoolExecutor(max_workers=min(self.max_workers, len(canonical))) as pool:
                futures = [pool.submit(fetch_one, code) for code in canonical]
                for future in as_completed(futures):
                    fetched_items.append(future.result())
        for code, names, error in fetched_items:
            if error is not None:
                failed.append(f"{code}: {error}")
                continue
            completed += 1
            for name in names:
                rows.append({"date": date, "code": code, "plate_name": str(name),
                             "source": "eastmoney"})
        data = pd.DataFrame(rows, columns=PLATE_COLUMNS)
        expected = len(canonical)
        if completed == 0 and failed:
            result = FetchResult.failed(dataset="plates", date=date, source="eastmoney",
                                        message="; ".join(failed), expected_count=expected,
                                        scope="SH,SZ,BJ", data=data)
        elif failed:
            result = FetchResult.partial(dataset="plates", date=date, source="eastmoney",
                                         expected_count=expected, actual_count=completed,
                                         message="; ".join(failed), scope="SH,SZ,BJ", data=data)
        elif data.empty:
            result = FetchResult.zero(dataset="plates", date=date, source="eastmoney",
                                      expected_count=expected, actual_count=completed,
                                      scope="SH,SZ,BJ", data=data)
        else:
            result = FetchResult.success(dataset="plates", date=date, source="eastmoney",
                                         expected_count=expected, actual_count=completed,
                                         scope="SH,SZ,BJ", started_at=started,
                                         finished_at=self.now(), data=data)
        if self.status_store is not None:
            self.status_store.record(result)
        return result
