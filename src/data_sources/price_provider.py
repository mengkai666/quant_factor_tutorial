from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

import pandas as pd
import requests

from .models import FetchResult, FetchStatus, normalize_code


PRICE_COLUMNS = [
    "date", "code", "close_raw", "close_qfq", "trade_status",
    "source_raw", "source_qfq", "fetched_at",
]
FETCH_COLUMNS = [
    "date", "close_raw", "close_qfq", "trade_status", "source_raw", "source_qfq",
]


def parse_tencent_kline_payload(code: str, raw_payload: dict, qfq_payload: dict) -> pd.DataFrame:
    raw_item = (raw_payload.get("data") or {}).get(code) or {}
    qfq_item = (qfq_payload.get("data") or {}).get(code) or {}
    raw_rows = raw_item.get("day")
    qfq_rows = qfq_item.get("qfqday")
    if raw_rows is None:
        raise ValueError(f"{code} response missing day")
    if qfq_rows is None:
        raise ValueError(f"{code} response missing qfqday")
    def required(rows, close_name):
        malformed = [row for row in rows if not isinstance(row, (list, tuple)) or len(row) < 3]
        if malformed:
            raise ValueError(f"{code} malformed kline rows")
        return pd.DataFrame({"date": [row[0] for row in rows],
                             close_name: [row[2] for row in rows]})

    raw = required(raw_rows, "close_raw")
    qfq = required(qfq_rows, "close_qfq")
    out = raw.merge(qfq, on="date", how="inner")
    out["close_raw"] = pd.to_numeric(out["close_raw"], errors="coerce")
    out["close_qfq"] = pd.to_numeric(out["close_qfq"], errors="coerce")
    out["trade_status"] = "traded"
    out["source_raw"] = "tencent_raw"
    out["source_qfq"] = "tencent_qfq"
    return out.dropna(subset=["close_raw", "close_qfq"])


