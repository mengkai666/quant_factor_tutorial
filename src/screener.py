"""Role-aware focus-pool construction for the next-session report."""
from __future__ import annotations

import re

import pandas as pd


def _height(value) -> int:
    text = str(value or "")
    if "首板" in text:
        return 1
    match = re.search(r"(\d+)", text)
    return int(match.group(1)) if match else 0


def _risky_name(name: str) -> bool:
    upper = str(name or "").strip().upper()
    return not upper or "ST" in upper or "退" in upper


def _core_mainline(ml_strength) -> str:
    if ml_strength is None or getattr(ml_strength, "empty", True):
        return ""
    try:
        values = ml_strength.iloc[-1].to_dict()
        return max(values, key=values.get) if values else ""
    except (KeyError, IndexError, TypeError, ValueError):
        return ""


def generate_focus_pool(ml_strength, echelon, top30_data, sentiment_df,
                        output_path="focus_pool.csv"):
    """Build mutually exclusive space, low-level and trend roles."""
    pool = []
    selected_codes = set()
    core_ml = _core_mainline(ml_strength)

    for item in echelon or []:
        height_label = str(item.get("height", ""))
        height = _height(height_label)
        details = item.get("stock_details") or []
        primary = str(item.get("primary", "") or "")
        is_core = bool(core_ml and core_ml in primary)
        if not details:
            names = item.get("stocks") or []
            details = [{"name": name, "code": ""} for name in names]

        if height >= 3:
            role = "空间龙头"
            strategy = "【空间博弈池】"
            limit = 2
        elif height in (1, 2) and (height == 2 or is_core):
            role = "低位补涨"
            strategy = "【低位补涨池】"
            limit = 2
        else:
            continue

        accepted = 0
        for detail in details:
            name = str(detail.get("name", "")).strip()
            code = str(detail.get("code", "")).strip()
            if not code or code in selected_codes or _risky_name(name):
                continue
            selected_codes.add(code)
            accepted += 1
            pool.append({
                "股票": name,
                "代码": code,
                "板块": re.sub(r"\d+%$", "", primary).strip() or core_ml,
                "角色": role,
                "策略池": strategy,
                "入场条件": (
                    f"昨日{height_label}。仅在竞价与板块强度同时确认后参与，"
                    "缩量加速和孤立封板不追。"
                ),
                "防守位": "开板后失去板块承接或跌破分时承接位退出",
            })
            if accepted >= limit:
                break

    trend_added = 0
    for period, records in (top30_data or {}).items():
        if trend_added >= 2:
            break
        for record in records or []:
            code = str(record.get("code", "")).strip()
            name = str(record.get("name", "")).strip()
            if not code or code in selected_codes or _risky_name(name):
                continue
            selected_codes.add(code)
            trend_added += 1
            mainline = str(
                record.get("mainline") or record.get("sub_sector")
                or record.get("industry") or core_ml or ""
            ).strip()
            pool.append({
                "股票": name,
                "代码": code,
                "板块": mainline,
                "角色": "趋势中军",
                "策略池": "【核心中军低吸池】",
                "入场条件": (
                    f"近期{period}趋势居前。仅在主线强度未转弱、回踩缩量且承接确认时低吸。"
                ),
                "防守位": "有效跌破20日均线或主线转弱退出",
            })
            if trend_added >= 2:
                break

    frame = pd.DataFrame(pool)
    if not frame.empty:
        frame = frame.drop_duplicates(subset=["代码"], keep="first").head(10).reset_index(drop=True)
        if output_path:
            frame.to_csv(output_path, index=False, encoding="utf-8-sig")
            print(f"  ✅ [量化引擎] 成功生成明日核心股票池: {output_path} (共 {len(frame)} 只标的)")
    else:
        print("  [量化引擎] 今日未筛选出符合条件的个股，股票池为空。")
    return frame
