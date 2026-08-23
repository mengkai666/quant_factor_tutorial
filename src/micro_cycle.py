from __future__ import annotations

from typing import Any

import pandas as pd

from data_sources.models import normalize_code
from data_sources.name_resolver import NameResolution


MAINLINE_INDUSTRIES = {
    "AI算力": {"电子化学品", "元件", "半导体", "其他电子", "通信设备", "消费电子", "光学光电子"},
    "AI应用": {"软件开发", "IT服务", "互联网服务", "游戏", "出版", "广告营销", "影视院线"},
    "医药": {"医疗服务", "生物制品", "化学制药", "中药", "医药商业", "医疗器械"},
    "周期资源": {"贵金属", "小金属", "能源金属", "金属新材料", "工业金属", "化学原料", "化学制品", "煤炭开采"},
    "机器人": {"自动化设备", "通用设备", "专用设备", "电机"},
    "新能源电网": {"电网设备", "电池", "光伏设备", "风电设备", "电力"},
    "军工航天": {"航空装备", "航天装备", "军工电子", "船舶制造"},
}
LEVEL_ORDER = {"核心共振": 0, "次级共振": 1, "连板跟随": 2}
INVALID_MAINLINES = {"", "其它", "其他", "nan", "None"}


def _column(frame, *candidates):
    return next((column for column in candidates if column in frame.columns), None)


def _iso_date(value):
    text = str(value).strip()
    return f"{text[:4]}-{text[4:6]}-{text[6:]}" if len(text) == 8 and text.isdigit() else text


def _after(rows, date):
    return [row for row in rows if str(row.get("date", "")) > date]


def _event(date="", **values):
    return {"date": date, **values}


def detect_micro_cycle(det: dict, *, daily_limit_counts=None) -> dict:
    rows = sorted((det or {}).get("index_series") or [], key=lambda row: row["date"])
    bottom_date = str(((det or {}).get("bottom") or {}).get("date") or "")
    start = next((i for i, row in enumerate(rows) if row["date"] == bottom_date), -1)
    empty = {
        "status": "探底未完成", "events": {}, "signal_date": "",
        "confirmation_date": "", "full_confirmation_date": "",
        "signal_return": None, "rising_days": 0, "signal_basis": "unavailable",
    }
    if start < 0 or len(rows) - start < 8:
        return empty

    stop_slice = rows[start:start + 3]
    stop = min(stop_slice, key=lambda row: float(row["low"]))
    stop_i = rows.index(stop)
    rebound_slice = rows[stop_i + 1:stop_i + 6]
    if len(rebound_slice) < 3:
        return empty
    high_peak = max(rebound_slice, key=lambda row: float(row["high"]))
    close_peak = max(rebound_slice, key=lambda row: float(row["close"]))
    close_peak_i = rows.index(close_peak)

    after_rebound = rows[close_peak_i + 1:]
    confirmation = next(
        (row for row in after_rebound if float(row["close"]) > float(close_peak["close"])),
        None,
    )
    search_end = rows.index(confirmation) if confirmation else len(rows)
    pullback_rows = rows[close_peak_i + 1:search_end]
    if not pullback_rows:
        return empty
    secondary = min(pullback_rows, key=lambda row: float(row["low"]))
    higher_low = float(secondary["low"]) > float(stop["low"])

    secondary_i = rows.index(secondary)
    retest_rows = rows[secondary_i + 1:search_end]
    retest = min(retest_rows, key=lambda row: float(row["close"])) if retest_rows else secondary
    retest_i = rows.index(retest)
    candidates = rows[retest_i + 1:search_end + (1 if confirmation else 0)]
    price_signal = next((
        row for row in candidates
        if float(row["close"]) > float(rows[rows.index(row) - 1]["close"])
        and float(row["low"]) >= float(retest["low"])
    ), None)

    signal = price_signal
    basis = "price_only" if signal else "unavailable"
    counts = daily_limit_counts or {}
    if signal and counts:
        previous = rows[rows.index(signal) - 1]["date"]
        if signal["date"] in counts and previous in counts:
            if counts[signal["date"]] > counts[previous]:
                basis = "price+limit_pool"
            else:
                signal = None
                basis = "unavailable"

    full = next((
        row for row in after_rebound
        if float(row["high"]) > float(high_peak["high"])
        and float(row["close"]) > float(close_peak["close"])
    ), None)
    if not higher_low:
        signal = None
        confirmation = None
        full = None
        basis = "unavailable"
    signal_i = rows.index(signal) if signal else -1
    signal_rows = rows[signal_i:] if signal_i >= 0 else []
    rising_days = 1 if signal else 0
    for previous, current in zip(signal_rows, signal_rows[1:]):
        if float(current["close"]) > float(previous["close"]):
            rising_days += 1
        else:
            break
    status = "探底未完成" if not higher_low else "震荡筑底"
    if confirmation:
        status = "小周期主升" if signal and rising_days >= 4 else "震荡转升"
    signal_return = (
        round((float(rows[-1]["close"]) / float(signal["close"]) - 1) * 100, 2)
        if signal else None
    )
    return {
        "status": status,
        "events": {
            "final_stop": _event(stop["date"], low=float(stop["low"])),
            "rebound_high": {
                "high_date": high_peak["date"], "high": float(high_peak["high"]),
                "close_date": close_peak["date"], "close": float(close_peak["close"]),
            },
            "secondary_bottom": _event(
                secondary["date"], low=float(secondary["low"]), higher_low=higher_low,
            ),
            "retest": _event(retest["date"], close=float(retest["close"])),
        },
        "signal_date": signal["date"] if signal else "",
        "confirmation_date": confirmation["date"] if confirmation else "",
        "full_confirmation_date": full["date"] if full else "",
        "signal_return": signal_return,
        "rising_days": rising_days if signal else 0,
        "signal_basis": basis,
    }