class PriceProvider:
    """Fetch explicit raw/qfq daily closes with an injectable per-code adapter."""

    def __init__(self, fetcher=None, status_store=None, now=None, max_workers: int = 8,
                 retry: int = 3, retry_delay: float = 0.5):
        self.fetcher = fetcher or self._default_fetcher
        self.status_store = status_store
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.max_workers = max_workers
        self.retry = max(1, retry)
        self.retry_delay = max(0.0, retry_delay)

    @staticmethod
    def _default_fetcher(code: str, start: str, end: str) -> pd.DataFrame:
        code = normalize_code(code)
        if code.startswith("bj"):
            return PriceProvider._sina_fetcher(code, start, end)
        try:
            return PriceProvider._tencent_fetcher(code, start, end)
        except Exception:
            return PriceProvider._sina_fetcher(code, start, end)

    @staticmethod
    def _tencent_fetcher(code: str, start: str, end: str) -> pd.DataFrame:
        session = requests.Session()
        session.trust_env = False
        url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}

        def request(adjust: str):
            param = f"{code},day,{start},{end},1000,{adjust}"
            response = session.get(url, params={"param": param}, headers=headers, timeout=15)
            response.raise_for_status()
            return response.json()

        return parse_tencent_kline_payload(code, request(""), request("qfq"))

    @staticmethod
    def _sina_fetcher(code: str, start: str, end: str) -> pd.DataFrame:
        import akshare as ak
        start_compact = start.replace("-", "")
        end_compact = end.replace("-", "")
        raw = ak.stock_zh_a_daily(symbol=code, start_date=start_compact,
                                  end_date=end_compact, adjust="")
        qfq = ak.stock_zh_a_daily(symbol=code, start_date=start_compact,
                                  end_date=end_compact, adjust="qfq")
        if raw is None or raw.empty:
            return pd.DataFrame(columns=FETCH_COLUMNS)
        if qfq is None or qfq.empty:
            raise ValueError(f"qfq response empty for {code}")
        raw_part = raw[["date", "close"]].rename(columns={"close": "close_raw"})
        qfq_part = qfq[["date", "close"]].rename(columns={"close": "close_qfq"})
        out = raw_part.merge(qfq_part, on="date", how="inner")
        out["trade_status"] = "traded"
        out["source_raw"] = "sina_raw"
        out["source_qfq"] = "sina_qfq"
        return out

    def fetch_range(self, universe: pd.DataFrame, dates: list[str]) -> FetchResult:
        started = self.now()
        if not dates:
            result = FetchResult.failed(
                dataset="prices", date=started.strftime("%Y-%m-%d"), source="price_provider",
                message="no trading dates supplied", scope="SH,SZ,BJ",
            )
            self._record(result)
            return result
        if "code" not in universe.columns:
            result = FetchResult.failed(
                dataset="prices", date=dates[-1], source="price_provider",
                message="universe missing code column", scope="SH,SZ,BJ",
            )
            self._record(result)
            return result

        codes = [normalize_code(value) for value in universe["code"].dropna().unique()]
        expected = len(codes)
        rows = []
        empty_codes = []
        completed_codes = []
        def fetch_one(code):
            last_error = None
            for attempt in range(self.retry):
                try:
                    return code, self.fetcher(code, dates[0], dates[-1]), None
                except Exception as exc:
                    last_error = exc
                    if attempt < self.retry - 1 and self.retry_delay:
                        time.sleep(self.retry_delay * (attempt + 1))
            return code, None, last_error

        try:
            with ThreadPoolExecutor(max_workers=max(1, min(self.max_workers, len(codes)))) as pool:
                futures = {pool.submit(fetch_one, code): code for code in codes}
                fetched_items = []
                for future in as_completed(futures):
                    fetched_items.append(future.result())
            failed_messages = []
            for code, fetched, error in fetched_items:
                if error is not None:
                    empty_codes.append(code)
                    failed_messages.append(f"{code}: {error}")
                    continue
                if fetched is None or fetched.empty:
                    empty_codes.append(code)
                    continue
                completed_codes.append(code)
                missing = set(FETCH_COLUMNS) - set(fetched.columns)
                if missing:
                    raise ValueError(f"{code} response missing {sorted(missing)}")
                part = fetched[FETCH_COLUMNS].copy()
                part["date"] = pd.to_datetime(part["date"], errors="coerce").dt.strftime("%Y-%m-%d")
                part = part[part["date"].isin(dates)].dropna(subset=["date"])
                part["code"] = code
                part["fetched_at"] = self.now().isoformat()
                rows.append(part[PRICE_COLUMNS])
        except Exception as exc:
            result = FetchResult.failed(
                dataset="prices", date=dates[-1], source="price_provider",
                message=str(exc), expected_count=expected,
                actual_count=len(completed_codes), scope="SH,SZ,BJ",
            )
            self._record(result)
            return result

        data = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=PRICE_COLUMNS)
        data = data.drop_duplicates(["date", "code"], keep="last").sort_values(["code", "date"]).reset_index(drop=True)
        actual = len(completed_codes)
        data = self._materialize_status_rows(universe, codes, dates, data)
        if actual == expected:
            result = FetchResult.success(
                dataset="prices", date=dates[-1], source="price_provider",
                expected_count=expected, actual_count=actual, scope="SH,SZ,BJ",
                started_at=started, finished_at=self.now(), data=data,
            )
        else:
            message = f"coverage {actual}/{expected}"
            if empty_codes:
                message += f"; empty codes={len(empty_codes)}"
            if 'failed_messages' in locals() and failed_messages:
                message += "; " + "; ".join(failed_messages[:10])
            result = FetchResult.partial(
                dataset="prices", date=dates[-1], source="price_provider",
                expected_count=expected, actual_count=actual, message=message,
                scope="SH,SZ,BJ", data=data,
            )
        self._record(result)
        return result

    def _materialize_status_rows(self, universe: pd.DataFrame, codes: list[str],
                                 dates: list[str], data: pd.DataFrame) -> pd.DataFrame:
        list_dates = {}
        if "list_date" in universe.columns:
            list_dates = dict(zip(universe["code"].map(normalize_code),
                                  universe["list_date"].fillna("").astype(str)))
        existing = set(zip(data["code"], data["date"])) if not data.empty else set()
        missing_rows = []
        fetched_at = self.now().isoformat()
        for code in codes:
            list_date = list_dates.get(code, "")
            for date in dates:
                if (code, date) in existing:
                    continue
                status = "not_listed" if list_date and date < list_date else "suspended"
                missing_rows.append({
                    "date": date, "code": code, "close_raw": pd.NA,
                    "close_qfq": pd.NA, "trade_status": status,
                    "source_raw": "", "source_qfq": "", "fetched_at": fetched_at,
                })
        if missing_rows:
            missing_frame = pd.DataFrame(missing_rows, columns=PRICE_COLUMNS)
            if data.empty:
                data = missing_frame
            else:
                data = pd.concat([data.astype(object), missing_frame.astype(object)],
                                 ignore_index=True)
        return data.sort_values(["code", "date"]).reset_index(drop=True)

    def rebuild(self, universe: pd.DataFrame, dates: list[str], candidate_path,
                batch_size: int = 100, resume: bool = True) -> FetchResult:
        candidate_path = Path(candidate_path)
        existing = pd.DataFrame(columns=PRICE_COLUMNS)
        completed = set()
        if resume and candidate_path.exists():
            loaded = pd.read_csv(candidate_path, dtype={"code": str, "date": str})
            if set(PRICE_COLUMNS).issubset(loaded.columns):
                existing = loaded[PRICE_COLUMNS]
                date_set = set(dates)
                for code, group in existing.groupby("code"):
                    covers_dates = date_set.issubset(set(group["date"].astype(str)))
                    statuses = set(group["trade_status"].astype(str))
                    trustworthy = "traded" in statuses or statuses == {"not_listed"}
                    if covers_dates and trustworthy:
                        completed.add(code)
        canonical = universe.copy()
        canonical["code"] = canonical["code"].map(normalize_code)
        remaining = canonical[~canonical["code"].isin(completed)]
        results = []
        combined = existing
        for start_idx in range(0, len(remaining), max(1, batch_size)):
            batch = remaining.iloc[start_idx:start_idx + batch_size]
            result = self.fetch_range(batch, dates)
            results.append(result)
            if result.data is not None and not result.data.empty:
                if combined.empty:
                    combined = result.data.copy()
                else:
                    combined = pd.concat([combined, result.data], ignore_index=True)
                combined = combined.drop_duplicates(["date", "code"], keep="last")
                combined = combined.sort_values(["code", "date"]).reset_index(drop=True)
                self.atomic_write(combined, candidate_path)
        expected = len(canonical)
        actual = combined["code"].nunique() if not combined.empty else 0
        if actual == expected and all(r.status is FetchStatus.SUCCESS for r in results):
            result = FetchResult.success(dataset="prices", date=dates[-1], source="price_provider",
                                         expected_count=expected, actual_count=actual,
                                         scope="SH,SZ,BJ", data=combined)
        else:
            messages = [r.message for r in results if r.message]
            result = FetchResult.partial(dataset="prices", date=dates[-1], source="price_provider",
                                         expected_count=expected, actual_count=actual,
                                         message="; ".join(messages), scope="SH,SZ,BJ", data=combined)
        self._record(result)
        return result

    def _record(self, result: FetchResult) -> None:
        if self.status_store is not None:
            self.status_store.record(result)

    @staticmethod
    def atomic_write(frame: pd.DataFrame, path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        os.close(fd)
        try:
            frame.to_csv(temp_name, index=False)
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    @staticmethod
    def promote(candidate_path, official_path) -> None:
        candidate = Path(candidate_path)
        official = Path(official_path)
        if not candidate.exists():
            raise FileNotFoundError(candidate)
        official.parent.mkdir(parents=True, exist_ok=True)
        os.replace(candidate, official)
