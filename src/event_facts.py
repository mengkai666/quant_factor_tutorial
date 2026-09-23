"""Auditable interpretation of retained limit-event observations.

The observation/archive layer never guesses states. This explicit consumer layer
can prove an attempted/failed/reclosed state from same-day pool semantics and a
supplied break count; board shape needs real raw OHLC. Original values/evidence
are retained. A closing-pool subset never becomes a full-market denominator.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import math
import re

from limit_events import event_field_coverage

VERSION = "limit-event-facts/v1"
STATE_FIELDS = ("limit_up_attempted", "broken", "reclosed", "board_type")


def _day(value):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (ValueError, TypeError):
        return None


def _time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo and parsed.utcoffset() is not None else None
    except (ValueError, TypeError):
        return None


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _known(value):
    return isinstance(value, str) and value.strip().lower() not in {"", "unknown", "none", "null", "n/a"}


def _state_value(field, value):
    if field == "board_type":
        if not isinstance(value, str): return None
        value = value.strip().lower()
        if value in {"one_word", "one-word", "一字", "一字板", "一字涨停"}: return "one_word"
        if value in {"turnover", "换手", "换手板", "换手涨停", "t_board", "t字板"}: return "turnover"
        return None
    if isinstance(value, bool): return value
    if isinstance(value, (int, float)) and value in (0, 1): return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "是", "成功", "回封"}: return True
    if text in {"false", "0", "no", "n", "否", "失败", "未回封"}: return False
    return None


def _rows(value):
    if hasattr(value, "to_dict"):
        value = value.to_dict("records")
    return [row for row in value or [] if isinstance(row, dict)]


def _board_evidence(bar, code, day):
    if not isinstance(bar, dict) or bar.get("code") != code or _day(bar.get("date")) != day:
        return None
    if bar.get("price_basis") not in {None, "raw", "close_raw"}:
        return None
    if not _known(bar.get("source")) or _time(bar.get("source_timestamp")) is None:
        return None
    prices = [_number(bar.get(key)) for key in ("open_raw", "high_raw", "low_raw", "close_raw")]
    if any(value is None or value <= 0 for value in prices):
        return None
    opening, high, low, close = prices
    if not low <= min(opening, close) <= max(opening, close) <= high:
        return None
    kind = "one_word" if all(math.isclose(value, close, rel_tol=0, abs_tol=1e-8) for value in prices) else "turnover"
    return kind, {key: deepcopy(bar[key]) for key in (
        "code", "date", "open_raw", "high_raw", "low_raw", "close_raw", "source", "source_timestamp", "price_basis"
    ) if key in bar}


def resolve_limit_event_facts(snapshot: dict | None, *, price_rows=None) -> dict:
    """Return a resolved copy, keeping raw values and the exact derivation inputs.

    ZT denotes observed upper-limit membership; ZB denotes an observed failed
    upper-limit attempt, never a down-limit pool. Known explicit states take precedence over derivation; raw nulls remain
    preserved while independent numeric evidence can resolve their meaning.
    Idempotent inputs retain their raw basis.
    """
    result = deepcopy(snapshot) if isinstance(snapshot, dict) else {}
    day = _day(result.get("trade_date"))
    prices = {row.get("code"): row for row in _rows(price_rows) if _day(row.get("date")) == day}
    records = []
    for incoming in _rows(result.get("records")):
        row = deepcopy(incoming)
        previous_evidence = row.get("derived_event_evidence") if row.get("event_fact_version") == VERSION else {}
        previous_evidence = previous_evidence if isinstance(previous_evidence, dict) else {}
        raw = row.get("raw_event_fields") if row.get("event_fact_version") == VERSION else None
        raw = deepcopy(raw) if isinstance(raw, dict) else {key: deepcopy(row.get(key)) for key in STATE_FIELDS}
        for key in STATE_FIELDS:
            row[key] = _state_value(key, raw.get(key))
        row["raw_event_fields"] = raw
        row["event_fact_version"] = VERSION
        row["derived_event_evidence"] = {}
        row["event_fact_conflicts"] = []
        observed = row.get("event_evidence") if isinstance(row.get("event_evidence"), dict) else {}
        pool = str(row.get("pool_type") or "").upper()
        code = str(row.get("code") or "")
        trusted = (day is not None and _day(row.get("trade_date")) == day
                   and re.fullmatch(r"(?:sh|sz|bj)[0-9]{6}", code) is not None
                   and _known(row.get("source")) and _time(row.get("source_timestamp")) is not None)
        if not trusted or pool not in {"ZT", "ZB"}:
            records.append(row)
            continue
        basis = {"pool_type": pool, "trade_date": day, "source": row["source"],
                 "source_timestamp": row["source_timestamp"]}
        count = _number(row.get("break_count"))
        count = int(count) if count is not None and count >= 0 and count.is_integer() else None
        candidates = {"limit_up_attempted": (True, "up_limit_pool_membership", basis)}
        if count is not None:
            candidates["broken"] = (count > 0, "explicit_break_count", {**basis, "break_count": count})
            candidates["reclosed"] = (count > 0 if pool == "ZT" else False,
                "current_pool_and_break_count", {**basis, "break_count": count})
            if pool == "ZB" and count == 0:
                row["event_fact_conflicts"].append("break_count")
        if pool == "ZT":
            old_board = previous_evidence.get("board_type") or {}
            old_inputs = old_board.get("inputs") if isinstance(old_board, dict) else {}
            fallback = old_inputs.get("bar") if isinstance(old_inputs, dict) else None
            board = _board_evidence(prices.get(code, fallback), code, day)
            if board:
                candidates["board_type"] = (board[0], "same_day_raw_ohlc", {**basis, "bar": board[1]})
        for key, (value, rule, inputs) in candidates.items():
            explicit = row.get(key) is not None
            if explicit:
                if row.get(key) is not None and row[key] != value:
                    row["event_fact_conflicts"].append(key)
                continue
            row[key] = value
            row["derived_event_evidence"][key] = {"rule": rule, "version": VERSION, "inputs": deepcopy(inputs)}
        records.append(row)
    result["records"] = records
    result["raw_field_coverage"] = result.get("raw_field_coverage") or event_field_coverage([
        {**row, **row.get("raw_event_fields", {})} for row in records
    ])
    result["field_coverage"] = event_field_coverage(records)
    result["fact_resolution"] = {"version": VERSION,
        "derived_counts": {key: sum(key in row["derived_event_evidence"] for row in records) for key in STATE_FIELDS},
        "conflicting_records": sum(bool(row.get("event_fact_conflicts")) for row in records)}
    result.setdefault("full_market_coverage", False)
    return result