def build_signal_limit_chain(
    limit_history: pd.DataFrame,
    trading_dates: list[str],
    signal_date: str,
    latest_date: str,
    *,
    names: NameResolution,
    immutable_dates: set[str] | None = None,
) -> dict:
    empty = {"usable": False, "status": "unavailable", "dates": [],
             "consecutive_days": 0, "rows": [], "hint": ""}
    if limit_history is None or limit_history.empty:
        return empty
    date_col = _column(limit_history, "date", "日期")
    code_col = _column(limit_history, "code", "代码")
    type_col = _column(limit_history, "type", "类型")
    if not date_col or not code_col:
        return empty
    signal_date = _iso_date(signal_date)
    latest_date = _iso_date(latest_date)
    ordered_dates = sorted({_iso_date(date) for date in trading_dates})
    dates = [date for date in ordered_dates if signal_date <= date <= latest_date]
    if len(dates) < 3:
        return empty

    frame = limit_history.copy()
    frame["_date"] = frame[date_col].astype(str).str.replace("-", "", regex=False)
    if type_col:
        frame = frame[frame[type_col].astype(str).str.upper().eq("ZT")]
    frame["_code"] = frame[code_col].map(normalize_code)
    sets = {
        date: set(frame.loc[frame["_date"].eq(date.replace("-", "")), "_code"])
        for date in dates
    }
    if any(not sets[date] for date in dates):
        return empty
    chain = set.intersection(*(sets[date] for date in dates))
    before = [date for date in ordered_dates if date < signal_date]
    previous = before[-1] if before else ""
    previous_set = set(
        frame.loc[frame["_date"].eq(previous.replace("-", "")), "_code"]
    ) if previous else set()
    starters = sorted(chain - previous_set)
    immutable = {_iso_date(date) for date in (immutable_dates or set())}
    verified = all(date in immutable for date in dates)
    return {
        "usable": bool(starters),
        "status": "verified" if verified else "provisional",
        "dates": dates,
        "consecutive_days": len(dates),
        "rows": [
            {"code": code, "name": names.names.get(code, code)}
            for code in starters
        ],
        "hint": "" if verified else "按历史事实交集计算，逐日不可变快照待补强",
    }


def _segment_return(records: list[dict[str, Any]], start: str, end: str) -> float | None:
    first = [row for row in records if str(row.get("date", "")) <= start]
    last = [row for row in records if str(row.get("date", "")) <= end]
    if not first or not last or not first[-1].get("close"):
        return None
    return round((float(last[-1]["close"]) / float(first[-1]["close"]) - 1) * 100, 2)


