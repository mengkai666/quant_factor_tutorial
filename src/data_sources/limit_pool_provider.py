from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Iterable

import pandas as pd

from .limit_pool_sources import EastmoneyLimitPoolSource, ThsLimitUpSource
from .limit_pool_reconciliation import reconcile_limit_pool
from .models import FetchResult, FetchStatus, normalize_code


LIMIT_POOL_COLUMNS = ["date", "pool_type", "code", "name", "limit_count", "source"]
SourceFetcher = tuple[str, Callable[[str], object]]


class LimitPoolProvider:
    def __init__(self, fetch_zt=None, fetch_dt=None, status_store=None, now=None,
                 zt_fallbacks: Iterable[SourceFetcher] | None = None,
                 dt_fallbacks: Iterable[SourceFetcher] | None = None,
                 zt_crosscheck: SourceFetcher | None = None,
                 dt_crosscheck: SourceFetcher | None = None):
        self.status_store = status_store
        self.now = now or (lambda: datetime.now(timezone.utc))
        (
            self.zt_sources, self.dt_sources,
            default_zt_crosscheck, default_dt_crosscheck,
        ) = self._build_sources(
            fetch_zt, fetch_dt, zt_fallbacks, dt_fallbacks
        )
        self.zt_crosscheck = zt_crosscheck or default_zt_crosscheck
        self.dt_crosscheck = dt_crosscheck or default_dt_crosscheck

    @classmethod
    def _build_sources(cls, fetch_zt, fetch_dt, zt_fallbacks, dt_fallbacks):
        eastmoney = None
        if fetch_zt is None or fetch_dt is None:
            eastmoney = EastmoneyLimitPoolSource()

        zt_sources = [("akshare_em", fetch_zt or cls._default_zt)]
        if fetch_zt is None:
            ths = ThsLimitUpSource()
            zt_sources.extend([
                ("eastmoney_push2ex", eastmoney.fetch_zt),
                ("ths_limit_up", ths.fetch_zt),
            ])

        dt_sources = [("akshare_em", fetch_dt or cls._default_dt)]
        if fetch_dt is None:
            dt_sources.append(("eastmoney_push2ex", eastmoney.fetch_dt))

        zt_crosscheck = ("eastmoney_push2ex", eastmoney.fetch_zt) if fetch_zt is None else None
        dt_crosscheck = ("eastmoney_push2ex", eastmoney.fetch_dt) if fetch_dt is None else None
        zt_sources.extend(list(zt_fallbacks or []))
        dt_sources.extend(list(dt_fallbacks or []))
        return zt_sources, dt_sources, zt_crosscheck, dt_crosscheck

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
        zt_frame, zt_source, zt_state, zt_errors = self._fetch_pool(
            self.zt_sources, date, "ZT"
        )
        dt_frame, dt_source, dt_state, dt_errors = self._fetch_pool(
            self.dt_sources, date, "DT"
        )
        parts = [frame for frame in (zt_frame, dt_frame) if not frame.empty]
        data = (pd.concat(parts, ignore_index=True)
                if parts else pd.DataFrame(columns=LIMIT_POOL_COLUMNS))
        actual = len(data)
        errors = zt_errors + dt_errors
        source = f"ZT:{zt_source}|DT:{dt_source}"
        reconciliation_partial = False
        reconciliation_messages = []
        for pool_type, frame, selected_source, checker in (
            ("ZT", zt_frame, zt_source, self.zt_crosscheck),
            ("DT", dt_frame, dt_source, self.dt_crosscheck),
        ):
            if frame.empty or checker is None or selected_source == checker[0]:
                continue
            check_name, check_fetcher = checker
            source += f"|CHECK:{pool_type}:{check_name}"
            try:
                check_raw = check_fetcher(date)
                check_frame = self._normalize(check_raw, date, pool_type, check_name)
                reconciliation = reconcile_limit_pool(frame, check_frame)
                if reconciliation.status == "partial":
                    reconciliation_partial = True
                    reconciliation_messages.append(
                        f"{pool_type}/{check_name}: {reconciliation.message}"
                    )
                elif reconciliation.status == "unavailable":
                    reconciliation_messages.append(
                        f"{pool_type}/{check_name}: {reconciliation.message}"
                    )
            except Exception as exc:
                reconciliation_messages.append(
                    f"{pool_type}/{check_name}: crosscheck unavailable: {exc}"
                )
        usable = {"success", "zero"}
        both_usable = zt_state in usable and dt_state in usable
        either_usable = zt_state in usable or dt_state in usable
        message = "; ".join(errors + reconciliation_messages)

        if reconciliation_partial:
            result = FetchResult.partial(
                dataset="limit_pool", date=date, source=source,
                expected_count=actual, actual_count=actual,
                scope="SH,SZ,BJ", message=message, data=data,
            )
        elif both_usable:
            if data.empty:
                result = FetchResult.zero(
                    dataset="limit_pool", date=date, source=source,
                    scope="SH,SZ,BJ", message=message, data=data,
                )
            else:
                result = FetchResult.success(
                    dataset="limit_pool", date=date, source=source,
                    expected_count=actual, actual_count=actual,
                    scope="SH,SZ,BJ", started_at=started,
                    finished_at=self.now(), message=message, data=data,
                )
        elif either_usable:
            result = FetchResult.partial(
                dataset="limit_pool", date=date, source=source,
                expected_count=actual, actual_count=actual,
                scope="SH,SZ,BJ", message=message, data=data,
            )
        else:
            result = FetchResult.failed(
                dataset="limit_pool", date=date, source=source,
                message=message or "all limit pool sources failed",
                scope="SH,SZ,BJ", data=data,
            )
        if self.status_store is not None:
            self.status_store.record(result)
        return result

    def fetch_history(self, dates: Iterable[str]) -> dict[str, FetchResult]:
        """Fetch every requested day through the same ZT/DT orchestration as the latest day."""
        results = {}
        for date in dates:
            canonical = str(date).replace("/", "-")
            if len(canonical) == 8 and canonical.isdigit():
                canonical = f"{canonical[:4]}-{canonical[4:6]}-{canonical[6:8]}"
            results[canonical] = self.fetch_day(canonical)
        return results

    def _fetch_pool(self, sources: list[SourceFetcher], date: str, pool_type: str):
        messages = []
        had_failure = False
        empty_source = None
        for source_name, fetcher in sources:
            try:
                raw = fetcher(date)
                if raw is None:
                    raise ValueError("empty response object")
                frame = self._normalize(raw, date, pool_type, source_name)
                messages.extend(frame.attrs.get("warnings", []))
                if not frame.empty:
                    return frame, source_name, "success", messages
                empty_source = source_name
            except Exception as exc:
                had_failure = True
                messages.append(f"{pool_type}/{source_name}: {exc}")
        if empty_source is not None and not had_failure:
            return pd.DataFrame(columns=LIMIT_POOL_COLUMNS), empty_source, "zero", messages
        if empty_source is not None:
            return pd.DataFrame(columns=LIMIT_POOL_COLUMNS), empty_source, "partial", messages
        return pd.DataFrame(columns=LIMIT_POOL_COLUMNS), "unavailable", "failed", messages

    @staticmethod
    def _normalize(frame, date: str, pool_type: str, source_name: str) -> pd.DataFrame:
        if isinstance(frame, list):
            frame = pd.DataFrame(frame)
        if not isinstance(frame, pd.DataFrame):
            raise ValueError("pool response must be a DataFrame or list")
        upstream_discarded = int(frame.attrs.get("discarded_rows", 0) or 0)
        if frame.empty:
            return pd.DataFrame(columns=LIMIT_POOL_COLUMNS)
        code_col = next((c for c in ("代码", "证券代码", "code") if c in frame.columns), None)
        if code_col is None:
            raise ValueError("missing code column")
        name_col = next((c for c in ("名称", "证券简称", "name") if c in frame.columns), None)
        count_col = next((c for c in ("连板数", "limit_count") if c in frame.columns), None)
        normalized = []
        discarded = upstream_discarded
        for index, value in frame[code_col].items():
            try:
                code = normalize_code(value)
            except ValueError:
                discarded += 1
                continue
            name = str(frame.loc[index, name_col]) if name_col else ""
            count = frame.loc[index, count_col] if count_col else 1
            try:
                count = max(1, int(float(count)))
            except (TypeError, ValueError):
                count = 1
            normalized.append({
                "date": date,
                "pool_type": pool_type,
                "code": code,
                "name": name,
                "limit_count": count,
                "source": source_name,
            })
        if not normalized:
            raise ValueError("pool contains no valid stock codes")
        result = pd.DataFrame(normalized, columns=LIMIT_POOL_COLUMNS)
        if discarded:
            noun = "row" if discarded == 1 else "rows"
            result.attrs["warnings"] = [
                f"{pool_type}/{source_name}: discarded {discarded} invalid stock {noun}"
            ]
        return result
