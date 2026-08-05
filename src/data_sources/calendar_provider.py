from __future__ import annotations

from datetime import datetime

import pandas as pd


class CalendarProvider:
    """Trading calendar with an injectable source for offline tests."""

    def __init__(self, source=None, close_hour: int = 16):
        self.source = source or self._akshare_source
        self.close_hour = close_hour

    @staticmethod
    def _akshare_source():
        import akshare as ak
        return ak.tool_trade_date_hist_sina()

    def _dates(self) -> list[str]:
        frame = self.source()
        if frame is None or frame.empty or "trade_date" not in frame.columns:
            raise ValueError("trading calendar source returned no trade_date column")
        dates = pd.to_datetime(frame["trade_date"], errors="coerce").dropna()
        return sorted(dates.dt.strftime("%Y-%m-%d").unique().tolist())

    def trading_days(self, start: str, end: str) -> list[str]:
        return [day for day in self._dates() if start <= day <= end]

    def latest_closed_day(self, now: datetime | None = None) -> str:
        now = now or datetime.now()
        today = now.strftime("%Y-%m-%d")
        upper = today if now.hour >= self.close_hour else "0000-00-00"
        eligible = []
        for day in self._dates():
            if day < today or (day == today and day <= upper):
                eligible.append(day)
        if not eligible:
            raise ValueError("trading calendar has no closed trading day")
        return eligible[-1]
