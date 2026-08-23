from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import math
from typing import Any, Iterable

import numpy as np
import pandas as pd

from report_logic import normalize_stock_code

from .http_session import get_session
from .models import FetchResult, FetchStatus, normalize_code


PRICE_COLUMNS = [
    "date", "code", "close_raw", "close_qfq", "trade_status",
    "source_raw", "source_qfq", "fetched_at",
]
FETCH_COLUMNS = [
    "date", "close_raw", "close_qfq", "trade_status", "source_raw", "source_qfq",
]

CANONICAL_PRICE_COLUMNS = [
    "date", "code", "close_raw", "close_qfq", "close_legacy",
    "price_basis", "source", "source_timestamp",
]

def normalize_price_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    """Normalize legacy/provider rows to the auditable report price contract."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=CANONICAL_PRICE_COLUMNS)
    out = frame.copy()
    if "date" not in out.columns or "code" not in out.columns:
        return pd.DataFrame(columns=CANONICAL_PRICE_COLUMNS)
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    out["code"] = out["code"].map(normalize_code)
    out = out.dropna(subset=["date", "code"])
    basis = out.get("price_basis", pd.Series("", index=out.index)).astype(str).str.lower()
    for col in ("close_raw", "close_qfq", "close_legacy"):
        if col not in out.columns:
            out[col] = pd.NA
    if "close" in out.columns:
        legacy = pd.to_numeric(out["close"], errors="coerce")
        raw_mask = out["close_raw"].isna() & basis.isin(["raw", "close_raw", "raw_close"])
        qfq_mask = out["close_qfq"].isna() & basis.isin(["qfq", "close_qfq", "adjusted"])
        out.loc[raw_mask, "close_raw"] = legacy[raw_mask]
        out.loc[qfq_mask, "close_qfq"] = legacy[qfq_mask]
        out.loc[~raw_mask & ~qfq_mask, "close_legacy"] = legacy[~raw_mask & ~qfq_mask]
    for col in ("close_raw", "close_qfq", "close_legacy"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if "source" not in out.columns:
        out["source"] = out.get("source_raw", out.get("source_qfq", "legacy_cache"))
    out["source"] = out["source"].fillna("unknown").astype(str)
    if "source_timestamp" not in out.columns:
        out["source_timestamp"] = out.get("fetched_at", "")
    out["source_timestamp"] = out["source_timestamp"].fillna("").astype(str)
    out["price_basis"] = basis.where(basis.ne(""), "unknown")
    has_raw = out["close_raw"].notna()
    has_qfq = out["close_qfq"].notna()
    has_legacy = out["close_legacy"].notna()
    out.loc[has_raw & ~has_qfq & ~has_legacy, "price_basis"] = "raw"
    out.loc[~has_raw & has_qfq & ~has_legacy, "price_basis"] = "qfq"
    out.loc[~has_raw & ~has_qfq & has_legacy, "price_basis"] = "legacy"
    out.loc[has_raw & has_qfq, "price_basis"] = "mixed"
    return (out[CANONICAL_PRICE_COLUMNS].drop_duplicates(["date", "code"], keep="last")
            .sort_values(["date", "code"]).reset_index(drop=True))

def merge_price_frames(*frames: pd.DataFrame | None) -> pd.DataFrame:
    """Merge source frames by date/code, preferring latest non-null values."""
    pieces = [normalize_price_frame(frame) for frame in frames if frame is not None and not frame.empty]
    if not pieces:
        return pd.DataFrame(columns=CANONICAL_PRICE_COLUMNS)
    merged = pd.concat(pieces, ignore_index=True)
    rows = []
    for (_, _), group in merged.groupby(["date", "code"], sort=False):
        row = group.iloc[-1].copy()
        for col in ("close_raw", "close_qfq", "close_legacy"):
            vals = group[col].dropna()
            if not vals.empty:
                row[col] = vals.iloc[-1]
        for col in ("source", "source_timestamp"):
            vals = group[col].replace("", pd.NA).dropna()
            if not vals.empty:
                row[col] = vals.iloc[-1]
        available = [b for b, col in (("raw", "close_raw"), ("qfq", "close_qfq"), ("legacy", "close_legacy")) if pd.notna(row[col])]
        row["price_basis"] = available[0] if len(available) == 1 else ("mixed" if available else "unknown")
        rows.append(row)
    return pd.DataFrame(rows)[CANONICAL_PRICE_COLUMNS].sort_values(["date", "code"]).reset_index(drop=True)

def price_value_column(frame: pd.DataFrame | None, basis: str = "qfq", allow_legacy: bool = True) -> str | None:
    if frame is None or frame.empty:
        return None
    wanted = "close_raw" if str(basis).lower() == "raw" else "close_qfq"
    if wanted in frame.columns and pd.to_numeric(frame[wanted], errors="coerce").notna().any():
        return wanted
    if allow_legacy and "close_legacy" in frame.columns and pd.to_numeric(frame["close_legacy"], errors="coerce").notna().any():
        return "close_legacy"
    # Compatibility for callers that inspect an old in-memory frame before
    # normalize_price_frame has been applied.
    if allow_legacy and "close" in frame.columns and pd.to_numeric(frame["close"], errors="coerce").notna().any():
        return "close"
    return None

def price_coverage(frame: pd.DataFrame | None, basis: str = "qfq", dates=None, codes=None) -> dict:
    column = price_value_column(frame, basis, allow_legacy=False)
    if frame is None or frame.empty or column is None:
        return {"column": column, "rows": 0, "covered": 0, "coverage_pct": 0.0, "dates": 0, "codes": 0}
    work = frame.copy()
    if dates is not None:
        work = work[work["date"].astype(str).isin({str(d)[:10] for d in dates})]
    if codes is not None:
        work = work[work["code"].map(normalize_code).isin({normalize_code(c) for c in codes})]
    valid = pd.to_numeric(work[column], errors="coerce").notna()
    total, covered = int(len(work)), int(valid.sum())
    return {"column": column, "rows": total, "covered": covered, "coverage_pct": round(covered / total * 100, 2) if total else 0.0, "dates": int(work.loc[valid, "date"].nunique()), "codes": int(work.loc[valid, "code"].nunique())}


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




def parse_tencent_qfq_payload(code: str, qfq_payload: dict) -> pd.DataFrame:
    """Parse Tencent qfq rows without inventing a raw close."""
    qfq_item = (qfq_payload.get("data") or {}).get(code) or {}
    qfq_rows = qfq_item.get("qfqday")
    if qfq_rows is None:
        raise ValueError(f"{code} response missing qfqday")
    malformed = [
        row for row in qfq_rows
        if not isinstance(row, (list, tuple)) or len(row) < 3
    ]
    if malformed:
        raise ValueError(f"{code} malformed qfq kline rows")
    out = pd.DataFrame({
        "date": [row[0] for row in qfq_rows],
        "close_qfq": [row[2] for row in qfq_rows],
    })
    out["close_raw"] = pd.NA
    out["close_qfq"] = pd.to_numeric(out["close_qfq"], errors="coerce")
    out["trade_status"] = "traded"
    out["source_raw"] = ""
    out["source_qfq"] = "tencent_qfq"
    return out[FETCH_COLUMNS].dropna(subset=["close_qfq"])
class PriceProvider:
    """Fetch explicit raw/qfq daily closes with an injectable per-code adapter."""

    def __init__(self, fetcher=None, qfq_fetcher=None, status_store=None, now=None,
                 max_workers: int = 8, retry: int = 3, retry_delay: float = 0.5):
        self.fetcher = fetcher or self._default_fetcher
        self.qfq_fetcher = qfq_fetcher or self._default_qfq_fetcher
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
    def _default_qfq_fetcher(code: str, start: str, end: str) -> pd.DataFrame:
        code = normalize_code(code)
        if code.startswith("bj"):
            return PriceProvider._sina_qfq_fetcher(code, start, end)
        try:
            return PriceProvider._tencent_qfq_fetcher(code, start, end)
        except Exception:
            return PriceProvider._sina_qfq_fetcher(code, start, end)
    @staticmethod
    def _tencent_fetcher(code: str, start: str, end: str) -> pd.DataFrame:
        # 线程复用 keep-alive 会话 (见 http_session): 逐股补缺每天上万次请求,
        # 每次现开 Session 等于每次重做 TLS 握手, 实测 45/s vs 73/s (同为 8 并发)。
        session = get_session()
        url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}

        def request(adjust: str):
            param = f"{code},day,{start},{end},1000,{adjust}"
            response = session.get(url, params={"param": param}, headers=headers, timeout=15)
            response.raise_for_status()
            return response.json()

        return parse_tencent_kline_payload(code, request(""), request("qfq"))

    @staticmethod
    def _tencent_qfq_fetcher(code: str, start: str, end: str) -> pd.DataFrame:
        session = get_session()
        url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
        param = f"{code},day,{start},{end},1000,qfq"
        response = session.get(url, params={"param": param}, headers=headers, timeout=15)
        response.raise_for_status()
        return parse_tencent_qfq_payload(code, response.json())

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


    @staticmethod
    def _sina_qfq_fetcher(code: str, start: str, end: str) -> pd.DataFrame:
        import akshare as ak
        qfq = ak.stock_zh_a_daily(
            symbol=code,
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adjust="qfq",
        )
        if qfq is None or qfq.empty:
            raise ValueError(f"qfq response empty for {code}")
        out = qfq[["date", "close"]].rename(columns={"close": "close_qfq"})
        out["close_raw"] = pd.NA
        out["trade_status"] = "traded"
        out["source_raw"] = ""
        out["source_qfq"] = "akshare_qfq"
        return out[FETCH_COLUMNS]

    def fetch_qfq_range(self, universe: pd.DataFrame, dates: list[str]) -> FetchResult:
        """Fetch adjusted closes only, preserving raw-price gaps as null."""
        provider = PriceProvider(
            fetcher=self.qfq_fetcher,
            qfq_fetcher=self.qfq_fetcher,
            status_store=self.status_store,
            now=self.now,
            max_workers=self.max_workers,
            retry=self.retry,
            retry_delay=self.retry_delay,
        )
        return provider.fetch_range(universe, dates)
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

def _float(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def normalize_price_rows(rows: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source in rows or ():
        row = dict(source)
        code = normalize_stock_code(row.get("code", row.get("代码", "")))
        date = str(row.get("date", row.get("日期", "")) or "").strip()
        basis = str(row.get("price_basis", row.get("adjustment", "")) or "").strip().lower()
        raw = _float(row.get("close_raw"))
        qfq = _float(row.get("close_qfq"))
        legacy_close = _float(row.get("close", row.get("收盘", row.get("收盘价"))))
        legacy_value = _float(row.get("close_legacy"))
        if basis in {"raw", "none", "unadjusted", "不复权"}:
            raw = raw if raw is not None else legacy_close
            qfq = None if "close_qfq" not in row else qfq
            basis = "raw"
        elif basis in {"qfq", "forward", "前复权"}:
            qfq = qfq if qfq is not None else legacy_close
            raw = None if "close_raw" not in row else raw
            basis = "qfq"
        elif basis in {"legacy", "legacy_mixed", "mixed"}:
            legacy_value = legacy_value if legacy_value is not None else legacy_close
            basis = "legacy_mixed"
        elif raw is not None and qfq is not None:
            basis = "raw+qfq"
        elif raw is not None and qfq is None:
            basis = "raw"
        elif qfq is not None and raw is None:
            basis = "qfq"
        elif legacy_close is not None:
            legacy_value = legacy_close
            basis = "legacy_mixed"
        else:
            basis = basis or "unknown"
        result.append({
            "code": code, "date": date, "close_raw": raw, "close_qfq": qfq,
            "close_legacy": legacy_value,
            "price_basis": basis, "source": str(row.get("source", "") or ""),
            "source_timestamp": str(row.get("source_timestamp", row.get("updated_at", "")) or ""),
        })
    return result


_CANONICAL_PRICE_COLUMNS = frozenset((
    "date", "code", "close_raw", "close_qfq", "close_legacy",
    "price_basis", "source", "source_timestamp",
))


def _normalize_price_frame_fast(frame: pd.DataFrame):
    """规范列名价格表的整列归一化快路; 不适用时返回 None 交回逐行路径。

    只在列集合恰好等于 8 个规范列时启用 (data/price_history_cache.csv 本身就是),
    此时旧的单列别名 close/收盘/收盘价/adjustment 都不存在 → 逐行逻辑里的
    ``legacy_close`` 恒为 None, 三个价格列原样保留, **只有 price_basis 需要重算**,
    于是可以整列向量化。与逐行路径在真实 18.5 万行缓存上 .equals 校验一致。
    (逐行 to_dict + 每行造 dict 实测 2.5s/次, 这里 ~0.2s。)
    """
    if len(frame.columns) != len(set(map(str, frame.columns))):
        return None
    if set(map(str, frame.columns)) != set(_CANONICAL_PRICE_COLUMNS):
        return None

    def _to_number(column: pd.Series) -> pd.Series:
        # _float() 把 inf 视作缺失, to_numeric 不会, 故显式抹平。
        numeric = pd.to_numeric(column, errors="coerce")
        return numeric.replace([np.inf, -np.inf], np.nan)

    raw = _to_number(frame["close_raw"])
    qfq = _to_number(frame["close_qfq"])
    legacy = _to_number(frame["close_legacy"])
    basis_text = frame["price_basis"].map(lambda v: str(v or "").strip().lower())
    basis_arr = basis_text.to_numpy(dtype=object)
    raw_ok, qfq_ok = raw.notna().to_numpy(), qfq.notna().to_numpy()
    fallback = np.where(basis_arr != "", basis_arr, "unknown").astype(object)
    basis = np.where(
        basis_text.isin(("raw", "none", "unadjusted", "不复权")).to_numpy(), "raw",
        np.where(
            basis_text.isin(("qfq", "forward", "前复权")).to_numpy(), "qfq",
            np.where(
                basis_text.isin(("legacy", "legacy_mixed", "mixed")).to_numpy(), "legacy_mixed",
                np.where(
                    raw_ok & qfq_ok, "raw+qfq",
                    np.where(raw_ok, "raw", np.where(qfq_ok, "qfq", fallback)),
                ),
            ),
        ),
    ).astype(object)

    return pd.DataFrame({
        "date": frame["date"].map(lambda v: str(v or "").strip()),
        "code": frame["code"].map(normalize_stock_code),
        "close_raw": raw.to_numpy(), "close_qfq": qfq.to_numpy(),
        "close_legacy": legacy.to_numpy(), "price_basis": basis,
        "source": frame["source"].map(lambda v: str(v or "")),
        "source_timestamp": frame["source_timestamp"].map(lambda v: str(v or "")),
    })


# 归一化结果记忆 (按内容哈希): 一轮报告里同一份 18 万行价格缓存要被归一化 6~10 次,
# 单次 2.5s (逐行 to_dict 转换), 而内容哈希只要 0.09s。纯函数, 记忆安全; 返回副本
# 以防调用方就地改列。只留最近 _NORM_MEMO_MAX 份, 避免长驻内存膨胀。
_NORM_FRAME_MEMO: dict = {}
_NORM_MEMO_MAX = 4


def _frame_content_key(frame: pd.DataFrame):
    """→ 可哈希的内容指纹; 取不到 (含不可哈希对象) 返回 None 表示不可记忆。"""
    try:
        from pandas.util import hash_pandas_object
        return (
            frame.shape,
            tuple(map(str, frame.columns)),
            int(hash_pandas_object(frame, index=False).sum()),
        )
    except Exception:
        return None


def normalize_price_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    """归一化 (带内容哈希记忆); 语义与 _normalize_price_frame_uncached 完全一致。"""
    if frame is None or frame.empty:
        return _normalize_price_frame_uncached(frame)
    key = _frame_content_key(frame)
    if key is None:
        return _normalize_price_frame_uncached(frame)
    cached = _NORM_FRAME_MEMO.get(key)
    if cached is None:
        cached = _normalize_price_frame_uncached(frame)
        if len(_NORM_FRAME_MEMO) >= _NORM_MEMO_MAX:
            _NORM_FRAME_MEMO.pop(next(iter(_NORM_FRAME_MEMO)), None)
        _NORM_FRAME_MEMO[key] = cached
    return cached.copy()


def _normalize_price_frame_uncached(frame: pd.DataFrame | None) -> pd.DataFrame:
    """把旧的 ``date,code,close`` 缓存转换为可审计的双口径结构。

    旧单列 close 无法从文件本身判断是不复权还是前复权，因此只进入
    ``close_legacy``，并标记为 ``legacy_mixed``。这保证历史数值仍可用于
    兼容展示，但不会被质量门禁误报成 raw/qfq 覆盖。
    """
    columns = [
        "date", "code", "close_raw", "close_qfq", "close_legacy",
        "price_basis", "source", "source_timestamp",
    ]
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)
    result = _normalize_price_frame_fast(frame)
    if result is None:
        result = pd.DataFrame(normalize_price_rows(frame.to_dict("records")), columns=columns)
    else:
        result = result[columns]
    if result.empty:
        return result
    result["date"] = result["date"].astype(str).str.strip()
    result["code"] = result["code"].astype(str).str.strip()
    for col in ("close_raw", "close_qfq", "close_legacy"):
        result[col] = pd.to_numeric(result[col], errors="coerce")
    result = result[(result["date"] != "") & (result["code"] != "")]
    return result.reset_index(drop=True)


def _first_number(values: pd.Series) -> float | None:
    for value in values:
        number = _float(value)
        if number is not None:
            return number
    return None


def _first_text(values: pd.Series) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _derive_basis(raw: float | None, qfq: float | None, legacy: float | None) -> str:
    if raw is not None and qfq is not None:
        return "raw+qfq"
    if raw is not None:
        return "raw"
    if qfq is not None:
        return "qfq"
    if legacy is not None:
        return "legacy_mixed"
    return "unknown"


def merge_price_frames(*frames: pd.DataFrame | None) -> pd.DataFrame:
    """按 code/date 合并价格来源，允许同一行同时补齐 raw 与 qfq。"""
    normalized = [normalize_price_frame(frame) for frame in frames if frame is not None]
    normalized = [frame for frame in normalized if not frame.empty]
    if not normalized:
        return normalize_price_frame(None)
    combined = pd.concat(normalized, ignore_index=True)
    rows: list[dict[str, Any]] = []
    for (code, date), group in combined.groupby(["code", "date"], sort=False):
        raw = _first_number(group["close_raw"])
        qfq = _first_number(group["close_qfq"])
        legacy = _first_number(group["close_legacy"])
        rows.append({
            "code": code,
            "date": date,
            "close_raw": raw,
            "close_qfq": qfq,
            "close_legacy": legacy,
            "price_basis": _derive_basis(raw, qfq, legacy),
            "source": _first_text(group["source"]),
            "source_timestamp": _first_text(group["source_timestamp"]),
        })
    result = pd.DataFrame(rows)
    return result.sort_values(["code", "date"]).reset_index(drop=True)


def price_value_column(frame: pd.DataFrame, basis: str = "qfq", *, allow_legacy: bool = True) -> str | None:
    """返回指定口径可用于计算的列名，兼容旧测试/调用方的 close。"""
    basis = str(basis or "qfq").lower()
    preferred = "close_qfq" if basis == "qfq" else "close_raw"
    if preferred in frame.columns and frame[preferred].notna().any():
        return preferred
    if allow_legacy and "close_legacy" in frame.columns and frame["close_legacy"].notna().any():
        return "close_legacy"
    if allow_legacy and "close" in frame.columns and frame["close"].notna().any():
        return "close"
    return None


# 价格矩阵记忆 (按内容哈希 + 口径): 一轮报告里 phase_resonance / stock_representatives /
# 主线强度追踪 会拿同一份 18 万行缓存反复建矩阵 (实测 9 次共 4.1s), 而内容哈希只要 0.09s。
# 纯函数, 记忆安全; 返回副本以防调用方就地改列。只留最近 _MATRIX_MEMO_MAX 份。
_MATRIX_MEMO: dict = {}
_MATRIX_MEMO_MAX = 6


def build_price_matrix(
    frame: pd.DataFrame,
    basis: str = "qfq",
    *,
    allow_legacy: bool = True,
) -> pd.DataFrame:
    """建价格矩阵 (带内容哈希记忆); 语义与 _build_price_matrix_uncached 完全一致。"""
    if frame is None or frame.empty:
        return _build_price_matrix_uncached(frame, basis, allow_legacy=allow_legacy)
    content = _frame_content_key(frame)
    if content is None:
        return _build_price_matrix_uncached(frame, basis, allow_legacy=allow_legacy)
    key = (content, str(basis).lower(), bool(allow_legacy))
    cached = _MATRIX_MEMO.get(key)
    if cached is None:
        cached = _build_price_matrix_uncached(frame, basis, allow_legacy=allow_legacy)
        if len(_MATRIX_MEMO) >= _MATRIX_MEMO_MAX:
            _MATRIX_MEMO.pop(next(iter(_MATRIX_MEMO)), None)
        _MATRIX_MEMO[key] = cached
    return cached.copy()


def _build_price_matrix_uncached(
    frame: pd.DataFrame,
    basis: str = "qfq",
    *,
    allow_legacy: bool = True,
) -> pd.DataFrame:
    """Build a per-stock price matrix, stitching legacy history when possible."""
    if frame is None or frame.empty or not {"date", "code"}.issubset(frame.columns):
        return pd.DataFrame()
    preferred = "close_raw" if str(basis).lower() == "raw" else "close_qfq"
    fallback = "close_legacy" if "close_legacy" in frame.columns else (
        "close" if "close" in frame.columns else None
    )
    columns = [column for column in (preferred, fallback) if column and column in frame.columns]
    if not columns:
        return pd.DataFrame()

    work = frame[["date", "code", *dict.fromkeys(columns)]].copy()
    work["date"] = work["date"].astype(str).str.strip()
    work["code"] = work["code"].map(normalize_code)
    for column in columns:
        work[column] = pd.to_numeric(work[column], errors="coerce")
    work["_value"] = work[preferred] if preferred in work else pd.NA

    if allow_legacy and fallback in work:
        # 按股票求 target/legacy 的中位数比例, 再用它把 legacy 折算进 target 的缺口。
        # ⚠️ 旧实现 for 循环逐股 groupby (5538 组 × 多次 Series 运算 = 单次 17s,
        #    一轮报告调 4 次 ≈ 68s), 这里改成一次 groupby.median 向量化, 结果逐元素等价。
        target = pd.to_numeric(work["_value"], errors="coerce")
        legacy = pd.to_numeric(work[fallback], errors="coerce")
        overlap = target.notna() & legacy.notna() & legacy.ne(0)
        ratio_by_code = None
        if overlap.any():
            ratios = (target[overlap] / legacy[overlap]).replace(
                [np.inf, -np.inf], np.nan
            ).dropna()
            if not ratios.empty:
                ratio_by_code = ratios.groupby(work["code"][ratios.index]).median()
        factor = (
            work["code"].map(ratio_by_code).astype(float).fillna(1.0)
            if ratio_by_code is not None and not ratio_by_code.empty
            else 1.0
        )
        work["_value"] = target.fillna(legacy * factor)

    work["_value"] = pd.to_numeric(work["_value"], errors="coerce").round(8)
    work = work.dropna(subset=["_value"])
    if work.empty:
        return pd.DataFrame()
    # ⚠️ pivot_table(aggfunc="last") 走 groupby 聚合路径, 单次比 pivot 慢一个量级;
    #    先按 (date,code) 保留最后一条再 pivot, 结果逐元素等价 (已与旧实现对账 0 差异)。
    return (work.drop_duplicates(["date", "code"], keep="last")
                .pivot(index="date", columns="code", values="_value")
                .sort_index())


def validate_price_contract(rows: Iterable[dict[str, Any]] | None) -> dict[str, Any]:
    errors: list[str] = []
    counts = {"raw": 0, "qfq": 0}
    seen: set[tuple[str, str, str]] = set()
    for index, row in enumerate(rows or ()):
        code, date = str(row.get("code", "")), str(row.get("date", ""))
        basis = str(row.get("price_basis", "") or "").lower()
        raw, qfq = _float(row.get("close_raw")), _float(row.get("close_qfq"))
        if not code or not date:
            errors.append(f"第{index + 1}行缺少 code/date")
        if basis not in {"raw", "qfq", "raw+qfq", "legacy_mixed"}:
            errors.append(f"{code}/{date} 价格口径不明确: {basis or 'missing'}")
        if basis in {"raw", "raw+qfq"} and raw is None:
            errors.append(f"{code}/{date} 缺少 close_raw")
        if basis in {"qfq", "raw+qfq"} and qfq is None:
            errors.append(f"{code}/{date} 缺少 close_qfq")
        if basis == "legacy_mixed" and _float(row.get("close_legacy")) is None:
            errors.append(f"{code}/{date} 缺少 close_legacy")
        key = (code, date, basis)
        if key in seen:
            errors.append(f"重复价格记录: {code}/{date}/{basis}")
        seen.add(key)
        if basis in counts:
            counts[basis] += 1
    return {"valid": not errors, "errors": list(dict.fromkeys(errors)), "counts": counts, "total": sum(counts.values())}


def price_coverage(rows: Iterable[dict[str, Any]] | None, universe_codes: Iterable[str], basis: str = "raw") -> dict[str, Any]:
    universe = {normalize_stock_code(code) for code in universe_codes if normalize_stock_code(code)}
    covered = {
        normalize_stock_code(row.get("code"))
        for row in (rows or ())
        if _float(row.get(f"close_{basis}")) is not None
    }
    covered &= universe
    total = len(universe)
    return {"basis": basis, "market_total": total, "market_covered": len(covered), "coverage_pct": round((len(covered) / total * 100) if total else 0.0, 2), "missing_codes": sorted(universe - covered)}
