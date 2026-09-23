"""Small raw-OHLC companion to the existing close-only price cache.

Uses the project's Tencent HTTP client, keeps the returned day's actual OHLC,
and never substitutes adjusted prices or a neighboring date. Cache writes are
atomic and opt-in. No reports, orders or strategy approvals are created here.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import tempfile

from .http_session import get_session
from .models import normalize_code

SCHEMA = "raw-daily-bars/v1"
SHANGHAI = timezone(timedelta(hours=8))


def _number(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not a price")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("nonfinite raw OHLC")
    return value


def is_valid_raw_bar(row, day, code=None):
    if not isinstance(row, dict) or row.get("date") != day or (code is not None and row.get("code") != code):
        return False
    try:
        normalize_code(row.get("code"))
        values = [_number(row.get(k)) for k in ("open_raw", "high_raw", "low_raw", "close_raw")]
        o, h, l, c = values
        stamp = datetime.fromisoformat(str(row.get("source_timestamp")).replace("Z", "+00:00"))
        return (min(values) > 0 and l <= min(o,c) <= max(o,c) <= h
                and row.get("price_basis") == "raw" and isinstance(row.get("source"), str) and bool(row["source"].strip())
                and stamp.tzinfo is not None)
    except (ValueError, TypeError, OverflowError):
        return False


def parse_tencent_raw_bars(code, day, payload, *, captured_at):
    code, day = normalize_code(code), date.fromisoformat(day).isoformat()
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise ValueError("Tencent raw history business error")
    item = (payload.get("data") or {}).get(code)
    if not isinstance(item, dict) or not isinstance(item.get("day"), list):
        raise ValueError("requested symbol/raw day not supplied")
    found = None
    for values in item["day"]:
        if not isinstance(values, list) or len(values) < 5:
            raise ValueError("malformed raw OHLC row")
        if str(values[0]) != day:
            continue
        row = {"code": code, "date": day, "open_raw": _number(values[1]),
               "close_raw": _number(values[2]), "high_raw": _number(values[3]), "low_raw": _number(values[4]),
               "price_basis": "raw", "source": "tencent_raw", "source_timestamp": str(captured_at)}
        if len(values) > 5:
            volume = _number(values[5])
            if volume < 0: raise ValueError("negative source volume")
            row["volume_lots"] = volume
        if not is_valid_raw_bar(row, day, code):
            raise ValueError("invalid or untraceable raw OHLC")
        if found is not None and found != row:
            raise ValueError("conflicting duplicate raw date")
        found = row
    return [found] if found is not None else []


def parse_sina_raw_bars(code, day, frame, *, captured_at):
    code, day = normalize_code(code), date.fromisoformat(day).isoformat()
    values = frame.to_dict("records") if hasattr(frame, "to_dict") else frame
    if not isinstance(values, list): raise ValueError("Sina raw history is not records")
    found = None
    for item in values:
        raw_day = item.get("date") if isinstance(item, dict) else None
        source_day = raw_day.strftime("%Y-%m-%d") if hasattr(raw_day, "strftime") else str(raw_day)
        if source_day != day: continue
        row = {"code": code, "date": day, "price_basis": "raw", "source": "sina_raw", "source_timestamp": str(captured_at)}
        for key in ("open", "high", "low", "close"):
            row[key + "_raw"] = _number(item.get(key))
        if not is_valid_raw_bar(row, day, code): raise ValueError("invalid Sina raw OHLC")
        if found is not None and found != row: raise ValueError("conflicting Sina date")
        found = row
    return [found] if found is not None else []


def load_raw_bars(cache_dir, day):
    day = date.fromisoformat(day).isoformat()
    path = Path(cache_dir) / (day + ".json")
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA or data.get("trade_date") != day:
        raise ValueError("raw bar cache schema/date mismatch")
    rows = data.get("records")
    if not isinstance(rows, list) or not all(is_valid_raw_bar(row, day) for row in rows):
        raise ValueError("invalid raw bar cache rows")
    if len({row["code"] for row in rows}) != len(rows):
        raise ValueError("duplicate raw bar cache symbols")
    return rows


def _save(cache_dir, day, rows):
    directory = Path(cache_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (day + ".json")
    data = json.dumps({"schema_version": SCHEMA, "trade_date": day, "records": rows}, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == data:
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=directory, prefix="."+day+"-", suffix=".tmp", encoding="utf-8", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)


class RawBarProvider:
    def __init__(self, fetcher=None, *, fallback_fetcher=None, now=None, max_workers=4):
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.fetcher = fetcher or self._fetch
        self.fallback_fetcher = fallback_fetcher or self._sina_fetch
        self.max_workers = max(1, int(max_workers))

    def _fetch(self, code, day, captured_at):
        try:
            response = get_session().get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                params={"param": f"{code},day,{day},{day},5,"},
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}, timeout=12)
            response.raise_for_status()
            rows = parse_tencent_raw_bars(code, day, response.json(), captured_at=self.now().isoformat())
            if rows: return rows
        except Exception:
            pass
        return self.fallback_fetcher(code, day, self.now().isoformat())

    def _sina_fetch(self, code, day, captured_at):
        import akshare as ak
        frame = ak.stock_zh_a_daily(symbol=code, start_date=day.replace("-", ""),
                                    end_date=day.replace("-", ""), adjust="")
        return parse_sina_raw_bars(code, day, frame, captured_at=self.now().isoformat())

    def fetch_day(self, codes, day, *, cache_dir=None):
        day = date.fromisoformat(day).isoformat()
        codes = list(dict.fromkeys(normalize_code(code) for code in codes))
        now = self.now()
        if now.tzinfo is None: raise ValueError("capture time must have a timezone")
        local = now.astimezone(SHANGHAI)
        if date.fromisoformat(day) > local.date() or (day == local.date().isoformat() and (local.hour, local.minute) < (15, 0)):
            return {"status": "not_closed", "trade_date": day, "requested": len(codes), "covered": 0, "records": [], "errors": []}
        cached = load_raw_bars(cache_dir, day) if cache_dir is not None else []
        by_code = {row["code"]: row for row in cached}
        missing = [code for code in codes if code not in by_code]
        errors = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(self.fetcher, code, day, now.isoformat()): code for code in missing}
            for future in as_completed(futures):
                code = futures[future]
                try:
                    rows = future.result()
                    if len(rows) != 1 or not is_valid_raw_bar(rows[0], day, code):
                        raise ValueError("exact raw daily bar unavailable")
                    by_code[code] = dict(rows[0])
                except Exception as exc:
                    errors.append({"code": code, "error": type(exc).__name__, "reason": str(exc)[:240]})
        if cache_dir is not None and len(by_code) > len(cached):
            _save(cache_dir, day, [by_code[code] for code in sorted(by_code)])
        records = [by_code[code] for code in codes if code in by_code]
        return {"schema_version": SCHEMA, "status": "complete" if len(records) == len(codes) else "partial",
                "trade_date": day, "requested": len(codes), "covered": len(records), "records": records,
                "errors": sorted(errors, key=lambda row: row["code"])}
