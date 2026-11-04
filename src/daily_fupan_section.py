"""今日深度复盘与尾盘实战研判模块 (Evidence-Based Dynamic Clean Edition)
极简专业风：无冗余动画、无花哨渐变，高信息密度、清晰对齐、一目了然。
动态数据驱动：基于真实行情数据自动渲染，杜绝写死日期、虚构股票与固定数值。
"""
from __future__ import annotations

import json
import math
import os
from typing import Any
import pandas as pd

from paths import (PRICE_CACHE, PRICE_SLICE_DIR, CALENDAR_CACHE, ZT_CACHE_FILE,
                   SENTIMENT_CACHE, SECURITY_MASTER_CACHE, DAILY_SNAPSHOT_DIR)
from data_sources.models import normalize_code
from html import escape as _e


def _normalize_date(report_date: str | None, context: dict | None = None) -> tuple[str, str]:
    """统一规范化交易日字符串为 ISO (YYYY-MM-DD) 与 8位数字格式."""
    raw = report_date or (context or {}).get('report_date')
    if not raw:
        # 默认寻找 snapshot 目录中最新交易日
        snap_dir = os.path.join(os.path.dirname(__file__), '..', 'data', 'report_daily_snapshots')
        if os.path.exists(snap_dir):
            files = sorted([f[:-5] for f in os.listdir(snap_dir) if f.endswith('.json')])
            raw = files[-1] if files else '2026-10-20'
        else:
            raw = '2026-10-20'

    s = str(raw).replace('/', '-').strip()
    if len(s) == 8 and s.isdigit():
        day_iso = f"{s[:4]}-{s[4:6]}-{s[6:]}"
    else:
        day_iso = s[:10]
    day_digits = day_iso.replace('-', '')
    return day_iso, day_digits


def _load_market_overview(
    report_date: str,
    context: dict | None = None,
    advance_decline: dict | None = None,
) -> dict[str, Any]:
    """提取指数真实量价、涨跌家数与全市场体检指标."""
    day_iso, day_digits = _normalize_date(report_date, context)

    # 1. 缺少同日行情时保留空值，不用示例行情代替。
    def load_index(symbol):
        try:
            from phase_resonance import fetch_index
            records = {r['date']: r for r in fetch_index(symbol)}
            dates = sorted(records)
            if day_iso not in records or dates.index(day_iso) == 0:
                return None
            cur = records[day_iso]
            prev = records[dates[dates.index(day_iso) - 1]]
            values = {key: float(cur[key]) for key in ('open', 'close', 'high', 'low')}
            previous = float(prev['close'])
            if not math.isfinite(previous) or previous <= 0 or not all(math.isfinite(value) and value > 0 for value in values.values()):
                return None
            if values['low'] > min(values['open'], values['close']) or values['high'] < max(values['open'], values['close']):
                return None
            return {**values, 'prev_close': previous,
                    'chg': (values['close'] / previous - 1) * 100,
                    'open_chg': (values['open'] / previous - 1) * 100}
        except Exception as exc:
            print(f"  [复盘报告] {symbol}同日指数行情待核验: {exc}")
            return None

    sh_data = load_index('sh000001')
    cyb_data = load_index('sz399006')

    # 2. 涨跌停与全市场广度
    ad = dict(advance_decline or (context or {}).get('advance_decline') or {})
    zt_count = ad.get('zt')
    dt_count = ad.get('dt')
    up_count = ad.get('up')
    down_count = ad.get('down')
    breadth_ratio = ad.get('breadth_ratio')

    # 回退查询日度快照
    snap_path = os.path.join(DAILY_SNAPSHOT_DIR, f'{day_iso}.json')
    if any(value is None for value in (zt_count, dt_count, up_count, down_count)) and os.path.exists(snap_path):
        try:
            with open(snap_path, encoding='utf-8') as f:
                snap = json.load(f)
            zt_count = zt_count if zt_count is not None else snap.get('limit_up')
            dt_count = dt_count if dt_count is not None else snap.get('limit_down')
            breadth_ratio = breadth_ratio if breadth_ratio is not None else snap.get('breadth_ratio')
        except Exception:
            pass

    # 回退查询情绪历史缓存
    if any(value is None for value in (zt_count, dt_count, up_count, down_count)) and os.path.exists(SENTIMENT_CACHE):
        try:
            sdf = pd.read_csv(SENTIMENT_CACHE)
            sdf['day_str'] = sdf['日期'].astype(str).str.replace(r'\.0$', '', regex=True)
            sub = sdf[sdf['day_str'] == day_digits]
            if not sub.empty:
                row = sub.iloc[0]
                up_count = up_count if up_count is not None else (int(row['up']) if pd.notna(row.get('up')) else None)
                down_count = down_count if down_count is not None else (int(row['down']) if pd.notna(row.get('down')) else None)
                zt_count = zt_count if zt_count is not None else (int(row['zt']) if pd.notna(row.get('zt')) else None)
                dt_count = dt_count if dt_count is not None else (int(row['dt']) if pd.notna(row.get('dt')) else None)
        except Exception:
            pass

    # 回退查询涨停表
    if zt_count is None and os.path.exists(ZT_CACHE_FILE):
        try:
            zt_df = pd.read_csv(ZT_CACHE_FILE, dtype=str, encoding='utf-8-sig')
            same_day = zt_df[zt_df['日期'] == day_digits]
            if not same_day.empty:
                zt_count = len(same_day)
        except Exception:
            pass

    return {
        'day': day_iso,
        'day_digits': day_digits,
        'sh': sh_data,
        'cyb': cyb_data,
        'zt': _market_count(zt_count),
        'dt': _market_count(dt_count),
        'up': _market_count(up_count),
        'down': _market_count(down_count),
        'breadth_ratio': breadth_ratio,
    }


