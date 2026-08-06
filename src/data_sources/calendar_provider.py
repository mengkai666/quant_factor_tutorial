from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import tempfile

import pandas as pd

from .models import FetchResult, FetchStatus


class CalendarProvider:
    """Trading calendar with cache-backed degradation and auditable status."""

    def __init__(self, source=None, close_hour: int = 16, cache_path=None,
                 status_store=None, now=None):
        self.source = source or self._akshare_source
        self.close_hour = close_hour
        self.cache_path = Path(cache_path) if cache_path else None
        self.status_store = status_store
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.last_result = None

    @staticmethod
    def _akshare_source():
        import akshare as ak
        return ak.tool_trade_date_hist_sina()

    def _read_cache(self) -> list[str]:
        if self.cache_path is None or not self.cache_path.exists():
            return []
        frame = pd.read_csv(self.cache_path, dtype=str)
        column = "trade_date" if "trade_date" in frame.columns else "date"
        if column not in frame.columns:
            return []
        dates = pd.to_datetime(frame[column], errors="coerce").dropna()
        return sorted(dates.dt.strftime("%Y-%m-%d").unique().tolist())

    def _write_cache(self, dates: list[str]) -> None:
        if self.cache_path is None or not dates:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.cache_path.name}.", suffix=".tmp",
            dir=self.cache_path.parent,
        )
        os.close(fd)
        try:
            pd.DataFrame({"trade_date": dates}).to_csv(temp_name, index=False)
            os.replace(temp_name, self.cache_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _record(self, dates: list[str], status: FetchStatus, message: str = "") -> None:
        now = self.now()
        date = dates[-1] if dates else now.strftime("%Y-%m-%d")
        self.last_result = FetchResult(
            dataset="calendar", date=date, source="akshare_calendar",
            status=status, expected_count=len(dates), actual_count=len(dates),
            scope="SH,SZ,BJ", message=message, started_at=now,
            finished_at=self.now(),
        )
        if self.status_store is not None:
            self.status_store.record(self.last_result)

    def _dates(self) -> list[str]:
        try:
            frame = self.source()
            if frame is None or frame.empty or "trade_date" not in frame.columns:
                raise ValueError("trading calendar source returned no trade_date column")
            dates = pd.to_datetime(frame["trade_date"], errors="coerce").dropna()
            normalized = sorted(dates.dt.strftime("%Y-%m-%d").unique().tolist())
            if not normalized:
                raise ValueError("trading calendar source returned no valid dates")
            self._write_cache(normalized)
            self._record(normalized, FetchStatus.SUCCESS)
            return normalized
        except Exception as exc:
            cached = self._read_cache()
            if cached:
                self._record(cached, FetchStatus.STALE, f"calendar source failed: {exc}; reused local snapshot")
                return cached
            self._record([], FetchStatus.FAILED, f"calendar source failed: {exc}")
            raise

    def trading_days(self, start: str, end: str) -> list[str]:
        return [day for day in self._dates() if start <= day <= end]

    def latest_closed_day(self, now: datetime | None = None) -> str:
        now = now or self.now()
        today = now.strftime("%Y-%m-%d")
        eligible = [day for day in self._dates() if day < today or (day == today and now.hour >= self.close_hour)]
        if not eligible:
            raise ValueError("trading calendar has no closed trading day")
        return eligible[-1]
