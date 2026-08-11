import os
from datetime import datetime, time as dt_time, timedelta

import pandas as pd

_cached_latest_date = None
_cached_latest_date_key = None
_MARKET_DATA_READY_TIME = dt_time(15, 30)


def _parse_date(value):
    """Parse YYYYMMDD / YYYY-MM-DD-like values into a date, or return None."""
    text = str(value or "").strip().replace("-", "")[:8]
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


def get_report_cutoff(*, now=None, report_date=None):
    """Return the latest calendar date that is safe to publish.

    REPORT_DATE is an explicit as-of override used by CI/backfills. Without an
    override, today's data is considered complete only after 15:30 local time;
    before that the cutoff is the previous calendar day. The caller's available
    trading dates then naturally skip weekends and exchange holidays.
    """
    explicit = report_date if report_date is not None else os.environ.get("REPORT_DATE")
    parsed = _parse_date(explicit)
    if explicit and parsed is None:
        raise ValueError(f"Invalid REPORT_DATE: {explicit!r}; expected YYYY-MM-DD or YYYYMMDD")
    if parsed is not None:
        return parsed

    current = now if now is not None else datetime.now().astimezone()
    cutoff = current.date()
    if current.time().replace(tzinfo=None) < _MARKET_DATA_READY_TIME:
        cutoff -= timedelta(days=1)
    return cutoff


def select_latest_completed_date(date_values, *, now=None, report_date=None):
    """Select the latest available trading date not newer than the report cutoff."""
    cutoff = get_report_cutoff(now=now, report_date=report_date)
    eligible = [parsed for parsed in (_parse_date(value) for value in date_values) if parsed and parsed <= cutoff]
    selected = max(eligible) if eligible else cutoff
    return datetime.combine(selected, dt_time.min)
def filter_completed_rows(frame, date_col, *, now=None, report_date=None):
    """Return cache rows whose date is valid and not newer than the report cutoff."""
    if frame is None or frame.empty or date_col not in frame.columns:
        return frame.copy() if frame is not None else pd.DataFrame()

    cutoff_text = get_report_cutoff(now=now, report_date=report_date).strftime("%Y%m%d")
    normalized = (
        frame[date_col].astype(str).str.strip().str.replace("-", "", regex=False).str[:8]
    )
    valid = normalized.str.fullmatch(r"\d{8}", na=False) & (normalized <= cutoff_text)
    return frame.loc[valid].copy()


def _cache_file_stamp(path):
    """Return a cheap identity stamp for a cache file, or None when absent."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def get_latest_date():
    """Return the latest completed date in the local cache.

    Future/premarket rows are ignored. REPORT_DATE can pin a historical run so
    every module uses the same as-of boundary.

    ⚠️ 记忆化必须带上涨停缓存的文件指纹 (mtime_ns, size)。日报进程内的调用顺序是
    "步骤1 抓当日涨停池并写缓存" 之前就有 get_trading_dates -> get_cached_trading_dates
    -> 本函数; 旧实现的 memo key 只有 (REPORT_DATE, cutoff), 两者在同一次运行内恒定,
    于是当日新写入的交易日永远看不见 —— 2026-08-11 事故: 19:58:57 启动时缓存最新为
    08-10 并被 memo 钉死, 19:59:07 写入 08-11 后步骤2 仍读到 08-10, 当日数据被
    trade_dates_set 过滤掉, update_price_cache 拿到的最大日期是 08-10 直接早退,
    整份报告回退成前一天。
    """
    global _cached_latest_date, _cached_latest_date_key

    from paths import ZT_CACHE_FILE

    cutoff = get_report_cutoff()
    cache_key = (
        os.environ.get("REPORT_DATE", ""),
        cutoff.isoformat(),
        _cache_file_stamp(ZT_CACHE_FILE),
    )
    if _cached_latest_date is not None and _cached_latest_date_key == cache_key:
        return _cached_latest_date

    try:
        if os.path.exists(ZT_CACHE_FILE):
            df = pd.read_csv(ZT_CACHE_FILE, dtype=str, usecols=["日期"])
            if "日期" in df.columns and not df.empty:
                _cached_latest_date = select_latest_completed_date(df["日期"], report_date=cutoff.isoformat())
                _cached_latest_date_key = cache_key
                return _cached_latest_date
    except Exception as exc:
        print(f"Error reading cache date: {exc}")

    _cached_latest_date = datetime.combine(cutoff, dt_time.min)
    _cached_latest_date_key = cache_key
    return _cached_latest_date