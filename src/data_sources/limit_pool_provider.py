from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from .models import FetchResult, normalize_code


LIMIT_POOL_COLUMNS = ["date", "pool_type", "code", "name", "limit_count", "source"]


class LimitPoolProvider:
    def __init__(self, fetch_zt=None, fetch_dt=None, status_store=None, now=None):
        self.fetch_zt = fetch_zt or self._default_zt
        self.fetch_dt = fetch_dt or self._default_dt
        self.status_store = status_store
        self.now = now or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _default_zt(date: str):
        import akshare as ak
        return ak.stock_zt_pool_em(date=date.replace("-", ""))

    @staticmethod
    def _default_dt(date: str):
        import akshare as ak
        return ak.stock_zt_pool_dtgc_em(date=date.replace("-", ""))

    def fetch_day(self, date: str) -> FetchResult:
        started = self.now()
        parts = []
        errors = []
        for pool_type, fetcher in (("ZT", self.fetch_zt), ("DT", self.fetch_dt)):
            try:
                raw = fetcher(date)
                if raw is None:
                    raise ValueError("empty response object")
                parts.append(self._normalize(raw, date, pool_type))
            except Exception as exc:
                errors.append(f"{pool_type}: {exc}")
        data = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=LIMIT_POOL_COLUMNS)
        actual = len(data)
        if errors and data.empty:
            result = FetchResult.failed(dataset="limit_pool", date=date, source="akshare_em",
                                        message="; ".join(errors), scope="SH,SZ,BJ")
        elif errors:
            result = FetchResult.partial(dataset="limit_pool", date=date, source="akshare_em",
                                         expected_count=actual, actual_count=actual,
                                         message="; ".join(errors), scope="SH,SZ,BJ", data=data)
        elif data.empty:
            result = FetchResult.zero(dataset="limit_pool", date=date, source="akshare_em",
                                      scope="SH,SZ,BJ", data=data)
        else:
            result = FetchResult.success(dataset="limit_pool", date=date, source="akshare_em",
                                         expected_count=actual, actual_count=actual,
                                         scope="SH,SZ,BJ", started_at=started,
                                         finished_at=self.now(), data=data)
        if self.status_store is not None:
            self.status_store.record(result)
        return result

    @staticmethod
    def _normalize(frame: pd.DataFrame, date: str, pool_type: str) -> pd.DataFrame:
        if frame.empty:
            return pd.DataFrame(columns=LIMIT_POOL_COLUMNS)
        code_col = next((c for c in ("代码", "证券代码", "code") if c in frame.columns), None)
        if code_col is None:
            raise ValueError("missing code column")
        name_col = next((c for c in ("名称", "证券简称", "name") if c in frame.columns), None)
        count_col = next((c for c in ("连板数", "limit_count") if c in frame.columns), None)
        codes = frame[code_col].map(normalize_code)
        names = frame[name_col].astype(str) if name_col else pd.Series([""] * len(frame), index=frame.index)
        counts = pd.to_numeric(frame[count_col], errors="coerce").fillna(1).astype(int) if count_col else 1
        return pd.DataFrame({
            "date": date,
            "pool_type": pool_type,
            "code": codes,
            "name": names,
            "limit_count": counts,
            "source": "akshare_em",
        }, columns=LIMIT_POOL_COLUMNS)