def build_sector_return_table(
    cache: dict, signal_date: str, latest_date: str, index_return: float | None,
) -> pd.DataFrame:
    rows = []
    for name, records in (cache or {}).items():
        value = _segment_return(records, signal_date, latest_date)
        if value is not None:
            rows.append({
                "name": str(name),
                "return": value,
                "excess_return": round(value - float(index_return), 2)
                if index_return is not None else None,
            })
    result = pd.DataFrame(rows, columns=["name", "return", "excess_return"])
    if result.empty:
        return result
    return result.sort_values(["return", "name"], ascending=[False, True]).reset_index(drop=True)


def _safe_code(value: Any) -> str:
    try:
        return normalize_code(value)
    except ValueError:
        return ""


def _attribution_map(frame: pd.DataFrame | None, latest_date: str) -> dict[str, str]:
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return {}
    required = {"date", "code", "mainline"}
    if not required.issubset(frame.columns):
        return {}
    work = frame.copy()
    work["_date"] = work["date"].astype(str).str.replace("-", "", regex=False)
    work["_code"] = work["code"].map(_safe_code)
    work["_mainline"] = work["mainline"].astype(str).str.strip()
    work = work[
        work["_date"].le(latest_date.replace("-", ""))
        & work["_code"].ne("")
        & ~work["_mainline"].isin(INVALID_MAINLINES)
    ]
    if work.empty:
        return {}
    latest = work.sort_values("_date").drop_duplicates("_code", keep="last")
    return dict(zip(latest["_code"], latest["_mainline"]))


def _endpoint_returns(
    price_matrix: pd.DataFrame | None,
    codes: list[str],
    signal_date: str,
    latest_date: str,
) -> dict[str, float]:
    if (
        price_matrix is None
        or not isinstance(price_matrix, pd.DataFrame)
        or signal_date not in price_matrix.index
        or latest_date not in price_matrix.index
    ):
        return {}
    result = {}
    for code in codes:
        if code not in price_matrix.columns:
            continue
        start = pd.to_numeric(price_matrix.at[signal_date, code], errors="coerce")
        end = pd.to_numeric(price_matrix.at[latest_date, code], errors="coerce")
        if pd.isna(start) or pd.isna(end) or float(start) == 0:
            continue
        result[code] = (float(end) / float(start) - 1) * 100
    return result


def build_cycle_resonance(
    sector_returns: pd.DataFrame,
    chain: dict,
    price_matrix: pd.DataFrame,
    signal_date: str,
    latest_date: str,
    *,
    cls_attribution: pd.DataFrame | None = None,
    em_attribution: pd.DataFrame | None = None,
) -> dict:
    chain_rows = list((chain or {}).get("rows") or []) if (chain or {}).get("usable") else []
    chain_total = len(chain_rows)
    empty = {
        "strong_industries": [],
        "mainlines": [],
        "attribution_coverage": 0.0,
        "leader_coverage": 0.0,
        "unattributed_count": chain_total,
    }

    strong = pd.DataFrame()
    if isinstance(sector_returns, pd.DataFrame) and not sector_returns.empty:
        strong = sector_returns.copy()
        strong["return"] = pd.to_numeric(strong["return"], errors="coerce")
        strong["excess_return"] = pd.to_numeric(strong["excess_return"], errors="coerce")
        strong = strong[strong["excess_return"].ge(2.0)].dropna(subset=["name", "return"])
        strong = strong.sort_values(["return", "name"], ascending=[False, True])
    empty["strong_industries"] = [
        {"name": str(row["name"]), "return": round(float(row["return"]), 2)}
        for _, row in strong.head(5).iterrows()
    ]
    if not chain_rows:
        return empty

    cls_map = _attribution_map(cls_attribution, latest_date)
    em_map = _attribution_map(em_attribution, latest_date)
    attributed = []
    for row in chain_rows:
        code = _safe_code(row.get("code"))
        mainline = cls_map.get(code) or em_map.get(code)
        if code and mainline:
            attributed.append({**row, "code": code, "mainline": mainline})

    returns = _endpoint_returns(
        price_matrix, [row["code"] for row in chain_rows], signal_date, latest_date,
    )
    leader_coverage = len(returns) / chain_total
    evidence_names = set(strong["name"].astype(str)) if not strong.empty else set()
    evidence_order = list(strong["name"].astype(str)) if not strong.empty else []
    mainlines = []
    groups = pd.DataFrame(attributed).groupby("mainline", sort=False) if attributed else []
    for mainline, rows in groups:
        row_list = rows.to_dict("records")
        industries = MAINLINE_INDUSTRIES.get(str(mainline), set()) & evidence_names
        industry_evidence = [name for name in evidence_order if name in industries]
        count = len(row_list)
        if count >= 2 and len(industry_evidence) >= 2:
            level = "核心共振"
        elif count >= 1 and industry_evidence:
            level = "次级共振"
        elif count >= 2:
            level = "连板跟随"
        else:
            continue
        leaders = sorted(
            row_list,
            key=lambda row: (-returns.get(row["code"], float("-inf")), row["code"]),
        )[:4]
        mainlines.append({
            "name": str(mainline),
            "level": level,
            "chain_count": count,
            "chain_total": chain_total,
            "industry_evidence": industry_evidence,
            "leaders": [
                {
                    "code": row["code"],
                    "name": str(row.get("name") or row["code"]),
                    "return": round(returns[row["code"]], 2)
                    if leader_coverage >= 0.80 and row["code"] in returns else None,
                }
                for row in leaders
            ],
        })
    mainlines.sort(key=lambda row: (
        LEVEL_ORDER[row["level"]], -row["chain_count"], row["name"],
    ))
    return {
        "strong_industries": empty["strong_industries"],
        "mainlines": mainlines,
        "attribution_coverage": round(len(attributed) / chain_total, 4),
        "leader_coverage": round(leader_coverage, 4),
        "unattributed_count": chain_total - len(attributed),
    }