def _market_count(value):
    try:
        number = float(value)
        return int(number) if pd.notna(number) and number >= 0 and number.is_integer() else None
    except (TypeError, ValueError, OverflowError):
        return None


def _market_commentary(market):
    """Describe the supplied session; a single snapshot cannot prove improvement."""
    counts = {key: _market_count(market.get(key)) for key in ('up', 'down', 'zt', 'dt')}
    shown = {key: str(value) if value is not None else '待核验' for key, value in counts.items()}
    up, down, zt, dt = (counts[key] for key in ('up', 'down', 'zt', 'dt'))
    ratio = up / (up + down) if up is not None and down is not None and up + down else None
    if ratio is None:
        try:
            supplied = float(market.get('breadth_ratio'))
            ratio = supplied if 0 <= supplied <= 1 else None
        except (TypeError, ValueError):
            pass
    if ratio is None:
        breadth = '上涨占比待核验'
        regime = '市场广度待核验'
    else:
        regime = '涨多跌少' if ratio >= .60 else ('涨跌分化' if ratio >= .45 else '跌多涨少')
        breadth = f'上涨占比 {ratio:.1%} · {regime}'
    if zt is None or dt is None:
        limits = '涨跌停家数待核验，暂不判断强弱'
    else:
        gap = zt - dt
        limits = f'涨停比跌停多 {gap} 家' if gap > 0 else (f'跌停比涨停多 {-gap} 家' if gap < 0 else '涨停与跌停家数相同')
    weak = ratio is not None and ratio < .45 or zt is not None and dt is not None and dt > zt
    if ratio is None or zt is None or dt is None:
        plan = '市场数据尚未齐备，先补齐同日事实；条件核验前不新增仓位。'
    elif weak:
        plan = '先观察市场广度与跌停反馈是否改善；未确认修复前不新增仓位，个股仅作条件跟踪。'
    else:
        plan = '继续核验市场广度、主线联动与个股承接；单日上涨不代表修复已持续，条件未确认不新增仓位。'
    return {'counts': shown, 'breadth': breadth, 'limits': limits,
            'regime': regime, 'ratio': ratio, 'plan': plan}


def _index_commentary(name, quote):
    if not quote:
        return {'color': 'f-muted', 'value': '待核验', 'range': '缺少同日完整行情',
                'description': f'{name}同日行情待核验'}
    close, change = quote['close'], quote['chg']
    return {'color': 'f-up' if change >= 0 else 'f-down',
            'value': f'{close:.2f} <small>{change:+.2f}%</small>',
            'range': f"最低 {quote['low']:.2f} · 最高 {quote['high']:.2f}",
            'description': f"{name}开盘 {quote['open']:.2f} 点，日内区间 {quote['low']:.2f}—{quote['high']:.2f} 点，收报 {close:.2f} 点（{change:+.2f}%）"}


