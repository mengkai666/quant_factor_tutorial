"""Deterministic after-close research brief, independent of trading permission.

Inputs are observations, never execution approvals. This module performs no I/O.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date
import math
import re

SCHEMA = "research-brief/v1"
_MISC = {"", "其它", "其他", "未分类", "未归因", "未知", "unknown", "none", "-"}
_CODE = re.compile(r"(?:sh|sz|bj)\d{6}\Z")


def _day(value):
    text = "" if value is None else str(value).strip()
    if re.fullmatch(r"\d{8}", text):
        text = text[:4] + "-" + text[4:6] + "-" + text[6:]
    try:
        return date.fromisoformat(text).isoformat()
    except (ValueError, TypeError):
        return None


def _number(value, *, positive=False):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) and (value > 0 if positive else value >= 0) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _records(value):
    if hasattr(value, "to_dict"):
        value = value.to_dict("records")
    return [row for row in value or [] if isinstance(row, dict)]


def _code(row):
    value = str(row.get("code") or row.get("代码") or "").strip().lower()
    return value if _CODE.fullmatch(value) else None


def _date_values(row):
    # Nullable dataframe columns are absent observations, not contradictory dates.
    missing = {"", "none", "nan", "nat", "<na>"}
    return [row[k] for k in ("trade_date", "date", "report_date", "日期")
            if k in row and str(row[k]).strip().lower() not in missing]


def _row_day(row):
    dates = [_day(value) for value in _date_values(row)]
    return dates[0] if dates and dates[0] is not None and all(d == dates[0] for d in dates) else None


def _height(row):
    for key in ("height", "limit_count", "连板数", "level"):
        value = row.get(key)
        if isinstance(value, str) and re.fullmatch(r"\d+(?:连板|板)?", value):
            value = re.match(r"\d+", value).group()
        number = _number(value, positive=True)
        if number is not None and number.is_integer():
            return int(number)
    return None


def _sector(row):
    return str(row.get("primary_sector") or row.get("大主线") or row.get("mainline") or row.get("sector") or "其它").strip()


def _normalize(row, day):
    code = _code(row)
    if not code or _row_day(row) != day:
        raise ValueError("权威涨停池存在日期或股票代码错误")
    for value in _date_values(row):
        if _day(value) != day:
            raise ValueError("权威股票行的日期字段相互冲突")
    if str(row.get("pool_type") or row.get("类型") or "ZT").upper() not in {"ZT", "涨停"}:
        raise ValueError("权威涨停池包含非涨停成员")
    name = str(row.get("name") or row.get("名称") or "").strip()
    if not name or name.lower() == code or name == code[-6:]:
        raise ValueError("权威涨停池缺少真实股票名称")
    first = str(row.get("first_limit_time") or "").replace(":", "")
    if not re.fullmatch(r"\d{6}", first):
        first = ""
    return {"code": code, "name": name, "sector": _sector(row),
            "sub_sector": str(row.get("细分板块") or row.get("sub_sector") or ""),
            "sector_observed": any(row.get(k) for k in ("primary_sector", "大主线", "mainline", "sector")),
            "current_height": _height(row), "source_date": day,
            "amount": _number(row.get("amount", row.get("成交额"))),
            "turnover_rate": _number(row.get("turnover_rate", row.get("换手率"))),
            "first_limit_time": first,
            "is_st": bool(re.search(r"ST|退", name, re.I))}


def _price_index(rows):
    result, conflicts = {}, set()
    for row in _records(rows):
        key = (_code(row), _row_day(row))
        if None in key:
            continue
        values = {"close_raw": _number(row.get("close_raw"), positive=True),
                  "close_qfq": _number(row.get("close_qfq"), positive=True)}
        if str(row.get("trade_status") or "").lower() in {"suspended", "not_listed", "delisted"}:
            values["close_raw"] = None
        previous = result.get(key)
        if previous is not None and any(previous[k] is not None and values[k] is not None
                and not math.isclose(previous[k], values[k], abs_tol=.00001, rel_tol=0) for k in values):
            conflicts.add(key)
        elif previous is None:
            result[key] = values
        else:
            result[key] = {k: previous[k] if previous[k] is not None else values[k] for k in values}
    for key in conflicts:
        result.pop(key, None)
    return result, conflicts


def _bars(rows, day):
    from data_sources.raw_bar_provider import is_valid_raw_bar
    result, conflicts = {}, set()
    for row in _records(rows):
        if _row_day(row) != day or not is_valid_raw_bar(row, day):
            continue
        code = _code(row)
        if not code:
            continue
        if code in result and any(float(result[code][k]) != float(row[k])
                                 for k in ("open_raw", "high_raw", "low_raw", "close_raw")):
            conflicts.add(code)
        else:
            result[code] = dict(row)
    for code in conflicts:
        result.pop(code, None)
    return result, conflicts


def _quote(row, day, index, price_conflicts, bars, bar_conflicts, baseline):
    code = row["code"]
    if (code, day) in price_conflicts or code in bar_conflicts:
        return None
    values = index.get((code, day), {})
    close = values.get("close_raw")
    bar = bars.get(code)
    if bar and close is not None and not math.isclose(float(bar["close_raw"]), close, abs_tol=.005, rel_tol=0):
        return None
    if close is None and bar:
        close = float(bar["close_raw"])
    if close is None:
        return None
    qfq = values.get("close_qfq")
    old = index.get((code, baseline), {}).get("close_qfq") if baseline else None
    result = {**row, "close": close,
              "pct_5d": round((qfq / old - 1) * 100, 4) if qfq and old else None,
              "open": float(bar["open_raw"]) if bar else None,
              "high": float(bar["high_raw"]) if bar else None,
              "low": float(bar["low_raw"]) if bar else None,
              "board_type": None}
    if bar:
        result["board_type"] = "one_word" if all(math.isclose(float(bar[k]), close, abs_tol=1e-8, rel_tol=0)
                for k in ("open_raw", "high_raw", "low_raw")) else "turnover"
    return result


def _stock_sort(row):
    return (-(row.get("current_height") or 0), -(row.get("amount") if row.get("amount") is not None else -1),
            row.get("first_limit_time") or "999999", row["code"])


def _notes(row):
    height = row.get("current_height")
    reasons = [f"当前{height}连板，处于板块高度前列" if height and height > 1 else "当日涨停，跟踪板块扩散"]
    if row.get("amount") is not None:
        reasons.append(f"成交额{row['amount']/100_000_000:.2f}亿元")
    if row.get("pct_5d") is not None:
        reasons.append(f"近5日涨幅{row['pct_5d']:+.2f}%")
    if row.get("board_type") == "one_word":
        risk = "当日一字板，封板强度不等于次日可成交；先看开板后的承接。"
    elif height and height >= 4:
        risk = f"已到{height}连板，高位分歧和断板波动需优先考虑。"
    else:
        risk = "单日涨停不等于持续走强，观察板块与个股能否同步延续。"
    anchor = row.get("low") or row["close"]
    watch = f"以本日收盘{row['close']:.2f}元为强弱参照，结合板块是否继续扩散观察。"
    cancel = f"若跌破本日{'低点' if row.get('low') is not None else '收盘参考'}{anchor:.2f}元后不能收回，降低观察优先级。"
    return {**row, "reason": "；".join(reasons), "risk": risk, "watch": watch, "cancel": cancel}


def validate_research_snapshot(snapshot, *, report_date=None):
    """Validate an authoritative daily pool without reading caches or permissions."""
    if not isinstance(snapshot, dict):
        raise ValueError("权威市场快照必须为对象")
    day = _day(report_date if report_date is not None else snapshot.get("report_date"))
    if not day or _day(snapshot.get("report_date")) != day or snapshot.get("date_verified") is False:
        raise ValueError("报告日与权威市场快照日期不一致")
    raw = snapshot.get("limit_pool_rows")
    if not isinstance(raw, list) or any(not isinstance(row, dict) for row in raw):
        raise ValueError("权威涨停池必须是完整的记录列表")
    count = _number(snapshot.get("limit_up"))
    if count is None or not count.is_integer() or count != len(raw):
        raise ValueError("权威涨停数量与成员清单不一致")
    observed = []
    for item in raw:
        row = deepcopy(item)
        has_date = bool(_date_values(row))
        if not has_date:
            if snapshot.get("date_verified") is not True:
                raise ValueError("未核验的快照不能为股票行提供日期")
            row["trade_date"] = day
            row["trade_date_source"] = "verified_snapshot_envelope"
        normalized = _normalize(row, day)
        row["code"] = normalized["code"]
        observed.append(row)
    if len({row["code"] for row in observed}) != len(observed):
        raise ValueError("权威涨停池存在重复股票代码")
    return observed


def build_research_brief(context, *, price_rows=None, history_rows=None, trading_days=None,
                         history_days=None, raw_bars=None, max_sectors=5, stocks_per_sector=3,
                         recent_window=5, recent_limit=20):
    """Build a research-only, date-pinned multi-sector brief from authoritative facts."""
    if not isinstance(context, dict):
        raise ValueError("报告上下文必须为对象")
    day = _day(context.get("report_date"))
    snapshot = (context.get("facts") or {}).get("market_snapshot") or context.get("market_snapshot") or {}
    current = [_normalize(row, day) for row in validate_research_snapshot(snapshot, report_date=day)]
    for value in (max_sectors, stocks_per_sector, recent_window, recent_limit):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("数量与回看窗口必须为正整数")
    calendar = sorted({_day(d) for d in trading_days or [] if _day(d) and _day(d) <= day})
    if trading_days is not None and day not in calendar:
        raise ValueError("报告日期不在已提供交易日历内")
    window = calendar[-recent_window:] if calendar else [day]
    baseline = calendar[-6] if len(calendar) >= 6 else None
    coverage = {d for d in (_day(d) for d in history_days or []) if d in window} | {day}
    index, price_conflicts = _price_index(price_rows)
    bars, bar_conflicts = _bars(raw_bars, day)
    quotes = {}
    for row in current:
        q = _quote(row, day, index, price_conflicts, bars, bar_conflicts, baseline)
        if q:
            quotes[row["code"]] = q
    if current and not quotes:
        raise ValueError("没有可核验的同日个股价格，保留上一份有效报告")
    grouped = defaultdict(list)
    for row in current:
        if row["sector"].lower() not in _MISC:
            grouped[row["sector"]].append(row)
    historical = {}
    for row in _records(history_rows):
        observed_day = _row_day(row)
        if observed_day not in window or observed_day == day:
            continue
        try:
            normalized = _normalize(row, observed_day)
        except ValueError:
            continue
        historical[(normalized["code"], observed_day)] = normalized
    historical.update({(row["code"], day): row for row in current})
    sector_rows = []
    for name, members in grouped.items():
        selected = [quotes[r["code"]] for r in members if r["code"] in quotes and not r["is_st"]]
        if not selected:
            continue
        stocks = [_notes(r) for r in sorted(selected, key=_stock_sort)[:stocks_per_sector]]
        previous = window[-2] if len(window) > 1 else None
        previous_rows = [r for (code, d), r in historical.items() if d == previous]
        classified_previous = previous in coverage and all(r["sector_observed"] for r in previous_rows)
        previous_count = sum(r["sector"] == name for r in previous_rows) if classified_previous else None
        sector_rows.append({"name": name, "limit_up_count": len(members),
            "streak_count": sum((r["current_height"] or 0) >= 2 for r in members),
            "max_height": max((r["current_height"] or 0 for r in members), default=0),
            "share_pct": round(len(members) / len(current) * 100, 2) if current else 0,
            "previous_limit_up_count": previous_count,
            "change": len(members) - previous_count if previous_count is not None else None,
            "stocks": stocks})
    sectors = sorted(sector_rows, key=lambda s: (-s["limit_up_count"], -s["streak_count"], -s["max_height"], s["name"]))[:max_sectors]
    for rank, sector in enumerate(sectors, 1):
        sector["rank"] = rank
    by_code = defaultdict(dict)
    for (code, d), row in historical.items():
        by_code[code][d] = row
    recent = []
    for code, events in by_code.items():
        peak = max((r["current_height"] or 0 for r in events.values()), default=0)
        if peak < 2 and len(events) < 2:
            continue
        last_day = max(events)
        row = dict(events[last_day])
        current_height = events.get(day, {}).get("current_height")
        if day not in events:
            row.update(source_date=day, current_height=None, amount=None, turnover_rate=None, first_limit_time="")
        q = _quote(row, day, index, price_conflicts, bars, bar_conflicts, baseline)
        if not q:
            continue
        state = f"当前{current_height}连板" if current_height and current_height >= 2 else "今日涨停" if day in events else "今日未涨停"
        trajectory = [{"date": d, "limit_up": True if d in events else False if d in coverage else None,
                       "height": events[d]["current_height"] if d in events else None} for d in window]
        recent.append({**q, "current_height": current_height, "peak_height": peak,
            "limit_up_count": len(events), "last_limit_date": last_day, "state": state, "trajectory": trajectory})
    recent.sort(key=lambda r: (-(r["current_height"] or 0), -r["peak_height"], -r["limit_up_count"], -int(r["last_limit_date"].replace("-", "")), r["code"]))
    total_recent = len(recent)
    breadth = _number(snapshot.get("breadth_ratio"))
    breadth = breadth if breadth is not None and breadth <= 1 else None
    down = _number(snapshot.get("limit_down"))
    down = down if down is not None and down.is_integer() else None
    height = max((r["current_height"] or 0 for r in current), default=0)
    narrative = [f"涨停{len(current)}只" + (f"、跌停{int(down)}只" if down is not None else "") + f"，最高连板{height}板。"]
    comparison = None
    previous_day = calendar[-2] if len(calendar) >= 2 else None
    previous_snapshot = (context.get("facts") or {}).get("previous_market_snapshot")
    if previous_day and isinstance(previous_snapshot, dict) and _day(previous_snapshot.get("report_date")) == previous_day:
        try:
            previous_rows = validate_research_snapshot(previous_snapshot, report_date=previous_day)
        except ValueError:
            pass
        else:
            change = len(current) - len(previous_rows)
            comparison = {"report_date":previous_day, "limit_up":len(previous_rows), "limit_up_change":change}
            direction = f"增加{change}只" if change > 0 else f"减少{-change}只" if change < 0 else "持平"
            narrative.append(f"较前一交易日（{previous_day}）涨停家数{direction}；先看活跃度变化，再判断是否扩散到更多板块。")
    if breadth is not None:
        tone = "上涨覆盖占优" if breadth >= .65 else "下跌覆盖占优" if breadth <= .35 else "涨跌分化，优先比较板块而非把局部强势当作普涨"
        narrative.append(f"上涨占比{breadth*100:.1f}%，{tone}。")
    if sectors:
        narrative.append(f"{sectors[0]['name']}聚集{sectors[0]['limit_up_count']}只涨停，占本日涨停样本{sectors[0]['share_pct']:.1f}%；这是题材聚集度，不是资金净流入占比。")
    target = _day(context.get("target_trade_date"))
    target = target if target and target > day else None
    next_items = [{"title": s["name"], "detail": f"先对照{ '、'.join(r['name'] for r in s['stocks']) }的价格承接，再看板块涨停扩散是否超过本日{s['limit_up_count']}只的基准；若核心股承接转弱且跟随减少，降低该方向的观察优先级。"} for s in sectors]
    return {"schema_version": SCHEMA, "purpose": "research_observation", "report_date": day,
        "target_trade_date": target, "title": "多板块盘后观察",
        "market": {"limit_up": len(current), "limit_down": int(down) if down is not None else None,
                   "max_height": height, "breadth_ratio": breadth, "comparison":comparison, "narrative": narrative},
        "sectors": sectors,
        "recent": {"window_days": window, "coverage_days": sorted(coverage),
                   "complete": len(window) == recent_window and all(d in coverage for d in window),
                   "total_count": total_recent, "stocks": recent[:recent_limit], "more_stocks": recent[recent_limit:]},
        "next_session": next_items,
        "provenance": {"population_scope": "closing_limit_pool", "authoritative_count": len(current),
            "priced_count": len(quotes), "source": snapshot.get("source") or snapshot.get("snapshot_source") or "authoritative_snapshot",
            "ranking_basis": "板块：涨停数→连板数→高度；个股：连板高度→成交额→首封时间。仅作观察排序，不推断胜率或收益。"}}