def _exact_attribution_records(
    frame: pd.DataFrame | None,
    report_date: str,
) -> dict[str, dict[str, str]]:
    """Return attribution rows locked to one report date."""
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return {}
    if not {"date", "code"}.issubset(frame.columns):
        return {}
    work = frame.copy()
    work["_date"] = work["date"].astype(str).str.replace("-", "", regex=False)
    work = work[work["_date"].eq(report_date.replace("-", ""))]
    if work.empty:
        return {}
    work["_code"] = work["code"].map(_safe_code)
    work = work[work["_code"].ne("")]
    result = {}
    # ⚠️ 列 tolist() 取代 iterrows() (每行一个 Series, 实测 2700 行 0.23s);
    #    逐元素表达式与旧实现逐字相同 (含 NaN → "nan" 这种既有行为)。
    unique = work.drop_duplicates("_code", keep="last")
    blank = [""] * len(unique)
    subs = unique["sub"].tolist() if "sub" in unique.columns else blank
    mainlines = unique["mainline"].tolist() if "mainline" in unique.columns else blank
    for code, sub_value, ml_value in zip(unique["_code"].tolist(), subs, mainlines):
        sub = str(sub_value or "").strip()
        mainline = str(ml_value or "").strip()
        result[code] = {
            "sub": "" if sub in INVALID_MAINLINES else sub,
            "mainline": "" if mainline in INVALID_MAINLINES else mainline,
        }
    return result


def _normalized_matrix_returns(
    price_matrix: pd.DataFrame | None,
    codes: list[str],
    start_date: str,
    end_date: str,
) -> dict[str, float]:
    if price_matrix is None or not isinstance(price_matrix, pd.DataFrame) or price_matrix.empty:
        return {}
    lookup = {
        str(index).replace("-", ""): index
        for index in price_matrix.index
    }
    start_key = lookup.get(start_date.replace("-", ""))
    end_key = lookup.get(end_date.replace("-", ""))
    if start_key is None or end_key is None:
        return {}
    result = {}
    for code in codes:
        if code not in price_matrix.columns:
            continue
        start = pd.to_numeric(price_matrix.at[start_key, code], errors="coerce")
        end = pd.to_numeric(price_matrix.at[end_key, code], errors="coerce")
        if pd.isna(start) or pd.isna(end) or float(start) == 0:
            continue
        result[code] = round((float(end) / float(start) - 1) * 100, 2)
    return result


