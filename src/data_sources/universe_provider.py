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


class UniverseProvider:
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
        expected_exchange = exchange.lower()
        codes = []
        for position, value in raw.items():
            text = str(value).strip()
            if not text or text.lower() in {"nan", "none"}:
                raise ValueError(f"{exchange} row {position}: empty stock code")
            try:
                code = normalize_code(text)
            except ValueError as exc:
                raise ValueError(f"{exchange} row {position}: invalid stock code {value!r}") from exc
            if not code.startswith(expected_exchange):
                raise ValueError(
                    f"{exchange} row {position}: stock code {value!r} belongs to {code[:2].upper()}, "
                    f"not {exchange}"
                )
            codes.append(code)
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
