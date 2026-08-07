from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import tempfile
import time

import pandas as pd

from .models import FetchResult, normalize_code


UNIVERSE_COLUMNS = [
    "code", "raw_code", "exchange", "name", "list_date", "delist_date",
    "list_status", "industry", "source", "updated_at",
]

_COLUMN_ALIASES = {
    "raw_code": ("代码", "证券代码", "A股代码", "code", "symbol"),
    "name": ("名称", "证券简称", "A股简称", "name"),
    "list_date": ("上市日期", "A股上市日期", "list_date"),
    "industry": ("行业", "所属行业", "industry"),
}


def _pick_column(frame: pd.DataFrame, aliases: tuple[str, ...], required: bool = False):
    for name in aliases:
        if name in frame.columns:
            return frame[name]
    if required:
        raise ValueError(f"missing required columns; expected one of {aliases}")
    return pd.Series([""] * len(frame), index=frame.index, dtype=str)


class _PipelineUniverseProvider:
    def __init__(self, sources=None, status_store=None, now=None,
                 retry: int = 3, retry_delay: float = 1.0,
                 min_refresh_ratio: float = 0.90):
        self.sources = sources or self._default_sources()
        self.status_store = status_store
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.retry = max(1, retry)
        self.retry_delay = max(0.0, retry_delay)
        self.min_refresh_ratio = min(max(float(min_refresh_ratio), 0.0), 1.0)

    @staticmethod
    def _default_sources():
        def sh():
            import akshare as ak
            main = ak.stock_info_sh_name_code(symbol="主板A股")
            star = ak.stock_info_sh_name_code(symbol="科创板")
            return pd.concat([main, star], ignore_index=True)

        def sz():
            import akshare as ak
            return ak.stock_info_sz_name_code(symbol="A股列表")

        def bj():
            import akshare as ak
            return ak.stock_info_bj_name_code()

        return {"SH": sh, "SZ": sz, "BJ": bj}

    def fetch(self) -> FetchResult:
        started = self.now()
        date = started.strftime("%Y-%m-%d")
        normalized = []
        errors = []
        for exchange in ("SH", "SZ", "BJ"):
            source = self.sources.get(exchange)
            if source is None:
                errors.append(f"{exchange}: source not configured")
                continue
            try:
                frame = None
                last_error = None
                for attempt in range(self.retry):
                    try:
                        frame = source()
                        last_error = None
                        break
                    except Exception as exc:
                        last_error = exc
                        if attempt < self.retry - 1 and self.retry_delay:
                            time.sleep(self.retry_delay * (attempt + 1))
                if last_error is not None:
                    raise last_error
                normalized.append(self._normalize_exchange(frame, exchange, started))
            except Exception as exc:
                errors.append(f"{exchange}: {exc}")
        if errors:
            result = FetchResult.failed(
                dataset="universe", date=date, source="akshare",
                message="; ".join(errors), scope="SH,SZ,BJ",
            )
        else:
            data = pd.concat(normalized, ignore_index=True)
            data = data.drop_duplicates("code", keep="last").sort_values("code").reset_index(drop=True)
            result = FetchResult.success(
                dataset="universe", date=date, source="akshare",
                expected_count=len(data), actual_count=len(data), scope="SH,SZ,BJ",
                started_at=started, finished_at=self.now(), data=data,
            )
        if self.status_store is not None:
            self.status_store.record(result)
        return result

    def _normalize_exchange(self, frame: pd.DataFrame, exchange: str,
                            updated_at: datetime) -> pd.DataFrame:
        if frame is None or frame.empty:
            raise ValueError("empty response")
        raw = _pick_column(frame, _COLUMN_ALIASES["raw_code"], required=True).astype(str)
        names = _pick_column(frame, _COLUMN_ALIASES["name"], required=True).astype(str)
        list_dates = pd.to_datetime(
            _pick_column(frame, _COLUMN_ALIASES["list_date"]), errors="coerce"
        ).dt.strftime("%Y-%m-%d").fillna("")
        industries = _pick_column(frame, _COLUMN_ALIASES["industry"]).fillna("").astype(str)
        expected_suffix = "." + exchange
        codes = [normalize_code(value + expected_suffix) for value in raw.str.extract(r"(\d{6})", expand=False)]
        return pd.DataFrame({
            "code": codes,
            "raw_code": [code[2:] for code in codes],
            "exchange": exchange,
            "name": names.str.strip(),
            "list_date": list_dates,
            "delist_date": "",
            "list_status": "listed",
            "industry": industries.str.strip(),
            "source": "akshare",
            "updated_at": updated_at.isoformat(),
        }, columns=UNIVERSE_COLUMNS)

    def refresh(self, path) -> pd.DataFrame:
        path = Path(path)
        result = self.fetch()
        if result.data is None:
            raise RuntimeError(result.message or "universe refresh failed")
        fresh = result.data.copy()
        if path.exists():
            prior = pd.read_csv(path, dtype=str).fillna("")
            if set(UNIVERSE_COLUMNS).issubset(prior.columns):
                active_prior = prior[
                    prior["list_status"].isin(["listed", "suspended_listing", "unknown"])
                ]
                for exchange in ("SH", "SZ", "BJ"):
                    expected = int((active_prior["exchange"] == exchange).sum())
                    actual = int((fresh["exchange"] == exchange).sum())
                    ratio = actual / expected if expected else 1.0
                    if expected and ratio < self.min_refresh_ratio:
                        message = (
                            f"{exchange} universe shrank {actual}/{expected} ({ratio:.1%}) "
                            f"below {self.min_refresh_ratio:.0%}"
                        )
                        if self.status_store is not None:
                            self.status_store.record(FetchResult.failed(
                                dataset="universe", date=result.date, source=result.source,
                                message=message, expected_count=expected, actual_count=actual,
                                scope="SH,SZ,BJ",
                            ))
                        raise RuntimeError(message)
                delisted = prior[prior["list_status"] == "delisted"]
                fresh = pd.concat([fresh, delisted], ignore_index=True)
                fresh = fresh.drop_duplicates("code", keep="first")
        fresh = fresh[UNIVERSE_COLUMNS].sort_values("code").reset_index(drop=True)
        self._atomic_write(fresh, path)
        return fresh

    @staticmethod
    def _atomic_write(frame: pd.DataFrame, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        os.close(fd)
        try:
            frame.to_csv(temp_name, index=False)
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

import csv
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

import requests

from report_logic import normalize_stock_code, summarize_market_universe

EASTMONEY_URL = "https://82.push2.eastmoney.com/api/qt/clist/get"
EASTMONEY_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
FIELDS = ["code", "name", "market", "industry", "status", "is_st", "tradable", "updated_at", "effective_date", "source"]


def _records_data_date(records: Iterable[dict[str, Any]]) -> str:
    dates: list[str] = []
    for row in records:
        value = str(row.get("updated_at", "") or "").strip()
        if value:
            dates.append(value[:10])
    return max(dates) if dates else ""


def _cache_timestamp(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    except OSError:
        return ""


def _as_bool(value: Any, *, default: bool = False) -> bool:
    """Parse booleans from Python values and CSV strings without bool("False") traps."""
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return bool(default)


def _market_from_row(code: str, market_id: Any) -> str:
    if code.startswith(("920", "430", "830", "870", "400")):
        return "bj"
    if str(market_id) == "1" or code.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh"
    return "sz"


def _default_fetch_page(page: int, page_size: int) -> list[dict[str, Any]]:
    params = {
        "pn": page, "pz": page_size, "po": 1, "np": 1, "fltt": 2, "invt": 2,
        "fid": "f12", "fs": EASTMONEY_FS, "fields": "f12,f13,f14,f100",
    }
    last_error: Exception | None = None
    for trust_env in (True, False):
        for attempt in range(3):
            try:
                session = requests.Session()
                session.trust_env = trust_env
                response = session.get(EASTMONEY_URL, params=params, timeout=(8, 25), headers={"User-Agent": "Mozilla/5.0"})
                response.raise_for_status()
                payload = response.json()
                return list(((payload.get("data") or {}).get("diff") or []))
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"东财证券列表请求失败: {last_error}")


def _default_fallback_fetch() -> list[dict[str, Any]]:
    """Fetch the full A-share code/name universe through AkShare."""
    import akshare as ak

    frame = ak.stock_info_a_code_name()
    if frame is None or frame.empty or not {"code", "name"}.issubset(frame.columns):
        raise RuntimeError("AkShare 全 A 证券列表为空或字段不完整")
    return [
        {
            "code": str(row["code"]).strip(),
            "name": str(row["name"]).strip(),
            "source": "akshare",
        }
        for _, row in frame.iterrows()
    ]


class _ReportUniverseProvider:
    def __init__(
        self,
        cache_path: str | Path,
        *,
        fetcher: Callable[[int, int], list[dict[str, Any]]] | None = None,
        fallback_fetcher: Callable[[], list[dict[str, Any]]] | None = None,
        page_size: int = 500,
        min_total: int = 4500,
    ):
        self.cache_path = Path(cache_path)
        custom_primary = fetcher is not None
        self.fetcher = fetcher or _default_fetch_page
        self.fallback_fetcher = fallback_fetcher if fallback_fetcher is not None else (
            None if custom_primary else _default_fallback_fetch
        )
        self.page_size = max(1, int(page_size))
        self.min_total = max(1, int(min_total))

    @staticmethod
    def normalize_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        result: dict[str, dict[str, Any]] = {}
        for item in rows:
            raw_code = str(item.get("f12", item.get("code", "")) or "").strip().zfill(6)
            market = str(item.get("market", "") or "").lower().strip() or _market_from_row(raw_code, item.get("f13"))
            code = normalize_stock_code(f"{market}{raw_code}")
            if not code:
                continue
            name = str(item.get("f14", item.get("name", "")) or "").strip()
            status = str(item.get("status", "active") or "active").strip().lower()
            inferred_st = name.upper().replace(" ", "").startswith(("ST", "*ST"))
            raw_is_st = item.get("is_st")
            is_st = inferred_st if raw_is_st is None or str(raw_is_st).strip() == "" else _as_bool(raw_is_st, default=inferred_st)
            inferred_tradable = status not in {"suspended", "delisted", "terminated", "inactive"}
            raw_tradable = item.get("tradable")
            tradable = inferred_tradable if raw_tradable is None or str(raw_tradable).strip() == "" else _as_bool(raw_tradable, default=inferred_tradable)
            result[code] = {
                "code": code, "name": name, "market": code[:2],
                "industry": str(item.get("f100", item.get("industry", "")) or "").strip(),
                "status": status, "is_st": is_st, "tradable": tradable,
                "updated_at": str(item.get("updated_at", now) or now),
                "effective_date": str(item.get("effective_date", item.get("updated_at", now)) or now)[:10],
                "source": str(item.get("source", "eastmoney") or "eastmoney"),
            }
        return [result[key] for key in sorted(result)]

    def load_cache(self) -> list[dict[str, Any]]:
        if not self.cache_path.exists():
            return []
        with self.cache_path.open("r", encoding="utf-8-sig", newline="") as handle:
            return self.normalize_rows(csv.DictReader(handle))

    @staticmethod
    def _effective_date(row: dict[str, Any]) -> str:
        value = row.get("effective_date") or row.get("updated_at") or ""
        text = str(value).strip()
        return text[:10] if len(text) >= 10 else ""

    @classmethod
    def records_as_of(cls, records: Iterable[dict[str, Any]], report_date: str) -> list[dict[str, Any]]:
        """Return the latest known security state not newer than ``report_date``.

        A current security master is not evidence of a historical state. When no
        dated snapshot exists on or before the report date, the state fields are
        explicitly marked unknown instead of inferring ST/tradability from the
        current name. This prevents cases such as a later un-ST name contaminating
        an older report (and vice versa).
        """
        target = str(report_date or "")[:10]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for raw in records or ():
            if not isinstance(raw, dict):
                continue
            code = normalize_stock_code(raw.get("code", ""))
            if not code:
                continue
            grouped.setdefault(code, []).append(dict(raw))

        result: list[dict[str, Any]] = []
        for code in sorted(grouped):
            rows = grouped[code]
            dated = [row for row in rows if cls._effective_date(row)]
            eligible = [row for row in dated if cls._effective_date(row) <= target]
            if eligible:
                chosen = max(eligible, key=cls._effective_date)
                output = dict(chosen)
                output["effective_date"] = cls._effective_date(chosen)
                output["status_as_of"] = str(chosen.get("status") or "unknown")
                output["name_as_of"] = str(chosen.get("name") or "") or None
                output["is_st_as_of"] = chosen.get("is_st") if chosen.get("is_st") is not None else None
                output["tradable_as_of"] = chosen.get("tradable") if chosen.get("tradable") is not None else None
            else:
                # Keep identity fields for diagnostics, but do not let callers
                # mistake them for a report-date state.
                latest = max(rows, key=lambda row: cls._effective_date(row) or "")
                output = dict(latest)
                output["effective_date"] = cls._effective_date(latest)
                output["status_as_of"] = "unknown"
                output["name_as_of"] = None
                output["is_st_as_of"] = None
                output["tradable_as_of"] = None
                output.setdefault("state_unknown_reason", "缺少不晚于报告日的证券主数据快照")
            result.append(output)
        return result

    @classmethod
    def security_master_as_of(cls, records: Iterable[dict[str, Any]], report_date: str) -> dict[str, dict[str, Any]]:
        return {row["code"]: row for row in cls.records_as_of(records, report_date) if row.get("code")}

    def _write_atomic(self, records: list[dict[str, Any]]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows([{key: row.get(key, "") for key in FIELDS} for row in records])
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.cache_path)

    def _validate_records(self, records: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str], bool]:
        summary = summarize_market_universe([row["code"] for row in records])
        errors = list(summary.get("errors", []))
        if len(records) < self.min_total:
            errors.append(f"证券总数不足: {len(records)} < {self.min_total}")
        valid = not summary.get("missing_market_prefixes") and not errors
        return summary, errors, valid

    def refresh(self) -> dict[str, Any]:
        raw: list[dict[str, Any]] = []
        primary_errors: list[str] = []
        try:
            for page in range(1, 1000):
                batch = self.fetcher(page, self.page_size)
                if not batch:
                    break
                raw.extend(batch)
                if len(batch) < self.page_size:
                    break
            records = self.normalize_rows(raw)
            summary, errors, valid = self._validate_records(records)
            if valid:
                self._write_atomic(records)
                return {
                    **summary, "records": records, "updated": True,
                    "used_fallback": False, "used_stale": False,
                    "errors": [], "source": "eastmoney", "fallback_source": "akshare",
                    "data_date": _records_data_date(records),
                    "source_timestamp": max((str(row.get("updated_at", "") or "") for row in records), default=""),
                    "cache_updated_at": _cache_timestamp(self.cache_path),
                }
            primary_errors.extend(errors)
            if self.fallback_fetcher is None:
                return {
                    **summary,
                    "records": records,
                    "updated": False,
                    "used_fallback": False,
                    "errors": primary_errors,
                    "source": "eastmoney",
                }
        except Exception as exc:
            primary_errors.append(str(exc))

        if self.fallback_fetcher is not None:
            try:
                fallback_records = self.normalize_rows(self.fallback_fetcher())
                summary, errors, valid = self._validate_records(fallback_records)
                if valid:
                    self._write_atomic(fallback_records)
                    return {
                        **summary,
                        "records": fallback_records,
                        "updated": True,
                        "used_fallback": True,
                        "used_stale": False,
                        "errors": primary_errors,
                        "source": "akshare", "fallback_source": "akshare",
                        "data_date": _records_data_date(fallback_records),
                        "source_timestamp": max((str(row.get("updated_at", "") or "") for row in fallback_records), default=""),
                        "cache_updated_at": _cache_timestamp(self.cache_path),
                    }
                primary_errors.extend(f"AkShare: {error}" for error in errors)
            except Exception as exc:
                primary_errors.append(f"AkShare 证券列表请求失败: {exc}")

        cached = self.load_cache()
        summary = summarize_market_universe([row["code"] for row in cached])
        return {
            **summary,
            "records": cached,
            "updated": False,
            "used_fallback": False,
            "used_stale": bool(cached),
            "errors": primary_errors,
            "source": "cache" if cached else "eastmoney",
            "fallback_source": "akshare",
            "data_date": _records_data_date(cached),
            "source_timestamp": max((str(row.get("updated_at", "") or "") for row in cached), default=""),
            "cache_updated_at": _cache_timestamp(self.cache_path),
        }

    def load_or_refresh(self, *, refresh: bool = False) -> dict[str, Any]:
        cached = self.load_cache()
        cached_summary = summarize_market_universe([row["code"] for row in cached])
        if cached and not cached_summary.get("missing_market_prefixes") and not refresh:
            return {
                **cached_summary, "records": cached, "updated": False,
                "used_fallback": False, "used_stale": False, "source": "cache",
                "fallback_source": "akshare", "data_date": _records_data_date(cached),
                "source_timestamp": max((str(row.get("updated_at", "") or "") for row in cached), default=""),
                "cache_updated_at": _cache_timestamp(self.cache_path),
            }
        result = self.refresh()
        if not result.get("updated") and cached:
            return {
                **cached_summary, "records": cached, "updated": False,
                "used_fallback": bool(result.get("used_fallback", False)), "used_stale": True,
                "errors": result.get("errors", []), "source": "cache",
                "fallback_source": result.get("fallback_source", "akshare"),
                "data_date": _records_data_date(cached),
                "source_timestamp": max((str(row.get("updated_at", "") or "") for row in cached), default=""),
                "cache_updated_at": _cache_timestamp(self.cache_path),
            }
        return result
class UniverseProvider:
    """Select the canonical pipeline provider or the report cache provider by constructor contract."""

    def __new__(cls, *args, **kwargs):
        report_keys = {"cache_path", "fetcher", "fallback_fetcher", "page_size", "min_total"}
        if (args and isinstance(args[0], (str, Path))) or report_keys.intersection(kwargs):
            return _ReportUniverseProvider(*args, **kwargs)
        return _PipelineUniverseProvider(*args, **kwargs)

    @staticmethod
    def _default_sources():
        return _PipelineUniverseProvider._default_sources()

    @staticmethod
    def normalize_rows(rows):
        return _ReportUniverseProvider.normalize_rows(rows)

    @staticmethod
    def records_as_of(records, report_date):
        return _ReportUniverseProvider.records_as_of(records, report_date)

    @staticmethod
    def security_master_as_of(records, report_date):
        return _ReportUniverseProvider.security_master_as_of(records, report_date)