def _load_ladder_and_progression(
    day_iso: str,
    echelon: Any = None,
    previous_echelon: Any = None,
) -> tuple[dict[int, list[dict]], list[tuple[str, int, int]], list[tuple[str, int]], int, int, list[tuple[str, int, list[str]]]]:
    """提取连板梯队 (按高度降序)、今日晋级列表、昨日高位断板列表、最高高度、板块热点统计."""
    day_digits = day_iso.replace('-', '')

    # 1. 加载今日涨停个股明细
    cur_stocks: dict[str, dict] = {}
    snap_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'report_daily_snapshots', f'{day_iso}.json')

    if os.path.exists(snap_path):
        try:
            with open(snap_path, encoding='utf-8') as f:
                snap = json.load(f)
            for r in snap.get('limit_pool_rows', []):
                c = r.get('代码') or r.get('code')
                n = r.get('名称') or r.get('name')
                h = int(r.get('height') or r.get('连板数') or 1)
                sec = r.get('大主线') or r.get('细分板块') or '其它'
                amt = float(r.get('amount') or 0)
                tor = float(r.get('turnover_rate') or 0)
                cur_stocks[c] = {
                    'code': c, 'name': n, 'height': h, 'sector': sec,
                    'amount': amt, 'turnover': tor,
                }
        except Exception:
            pass

    # 若快照未命中，从 ZT_CACHE_FILE 补充
    if not cur_stocks and os.path.exists(ZT_CACHE_FILE):
        try:
            ztdf = pd.read_csv(ZT_CACHE_FILE, dtype=str)
            sub = ztdf[ztdf['日期'] == day_digits]
            for _, row in sub.iterrows():
                c = row.get('代码', '')
                n = row.get('名称', '')
                try:
                    h = int(float(row.get('连板数', 1)))
                except Exception:
                    h = 1
                cur_stocks[c] = {
                    'code': c, 'name': n, 'height': h, 'sector': '其它',
                    'amount': 0.0, 'turnover': 0.0,
                }
        except Exception:
            pass

    # 2. 分组为梯队字典 dict[int, list[dict]]
    tiers: dict[int, list[dict]] = {}
    for item in cur_stocks.values():
        tiers.setdefault(item['height'], []).append(item)
    for h in tiers:
        tiers[h].sort(key=lambda s: s['amount'], reverse=True)

    max_h = max(tiers.keys()) if tiers else 1

    # 3. 寻找上一交易日快照以计算 连板晋级 与 高标断板
    snap_dir = os.path.join(os.path.dirname(__file__), '..', 'data', 'report_daily_snapshots')
    prev_day_iso = None
    prev_day_digits = None
    if os.path.exists(snap_dir):
        all_snaps = sorted([f[:-5] for f in os.listdir(snap_dir) if f.endswith('.json') and f[:-5] < day_iso])
        if all_snaps:
            prev_day_iso = all_snaps[-1]
            prev_day_digits = prev_day_iso.replace('-', '')

    prev_stocks: dict[str, dict] = {}
    if prev_day_iso:
        p_snap_path = os.path.join(snap_dir, f'{prev_day_iso}.json')
        if os.path.exists(p_snap_path):
            try:
                with open(p_snap_path, encoding='utf-8') as f:
                    psnap = json.load(f)
                for r in psnap.get('limit_pool_rows', []):
                    c = r.get('代码') or r.get('code')
                    n = r.get('名称') or r.get('name')
                    h = int(r.get('height') or r.get('连板数') or 1)
                    prev_stocks[c] = {'code': c, 'name': n, 'height': h}
            except Exception:
                pass

    if not prev_stocks and prev_day_digits and os.path.exists(ZT_CACHE_FILE):
        try:
            ztdf = pd.read_csv(ZT_CACHE_FILE, dtype=str)
            sub = ztdf[ztdf['日期'] == prev_day_digits]
            for _, row in sub.iterrows():
                c = row.get('代码', '')
                n = row.get('名称', '')
                try:
                    h = int(float(row.get('连板数', 1)))
                except Exception:
                    h = 1
                prev_stocks[c] = {'code': c, 'name': n, 'height': h}
        except Exception:
            pass

    prev_max_h = max((s['height'] for s in prev_stocks.values()), default=1)

    # 晋级列表 (今日连板数 > 昨日连板数)
    promotions: list[tuple[str, int, int]] = []
    for c, cur_info in cur_stocks.items():
        if c in prev_stocks:
            p_h = prev_stocks[c]['height']
            if cur_info['height'] > p_h:
                promotions.append((cur_info['name'], p_h, cur_info['height']))
    promotions.sort(key=lambda x: x[2], reverse=True)

    # 昨日高标断板列表 (昨日高度 >= 2 或昨日最高板，今日未涨停)
    broken_high: list[tuple[str, int]] = []
    for c, p_info in prev_stocks.items():
        if c not in cur_stocks and (p_info['height'] >= 2 or p_info['height'] == prev_max_h):
            broken_high.append((p_info['name'], p_info['height']))
    broken_high.sort(key=lambda x: x[1], reverse=True)

    # 4. 板块题材聚集度统计
    sectors_map: dict[str, list[dict]] = {}
    for it in cur_stocks.values():
        sec = it.get('sector') or '其它'
        sectors_map.setdefault(sec, []).append(it)
    
    sector_summary = []
    for sec, stks in sorted(sectors_map.items(), key=lambda x: len(x[1]), reverse=True):
        stks_sorted = sorted(stks, key=lambda s: (s['height'], s['amount']), reverse=True)
        top_names = [f"{s['name']}{s['height']}板" if s['height'] > 1 else s['name'] for s in stks_sorted[:4]]
        sector_summary.append((sec, len(stks), top_names))

    return tiers, promotions, broken_high, max_h, prev_max_h, sector_summary


