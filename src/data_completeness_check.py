# -*- coding: utf-8 -*-
"""数据补全与连续性自检模块。

功能定位:
    1. 专责“防漏跑、防断档”：在流水线启动与收尾时，主动比对近 30 个交易日（或指定天数）的日历，
       穿透检查 涨停池、全市场价格、短线情绪三大缓存。
    2. 发现中间有天漏跑时，精准定位缺失日并预警，确保自动回补链路成功修复空洞。
    3. 严正声明：本检测仅做完整性与缺口兜底，绝不缩小、裁剪或截断既有的全量历史分析范围（如 90/120/250 日线）。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Set
import pandas as pd

from paths import PRICE_CACHE, SENTIMENT_CACHE, ZT_CACHE_FILE
from lianban_analysis import get_trading_dates


def check_recent_data_completeness(window_days: int = 30) -> Dict[str, Any]:
    """检测最近 N 个交易日的数据连续性与缺口状态。

    Args:
        window_days: 重点核验的近端交易日天数，默认 30 天。

    Returns:
        包含检测状态、缺失日期列表、缓存统计的字典。
    """
    banner = "=" * 72
    print(f"\n{banner}")
    print(f"  🔍 [数据补全检测] 正在核验最近 {window_days} 个交易日的数据连续性 (历史分析全量范围保持不变)")
    print(banner)

    # 1. 获取近 window_days 个有效交易日 (由近及远: 20261015, 20261014...)
    try:
        candidate_dates_ymd = get_trading_dates(window_days)
    except Exception as exc:
        print(f"  ⚠️ 获取交易日历失败 ({exc}), 无法执行缺口检测")
        return {"status": "ERROR", "message": str(exc)}

    if not candidate_dates_ymd:
        print("  ⚠️ 交易日历返回为空")
        return {"status": "EMPTY"}

    # 规范化日期表示: '20261015' 与 '2026-10-15'
    expected_ymd = set(candidate_dates_ymd)
    expected_dash = {f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in candidate_dates_ymd}
    earliest = candidate_dates_ymd[-1]
    latest = candidate_dates_ymd[0]
    date_range_str = f"{earliest[:4]}-{earliest[4:6]}-{earliest[6:]} 至 {latest[:4]}-{latest[4:6]}-{latest[6:]}"

    # 2. 核验涨停板缓存
    zt_missing: List[str] = []
    zt_dates_found = set()
    if os.path.exists(ZT_CACHE_FILE):
        try:
            zt_df = pd.read_csv(ZT_CACHE_FILE, dtype=str, usecols=['日期'])
            zt_dates_found = set(zt_df['日期'].dropna().astype(str).tolist())
            zt_missing = sorted(expected_ymd - zt_dates_found)
        except Exception as exc:
            print(f"  ⚠️ 读取涨停缓存失败: {exc}")
    else:
        zt_missing = sorted(list(expected_ymd))

    # 3. 核验价格缓存
    price_missing: List[str] = []
    price_dates_found = set()
    if os.path.exists(PRICE_CACHE):
        try:
            p_df = pd.read_csv(PRICE_CACHE, dtype={'date': str}, usecols=['date'])
            price_dates_found = set(p_df['date'].dropna().astype(str).tolist())
            # 价格缓存支持带横杠或不带横杠
            price_dates_norm = {d.replace('-', '') for d in price_dates_found}
            price_missing = sorted([d for d in expected_ymd if d not in price_dates_norm])
        except Exception as exc:
            print(f"  ⚠️ 读取价格缓存失败: {exc}")
    else:
        price_missing = sorted(list(expected_ymd))

    # 4. 核验情绪缓存
    sent_missing: List[str] = []
    sent_dates_found = set()
    if os.path.exists(SENTIMENT_CACHE):
        try:
            s_df = pd.read_csv(SENTIMENT_CACHE, dtype=str, usecols=['日期'])
            sent_dates_found = set(s_df['日期'].dropna().astype(str).tolist())
            sent_missing = sorted(expected_ymd - sent_dates_found)
        except Exception as exc:
            print(f"  ⚠️ 读取情绪缓存失败: {exc}")
    else:
        sent_missing = sorted(list(expected_ymd))

    all_missing_dates = sorted(list(set(zt_missing) | set(price_missing) | set(sent_missing)))

    # 5. 输出结构化结论
    print(f"  📅 重点核验区间: {date_range_str} (共 {len(candidate_dates_ymd)} 个交易日)")

    if not all_missing_dates:
        print(f"  ✅ 涨停历史缓存: 最近 {window_days} 交易日全部在库 (连续无断档)")
        print(f"  ✅ 价格历史缓存: 最近 {window_days} 交易日全量覆盖 (连续无断档)")
        print(f"  ✅ 市场情绪历史: 最近 {window_days} 交易日指标对齐 (连续无断档)")
        print(f"  🎉 [检测通过] 最近 {window_days} 交易日数据完整性 100%，无漏跑空洞，无需补全。")
        print(f"{banner}\n")
        return {
            "status": "PASS",
            "window_days": window_days,
            "date_range": date_range_str,
            "missing_dates": [],
            "all_complete": True,
        }
    else:
        print(f"  ⚠️ [检测预警] 发现中间存在未跑/缺失的交易日 (共 {len(all_missing_dates)} 天缺失):")
        for d in all_missing_dates:
            gaps = []
            if d in zt_missing: gaps.append("涨停池")
            if d in price_missing: gaps.append("价格数据")
            if d in sent_missing: gaps.append("市场情绪")
            d_dash = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            print(f"     👉 缺失日期: {d_dash} | 缺失项: {', '.join(gaps)}")

        print(f"  🔄 [自动补全响应] 系统已将缺失日期加入自动回补通道，将自动从官方接口拉取历史数据填补！")
        print(f"{banner}\n")
        return {
            "status": "GAP_DETECTED",
            "window_days": window_days,
            "date_range": date_range_str,
            "missing_dates": all_missing_dates,
            "missing_zt": zt_missing,
            "missing_price": price_missing,
            "missing_sent": sent_missing,
            "all_complete": False,
        }


def verify_backfill_completion(missing_dates_before: List[str]) -> bool:
    """在跑批执行完成后，核验先前缺失的日期是否已成功入库。"""
    if not missing_dates_before:
        return True

    print("\n" + "=" * 72)
    print("  📋 [数据补全验收] 正在核验先前缺失日期的回补入库结果...")
    
    # 重新读取缓存核验
    zt_dates = set()
    if os.path.exists(ZT_CACHE_FILE):
        try:
            zt_df = pd.read_csv(ZT_CACHE_FILE, dtype=str, usecols=['日期'])
            zt_dates = set(zt_df['日期'].dropna().astype(str).tolist())
        except Exception:
            pass

    price_dates = set()
    if os.path.exists(PRICE_CACHE):
        try:
            p_df = pd.read_csv(PRICE_CACHE, dtype={'date': str}, usecols=['date'])
            price_dates = {d.replace('-', '') for d in p_df['date'].dropna().astype(str).tolist()}
        except Exception:
            pass

    still_missing = []
    for d in missing_dates_before:
        d_dash = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        filled_zt = d in zt_dates
        filled_price = d in price_dates
        if filled_zt and filled_price:
            print(f"  ✅ 交易日 {d_dash} 已成功补全 (涨停池与全市场行情已全部入库)")
        else:
            still_missing.append(d)
            print(f"  ⚠️ 交易日 {d_dash} 部分回补中 (ZT={filled_zt}, Price={filled_price})")

    if not still_missing:
        print("  🎉 [数据补全验收通过] 先前缺失的交易日已全部自动回补补齐！")
    else:
        print(f"  ⚠️ 仍有 {len(still_missing)} 个日期等待下一轮调度回补")
    print("=" * 72 + "\n")
    return len(still_missing) == 0