def _market_leaders(rows: list[dict], returns: dict[str, float], *, limit: int = 3) -> list[dict]:
    ordered = sorted(
        rows,
        key=lambda row: (
            -int(row.get("height") or 0),
            -returns.get(row["code"], float("-inf")),
            row["code"],
        ),
    )[:limit]
    return [
        {
            "code": row["code"],
            "name": row["name"],
            "return": returns.get(row["code"]),
        }
        for row in ordered
    ]


def build_market_resonance(
    limit_history: pd.DataFrame,
    raw_price_matrix: pd.DataFrame,
    qfq_price_matrix: pd.DataFrame,
    sector_returns: pd.DataFrame,
    signal_date: str,
    latest_date: str,
    *,
    previous_date: str,
    names: NameResolution,
    cls_attribution: pd.DataFrame | None = None,
    em_attribution: pd.DataFrame | None = None,
    continuous_core: dict | None = None,
) -> dict:
    """Build report-day and cycle resonance from the full immutable limit-up pool.

    The strict daily limit-up intersection is intentionally kept only in
    ``continuous_core``; it never narrows the market-wide sector samples.
    """
    empty = {
        "daily_sectors": [], "mainlines": [], "cycle_sectors": [],
        "continuous_core": [], "attribution_coverage": 0.0,
        "daily_return_coverage": 0.0, "cycle_return_coverage": 0.0,
        "hint": "",
    }
    if limit_history is None or not isinstance(limit_history, pd.DataFrame) or limit_history.empty:
        return empty
    date_col = _column(limit_history, "date", "日期")
    code_col = _column(limit_history, "code", "代码")
    type_col = _column(limit_history, "type", "类型")
    name_col = _column(limit_history, "name", "名称")
    height_col = _column(limit_history, "height", "连板数", "streak")
    if not date_col or not code_col:
        return empty

    frame = limit_history.copy()
    frame["_date"] = frame[date_col].astype(str).str.replace("-", "", regex=False)
    frame = frame[frame["_date"].eq(latest_date.replace("-", ""))]
    if type_col:
        frame = frame[frame[type_col].astype(str).str.upper().eq("ZT")]
    frame["_code"] = frame[code_col].map(_safe_code)
    frame = frame[frame["_code"].ne("")]
    if frame.empty:
        return empty

    cls_map = _exact_attribution_records(cls_attribution, latest_date)
    em_map = _exact_attribution_records(em_attribution, latest_date)
    attribution = {**em_map, **cls_map}
    pool = []
    for _, row in frame.drop_duplicates("_code", keep="last").iterrows():
        code = row["_code"]
        raw_height = pd.to_numeric(row.get(height_col), errors="coerce") if height_col else 1
        height = 1 if pd.isna(raw_height) else max(1, int(float(raw_height)))
        cached_name = str(row.get(name_col) or "").strip() if name_col else ""
        attr = attribution.get(code) or {"sub": "", "mainline": ""}
        pool.append({
            "code": code,
            "name": names.names.get(code) or cached_name or code,
            "height": height,
            "sub": attr.get("sub", ""),
            "mainline": attr.get("mainline", ""),
        })

    codes = [row["code"] for row in pool]
    daily_returns = _normalized_matrix_returns(
        raw_price_matrix, codes, previous_date, latest_date,
    )
    qfq_returns = _normalized_matrix_returns(
        qfq_price_matrix, codes, signal_date, latest_date,
    )
    raw_cycle_returns = _normalized_matrix_returns(
        raw_price_matrix, codes, signal_date, latest_date,
    )
    cycle_returns = dict(qfq_returns)
    raw_fallback_codes = set()
    for code, value in raw_cycle_returns.items():
        if code not in cycle_returns:
            cycle_returns[code] = value
            raw_fallback_codes.add(code)

    daily_sectors = []
    attributed_subs = [row for row in pool if row["sub"]]
    if attributed_subs:
        for sub, rows in pd.DataFrame(attributed_subs).groupby("sub", sort=False):
            row_list = rows.to_dict("records")
            values = [daily_returns[row["code"]] for row in row_list if row["code"] in daily_returns]
            daily_sectors.append({
                "name": str(sub),
                "limit_count": len(row_list),
                "max_height": max(row["height"] for row in row_list),
                "return": round(sum(values) / len(values), 2) if values else None,
                "leaders": _market_leaders(row_list, daily_returns),
            })
    daily_sectors.sort(key=lambda row: (
        -row["limit_count"], -row["max_height"],
        -(row["return"] if row["return"] is not None else float("-inf")), row["name"],
    ))

    sector_frame = pd.DataFrame()
    if isinstance(sector_returns, pd.DataFrame) and not sector_returns.empty:
        sector_frame = sector_returns.copy()
        sector_frame["return"] = pd.to_numeric(sector_frame.get("return"), errors="coerce")
        sector_frame["excess_return"] = pd.to_numeric(
            sector_frame.get("excess_return"), errors="coerce",
        )
        sector_frame = sector_frame.dropna(subset=["name", "return"])

    mainlines = []
    attributed_mainlines = [row for row in pool if row["mainline"]]
    if attributed_mainlines:
        for mainline, rows in pd.DataFrame(attributed_mainlines).groupby("mainline", sort=False):
            row_list = rows.to_dict("records")
            related = MAINLINE_INDUSTRIES.get(str(mainline), set())
            evidence = sector_frame[sector_frame["name"].astype(str).isin(related)] if not sector_frame.empty else pd.DataFrame()
            sector_return = float(evidence["return"].max()) if not evidence.empty else None
            mainlines.append({
                "name": str(mainline),
                "limit_count": len(row_list),
                "max_height": max(row["height"] for row in row_list),
                "sector_return": round(sector_return, 2) if sector_return is not None else None,
                "leaders": _market_leaders(row_list, cycle_returns),
            })
    mainlines.sort(key=lambda row: (
        -row["limit_count"], -row["max_height"],
        -(row["sector_return"] if row["sector_return"] is not None else float("-inf")),
        row["name"],
    ))

    cycle_sectors = []
    if not sector_frame.empty and attributed_mainlines:
        strong = sector_frame[sector_frame["excess_return"].ge(2.0)].sort_values(
            ["return", "name"], ascending=[False, True],
        )
        for _, sector in strong.iterrows():
            sector_name = str(sector["name"])
            matching_mainlines = {
                mainline for mainline, industries in MAINLINE_INDUSTRIES.items()
                if sector_name in industries
            }
            candidates = [
                row for row in attributed_mainlines
                if row["sub"] == sector_name or row["mainline"] in matching_mainlines
            ]
            if not candidates:
                continue
            mainline_counts = pd.Series([row["mainline"] for row in candidates]).value_counts()
            cycle_sectors.append({
                "name": sector_name,
                "mainline": str(mainline_counts.index[0]) if not mainline_counts.empty else "",
                "limit_count": len(candidates),
                "max_height": max(row["height"] for row in candidates),
                "return": round(float(sector["return"]), 2),
                "leaders": _market_leaders(candidates, cycle_returns),
            })
            if len(cycle_sectors) >= 5:
                break

    core_rows = []
    if (continuous_core or {}).get("usable"):
        for row in (continuous_core or {}).get("rows") or []:
            code = _safe_code(row.get("code"))
            if not code:
                continue
            core_rows.append({
                "code": code,
                "name": str(row.get("name") or names.names.get(code) or code),
                "return": cycle_returns.get(code),
            })
        core_rows.sort(key=lambda row: (
            -cycle_returns.get(row["code"], float("-inf")), row["code"],
        ))

    hints = []
    attributed_count = sum(bool(row["sub"] or row["mainline"]) for row in pool)
    if attributed_count < len(pool):
        hints.append("部分涨停股暂未归因")
    if len(daily_returns) < len(pool) or len(cycle_returns) < len(pool):
        hints.append("部分个股收益暂缺")
    if raw_fallback_codes:
        hints.append("少量区间收益使用原始价")
    return {
        "daily_sectors": daily_sectors[:5],
        "mainlines": mainlines[:5],
        "cycle_sectors": cycle_sectors,
        "continuous_core": core_rows[:8],
        "attribution_coverage": round(attributed_count / len(pool), 4),
        "daily_return_coverage": round(len(daily_returns) / len(pool), 4),
        "cycle_return_coverage": round(len(cycle_returns) / len(pool), 4),
        "hint": " · ".join(hints),
    }