def _positive_price(value) -> float | None:
    try:
        price = float(value)
        return price if math.isfinite(price) and price > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _quote_code(value) -> str | None:
    try:
        return normalize_code(value)
    except (TypeError, ValueError):
        return None


def _previous_quote_day(day_iso: str) -> str | None:
    """A missing slice cannot silently turn a multi-day return into a day move."""
    try:
        calendar = pd.read_csv(CALENDAR_CACHE, dtype=str)
        days = sorted(set(pd.to_datetime(calendar['trade_date'], errors='coerce').dropna().dt.strftime('%Y-%m-%d')))
        position = days.index(day_iso)
        return days[position - 1] if position else None
    except (OSError, ValueError, KeyError):
        return None


def _core_price_rows(frame: pd.DataFrame | None, codes: set[str], days: set[str]) -> dict[tuple[str, str], float]:
    if frame is None or frame.empty or not {'code', 'date', 'close_raw'}.issubset(frame.columns):
        return {}
    # Normalize only the two requested dates, not the entire history cache.
    dates = pd.to_datetime(frame['date'].astype(str), errors='coerce').dt.strftime('%Y-%m-%d')
    selected = frame.loc[dates.isin(days), ['code', 'close_raw']].copy()
    selected['date'] = dates.loc[selected.index]
    selected['code'] = selected['code'].map(_quote_code)
    selected = selected[selected['code'].isin(codes)]
    selected['close_raw'] = selected['close_raw'].map(_positive_price)
    result = {}
    for (code, date), group in selected.groupby(['code', 'date']):
        values = group['close_raw'].dropna().unique()
        # Conflicting quotes for the same date are not a verified closing price.
        if len(values) == 1:
            result[(code, date)] = float(values[0])
    return result


def _load_core_stock_quotes(
    day_iso: str,
    tiers: dict[int, list[dict]],
    broken_high: list[tuple[str, int]],
    max_h: int,
    price_df: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    """动态筛选当日代表性空间龙、中位梯队、关键断板股与大成交容量中军."""
    selected_items: list[dict] = []
    seen_codes: set[str] = set()

    # 1. 空间最高板 (1-2只)
    for item in tiers.get(max_h, [])[:2]:
        c = item['code']
        if c not in seen_codes:
            seen_codes.add(c)
            selected_items.append({
                'code': c,
                'name': item['name'],
                'sector': item.get('sector', '核心'),
                'role': f"{item.get('sector', '主线')}/空间总龙",
                'desc': f"强势晋级 {max_h} 连板，成为全市场短线连板空间最高标杆",
                'is_zt': True,
            })

    # 2. 中位梯队先锋 (max_h - 1)
    if max_h > 1:
        mid_tier = tiers.get(max_h - 1, [])
        for item in mid_tier[:2]:
            c = item['code']
            if c not in seen_codes:
                seen_codes.add(c)
                selected_items.append({
                    'code': c,
                    'name': item['name'],
                    'sector': item.get('sector', '中位'),
                    'role': f"{item.get('sector', '中位')}/中位先锋",
                    'desc': f"晋级 {max_h - 1} 连板，中位换手卡位梯队中坚",
                    'is_zt': True,
                })

    # 3. 昨日高标断板分歧核心 (1只)
    if broken_high:
        b_name, b_h = broken_high[0]
        b_code = None
        if os.path.exists(SECURITY_MASTER_CACHE):
            try:
                mdf = pd.read_csv(SECURITY_MASTER_CACHE, dtype=str)
                m_sub = mdf[mdf['name'] == b_name]
                if not m_sub.empty:
                    b_code = m_sub.iloc[0]['code']
            except Exception:
                pass
        if b_code and b_code not in seen_codes:
            seen_codes.add(b_code)
            selected_items.append({
                'code': b_code,
                'name': b_name,
                'sector': '高标出清',
                'role': '高位断板/分歧出清',
                'desc': f"昨日 {b_h} 板今日断板大幅分歧出清，筹码高位震荡释放获利盘",
                'is_zt': False,
            })

    # 4. 容量大成交中军 / 领涨核心 (补充至 7 只)
    all_zt_stocks = []
    for h, stk_list in tiers.items():
        all_zt_stocks.extend(stk_list)
    all_zt_stocks.sort(key=lambda s: s.get('amount', 0), reverse=True)

    for item in all_zt_stocks:
        if len(selected_items) >= 7:
            break
        c = item['code']
        if c not in seen_codes:
            seen_codes.add(c)
            amt = item.get('amount', 0)
            amt_str = f"{amt / 1e8:.1f}亿" if amt >= 1e8 else f"{amt / 1e4:.0f}万"
            h = item.get('height', 1)
            is_20cm = '30' in c or '68' in c
            if amt >= 10e8:
                role = f"{item.get('sector', '主线')}/容量中军"
                desc = f"放量成交 {amt_str} 强势封板，引领板块合力托底"
            elif is_20cm and h >= 2:
                role = f"{item.get('sector', '主线')}/20cm先锋"
                desc = f"创业板 20cm {h} 连板，弹性抱团风向标"
            else:
                role = f"{item.get('sector', '主线')}/领涨核心"
                desc = f"放量封板（{h}板），成交 {amt_str} 助攻主线"
            selected_items.append({
                'code': c,
                'name': item['name'],
                'sector': item.get('sector', '主线'),
                'role': role,
                'desc': desc,
                'is_zt': True,
            })

    # 5. 当日原始收盘价与前一交易日原始收盘价均有效才计算涨跌。
    # 先用主流程传入的数据；缺项才补同日切片/缓存，不用涨停标签造价。
    previous_day = _previous_quote_day(day_iso)
    days = {day_iso} | ({previous_day} if previous_day else set())
    codes = {_quote_code(it['code']) for it in selected_items} - {None}
    price_rows = _core_price_rows(price_df, codes, days)
    required = {(code, day) for code in codes for day in days}
    if codes:
        for day in sorted(days):
            if all((code, day) in price_rows for code in codes):
                continue
            try:
                frame = pd.read_csv(os.path.join(PRICE_SLICE_DIR, f'{day}.csv.gz'), dtype={'code': str, 'date': str})
                for key, value in _core_price_rows(frame, codes, {day}).items():
                    price_rows.setdefault(key, value)
            except (OSError, ValueError, KeyError):
                pass
        if not required.issubset(price_rows):
            try:
                frame = pd.read_csv(PRICE_CACHE, dtype={'code': str, 'date': str})
                for key, value in _core_price_rows(frame, codes, days).items():
                    price_rows.setdefault(key, value)
            except (OSError, ValueError, KeyError):
                pass

    quotes = []
    for it in selected_items:
        code = _quote_code(it['code'])
        p = price_rows.get((code, day_iso))
        previous = price_rows.get((code, previous_day)) if previous_day else None
        chg = (p / previous - 1) * 100 if p is not None and previous is not None else None
        status = '同日收盘价待核验' if p is None else '前一交易日收盘价待核验' if previous is None else '同日及前一交易日原始收盘价已核验'
        quotes.append({
            'code': it['code'],
            'name': it['name'],
            'role': it['role'],
            'price': p,
            'chg': chg,
            'price_date': day_iso if p is not None else None,
            'previous_close_date': previous_day if previous is not None else None,
            'quote_status': status,
            'desc': it['desc'],
        })
    return quotes


def generate_daily_fupan_html(
    report_date: str | None = None,
    context: dict | None = None,
    advance_decline: dict | None = None,
    echelon: Any = None,
    previous_echelon: Any = None,
    price_df: pd.DataFrame | None = None,
) -> str:
    """根据同日已知行情生成复盘；缺失字段显示待核验。"""
    day_iso, _ = _normalize_date(report_date, context)
    m = _load_market_overview(day_iso, context, advance_decline)
    sh = _index_commentary('上证指数', m['sh'])
    cyb = _index_commentary('创业板指', m['cyb'])
    market = _market_commentary(m)
    counts = market['counts']

    tiers, promotions, broken_high, max_h, prev_max_h, sector_summary = _load_ladder_and_progression(
        day_iso, echelon, previous_echelon
    )
    stocks = _load_core_stock_quotes(day_iso, tiers, broken_high, max_h, price_df)

    # 1. 梯队与高度描述
    top_stocks = '、'.join([s['name'] for s in tiers.get(max_h, [])[:5]]) or '暂无'
    mid_h = max_h - 1 if max_h > 2 else 2
    mid_stocks = '、'.join([s['name'] for s in tiers.get(mid_h, [])[:5]])

    # 3. 高位连板与断板出清定性
    if broken_high:
        broken_str = '、'.join([f"{name} {h}板" for name, h in broken_high[:4]])
        broken_desc = f"前期高位连板股（{broken_str}）本日未继续连板；"
    else:
        broken_desc = "本次梯队记录未列出高位断板个股；"

    if max_h > 2 and mid_stocks:
        mid_desc = f"当前 {mid_h} 板梯队包括（{mid_stocks}），后续观察能否继续晋级。"
    else:
        mid_desc = "中位梯队记录有限，后续观察首二板及梯队延续。"

    # 4. 热点轮动总结
    sec_descs = []
    for sec, count, top_stks in sector_summary[:3]:
        sec_descs.append(f"<b>{sec}</b>（{count}只涨停，{'、'.join(top_stks[:3])}）")
    if len(sector_summary) > 3:
        sec4_name = sector_summary[3][0]
        sec_descs.append(f"<b>{sec4_name}</b>")
    sectors_line = '、'.join(sec_descs) + '位于本日涨停数量前列' if sec_descs else '板块归属数据待补齐'

    # 5. 形态定性与市场情绪
    regime_tag = market['regime']
    regime_desc = (f"{market['breadth']}（{counts['up']} 涨 / {counts['down']} 跌）；"
                   f"涨停 {counts['zt']} 家，跌停 {counts['dt']} 家。{market['plan']}")

    max_h_tag = f"空间板 {max_h}板 ({top_stocks.split('、')[0]})"

    # 动态梯队行
    ladder_lines = []
    for h in sorted(tiers.keys(), reverse=True):
        stk_list = [s['name'] for s in tiers[h]]
        line_cls = f"l{h}" if h in (9, 5, 3, 2) else ""
        border_clr = "#ef4444" if h >= 5 else ("#f59e0b" if h >= 3 else "#3b82f6")
        stk_str = '、'.join(stk_list[:8]) + (f" 等{len(stk_list)}只" if len(stk_list) > 8 else "")
        ladder_lines.append(f"""
            <div class="ladder-line {line_cls}" style="border-left-color:{border_clr}">
                <span class="ladder-tier">【{h} 连板】</span>
                <span class="ladder-stocks"><b style="color:#e5e7eb">{_e(stk_str)}</b></span>
            </div>
        """)

    # 核心个股表格
    stock_rows = []
    for s in stocks:
        chg = s.get('chg')
        c_cls = 'f-up' if chg is not None and chg > 0 else ('f-down' if chg is not None and chg < 0 else 'f-muted')
        price_text = f"{s['price']:.2f} 元" if s.get('price') is not None else '待核验'
        change_text = f'{chg:+.2f}%' if chg is not None else '待核验'
        tag_color = 'red' if '龙' in s['role'] or '先锋' in s['role'] else ('blue' if '中军' in s['role'] else 'amber')
        stock_rows.append(f"""
            <tr>
                <td><b>{_e(s['name'])}</b> <small style="color:#6b7280">({_e(s['code'])})</small></td>
                <td><span class="f-tag {tag_color}">{_e(s['role'])}</span></td>
                <td class="f-num">{price_text}</td>
                <td class="f-num {c_cls}">{change_text}</td>
                <td>{_e(s['desc'])}<small class="f-muted" style="display:block">{_e(s.get('quote_status', ''))}</small></td>
            </tr>
        """)

    # 动态板块轮动要点
    def sector_fact(index):
        if len(sector_summary) <= index:
            return '对应板块明细待补齐。'
        name, count, names = sector_summary[index]
        return f"<b>{_e(name)}：</b> 本日记录 {count} 只涨停，代表包括 {_e('、'.join(names[:3]))}；后续核验联动与梯队延续。"
    sec1_text, sec2_text = sector_fact(0), sector_fact(1)
    sec3_text = f"<b>市场风险：</b> {market['limits']}。板块涨停分布不能单独证明资金流入、放量或新周期确认。"

    return f'''
<!-- daily-fupan-continuous:start -->
<section id="sec-daily-fupan" class="daily-fupan-section" style="margin: 20px 0 28px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'PingFang SC', 'Microsoft YaHei', sans-serif;">
    <style>
        .fupan-clean-card {{
            background: #11141a;
            border: 1px solid #262c36;
            border-radius: 8px;
            padding: 20px 22px;
            color: #d1d5db;
        }}
        .f-top-bar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #202632;
            padding-bottom: 14px;
            margin-bottom: 16px;
            flex-wrap: wrap;
            gap: 10px;
        }}
        .f-top-bar h2 {{
            margin: 0;
            font-size: 18px;
            font-weight: 700;
            color: #f3f4f6;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .f-tag-row {{
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
        }}
        .f-tag {{
            font-size: 11.5px;
            font-weight: 600;
            padding: 3px 8px;
            border-radius: 4px;
            border: 1px solid transparent;
        }}
        .f-tag.red {{ background: rgba(239, 68, 68, 0.1); color: #f87171; border-color: rgba(239, 68, 68, 0.25); }}
        .f-tag.blue {{ background: rgba(59, 130, 246, 0.1); color: #60a5fa; border-color: rgba(59, 130, 246, 0.25); }}
        .f-tag.amber {{ background: rgba(245, 158, 11, 0.1); color: #fbbf24; border-color: rgba(245, 158, 11, 0.25); }}
        .f-tag.gray {{ background: #1a202c; color: #9ca3af; border-color: #2d3748; }}

        .f-metrics-strip {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 10px;
            margin-bottom: 18px;
        }}
        .f-m-item {{
            background: #171b24;
            border: 1px solid #232936;
            border-radius: 6px;
            padding: 10px 14px;
        }}
        .f-m-item .label {{ font-size: 11.5px; color: #8590a2; margin-bottom: 2px; }}
        .f-m-item .val {{ font-size: 17px; font-weight: 700; font-variant-numeric: tabular-nums; }}
        .f-m-item .sub {{ font-size: 11px; color: #6b7280; margin-top: 2px; }}

        .f-block {{
            background: #151922;
            border: 1px solid #202633;
            border-radius: 6px;
            padding: 14px 16px;
            margin-bottom: 12px;
        }}
        .f-block-title {{
            font-size: 13.5px;
            font-weight: 700;
            color: #e5e7eb;
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            gap: 6px;
        }}

        .f-list {{
            margin: 0;
            padding-left: 18px;
            font-size: 13px;
            line-height: 1.7;
            color: #c9d1d9;
        }}
        .f-list li {{ margin-bottom: 4px; }}
        .f-list b {{ color: #f3f4f6; }}

        .f-simple-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 12.5px;
            margin-top: 8px;
        }}
        .f-simple-table th {{
            text-align: left;
            padding: 7px 10px;
            background: #1a202c;
            color: #8b949e;
            font-weight: 600;
            border-bottom: 1px solid #2d3748;
        }}
        .f-simple-table td {{
            padding: 7px 10px;
            border-bottom: 1px solid #1e2530;
            color: #d1d5db;
        }}
        .f-simple-table tr:last-child td {{ border-bottom: none; }}
        .f-num {{ text-align: right; font-variant-numeric: tabular-nums; font-family: monospace; }}
        .f-up {{ color: #f87171; font-weight: 600; }}
        .f-down {{ color: #4ade80; font-weight: 600; }}
        .f-muted {{ color: #6b7280; }}

        .ladder-compact {{
            display: flex;
            flex-direction: column;
            gap: 6px;
            margin-top: 8px;
        }}
        .ladder-line {{
            display: flex;
            align-items: baseline;
            gap: 10px;
            font-size: 12.5px;
            background: #171b24;
            padding: 6px 10px;
            border-radius: 4px;
            border-left: 3px solid #3b82f6;
        }}
        .ladder-tier {{ font-weight: 700; color: #f3f4f6; width: 68px; flex-shrink: 0; }}
        .ladder-stocks {{ flex: 1; color: #9ca3af; }}
        .ladder-stocks b {{ color: #e5e7eb; }}

        .f-note {{
            background: rgba(59, 130, 246, 0.05);
            border-left: 3px solid #3b82f6;
            padding: 8px 12px;
            font-size: 12.5px;
            line-height: 1.6;
            color: #cbd5e1;
            margin-top: 8px;
            border-radius: 0 4px 4px 0;
        }}
    </style>

    <div class="fupan-clean-card">
        <!-- 头部简洁标题 -->
        <div class="f-top-bar">
            <div>
                <h2 id="sec-daily-fupan" class="section-title">📑 今日盘面复盘与尾盘预案</h2>
            </div>
            <div class="f-tag-row">
                <span class="f-tag red">{regime_tag}</span>
                <span class="f-tag amber">{max_h_tag}</span>
                <span class="f-tag blue">涨跌比 {counts['up']}:{counts['down']}</span>
                <span class="f-tag gray">{day_iso}</span>
            </div>
        </div>

        <!-- 关键核心数据条 (一目了然) -->
        <div class="f-metrics-strip">
            <div class="f-m-item">
                <div class="label">上证指数</div>
                <div class="val {sh['color']}">{sh['value']}</div>
                <div class="sub">{sh['range']}</div>
            </div>
            <div class="f-m-item">
                <div class="label">创业板指</div>
                <div class="val {cyb['color']}">{cyb['value']}</div>
                <div class="sub">{cyb['range']}</div>
            </div>
            <div class="f-m-item">
                <div class="label">市场涨跌家数</div>
                <div class="val" style="color:#f3f4f6"><span class="f-up">{counts['up']} 涨</span> <span style="color:#6b7280">/</span> <span class="f-down">{counts['down']} 跌</span></div>
                <div class="sub">{market['breadth']}</div>
            </div>
            <div class="f-m-item">
                <div class="label">涨跌停梯队</div>
                <div class="val"><span class="f-up">涨停 {counts['zt']}</span> <span style="color:#6b7280">/</span> <span class="f-down">跌停 {counts['dt']}</span></div>
                <div class="sub">{market['limits']}</div>
            </div>
        </div>

        <!-- 1. 盘面走势核心提炼 (极简要点) -->
        <div class="f-block">
            <div class="f-block-title">📌 一、全天走势核心梳理</div>
            <ul class="f-list">
                <li><b>指数行情：</b> {sh['description']}；{cyb['description']}。</li>
                <li><b>高位连板与梯队重构：</b> 市场最高空间板达到 <b>{max_h} 板</b>（{top_stocks}）；{broken_desc}{mid_desc}</li>
                <li><b>结构性热点轮动：</b> {sectors_line}。</li>
                <li><b>形态定性与市场情绪：</b> {regime_desc}</li>
            </ul>
        </div>

        <!-- 2. 连板梯队 (紧凑行布局) -->
        <div class="f-block">
            <div class="f-block-title">🪜 二、连板天梯与短线梯队</div>
            <div class="ladder-compact">
                {''.join(ladder_lines)}
            </div>
        </div>

        <!-- 3. 核心抱团股与大成交中军 -->
        <div class="f-block">
            <div class="f-block-title">🛡️ 三、核心抱团股与容量中军表现</div>
            <table class="f-simple-table">
                <thead>
                    <tr>
                        <th>核心标的</th>
                        <th>主属性 / 角色</th>
                        <th class="f-num">收盘价</th>
                        <th class="f-num">涨跌幅</th>
                        <th>盘面表现与观察</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(stock_rows)}
                </tbody>
            </table>
        </div>

        <!-- 4. 板块分析与轮动博弈 -->
        <div class="f-block">
            <div class="f-block-title">🔄 四、板块之间强弱与轮动逻辑</div>
            <ul class="f-list">
                <li>{sec1_text}</li>
                <li>{sec2_text}</li>
                <li>{sec3_text}</li>
            </ul>
        </div>

        <!-- 5. 尾盘重点与明日策略 -->
        <div class="f-block" style="margin-bottom:0">
            <div class="f-block-title">⚔️ 五、尾盘关注重点与明日预案</div>
            <div class="f-note">
                <b>1. 后续核验：</b> 观察 <b>{top_stocks}</b> 所在 {max_h} 板梯队能否延续；本日跌停 {counts['dt']} 家，后续对比跌停数量与高位股反馈，日线数据不能核实实时封单。<br>
                <b>2. 下一交易日条件：</b> <b>{market['plan']}</b> 具体入场、失效与退出条件见对应逐只预案。
            </div>
        </div>

    </div>
</section>
<!-- daily-fupan-continuous:end -->
'''


if __name__ == "__main__":
    html = generate_daily_fupan_html("2026-10-20")
    print("Generated dynamic recap length for 2026-10-20:", len(html))
    # 打印核心梳理部分进行肉眼核验
    import re
    m = re.search(r'📌 一、全天走势核心梳理.*?</ul', html, re.DOTALL)
    if m:
        print("\n" + m.group(0))
