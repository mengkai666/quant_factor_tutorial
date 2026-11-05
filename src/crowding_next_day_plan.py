"""明日预案独立页（site/plan/）：把规则引擎当天产出的预案集中成一页，并对上一交易日预案逐条记分。

页面上的每个事实都来自当日跑批已有的结构，本模块不另做判断：
  - 今日事实：report_context['market_thesis']['breadth_relay_state']（宽度 / 涨跌停 / 晋级率）+ echelon 连板梯队
  - 明日预案：report_context['today_decision']（三道开关、仓位、候选）+ scenario_plans（分时段触发 / 失效条件）
  - 昨日记分：report_prediction_history.jsonl 里上一交易日最后一版预测的观察池
              × 当日收盘价（主流程内存里的 price_df，缺了读价格切片）× 涨停历史缓存
AI 生成的竞价风向标只在 tactics_recap.json 由当日真实行情生成（facts_source=report_context）且日期一致时附上。

    build_next_day_plan(report_context, echelon, price_df=None) -> dict
    generate_plan_html(plan) -> str            # 独立整页
    render_plan_teaser_html(report_date) -> str  # 主报告入口卡
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from html import escape
from typing import Any

import pandas as pd

# 预案渲染只读，不导入带有缓存自愈写入副作用的 paths。
_BASE_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
DATA_DIR = os.environ.get('QF_DATA_DIR') or str(_BASE_DIR / 'data')
OUTPUT_DIR = str(_BASE_DIR / 'output')
PRICE_SLICE_DIR = os.path.join(DATA_DIR, 'price_slices')
ZT_CACHE_FILE = os.path.join(DATA_DIR, '涨停历史缓存.csv')
PRICE_CACHE = os.path.join(DATA_DIR, 'price_history_cache.csv')

PREDICTION_HISTORY = os.path.join(DATA_DIR, 'report_prediction_history.jsonl')
TACTICS_RECAP = os.path.join(DATA_DIR, 'tactics_recap.json')
_PHASES = (('auction_triggers', '竞价 9:25'), ('early_session_triggers', '开盘 9:35'),
           ('confirmation_triggers', '确认 10:00'), ('afternoon_triggers', '午后'))
_ROLE = {'attack': '进攻观察', 'confirm': '确认观察', 'risk': '风险锚', 'core': '核心', 'core_watch': '核心观察',
         'observation': '观察池'}


def _iso(value: Any) -> str:
    """Timestamp / 'YYYYMMDD' / 'YYYY-MM-DD' 统一成 'YYYY-MM-DD'；认不出返回 ''。"""
    if value is None or value == '':
        return ''
    try:
        return pd.Timestamp(str(value)).strftime('%Y-%m-%d')
    except (ValueError, TypeError):
        return ''


def _e(value: Any) -> str:
    return escape(str(value if value is not None else ''))


def _pct(value: float | None) -> str:
    return '—' if value is None else f'{value:+.2f}%'


def _closes(price_df: pd.DataFrame | None, day: str, *, data_dir=None) -> dict[str, float]:
    """某日收盘价 code -> close_raw。主流程的 price_df 优先（当日切片要到发布之后才导出）。"""
    if isinstance(price_df, pd.DataFrame) and not price_df.empty and {'date', 'code', 'close_raw'} <= set(price_df.columns):
        rows = price_df[price_df['date'].astype(str) == day]
        if not rows.empty:
            return {str(c): float(v) for c, v in zip(rows['code'], rows['close_raw']) if pd.notna(v)}
    path = os.path.join(os.path.join(data_dir, 'price_slices') if data_dir else PRICE_SLICE_DIR, f'{day}.csv.gz')
    if os.path.exists(path):
        try:
            rows = pd.read_csv(path, dtype={'code': str})
            if not {'date', 'code', 'close_raw'} <= set(rows.columns):
                return {}
            rows = rows[rows['date'].astype(str) == day]
            return {str(c): float(v) for c, v in zip(rows['code'], rows['close_raw']) if pd.notna(v)}
        except (OSError, ValueError, EOFError, KeyError):
            return {}
    return {}


def _limit_ratio(code: str, name: str = '') -> float:
    """涨跌幅限制看板块不看 ST 标记以外的东西：科创 / 创业 20%，北交所 30%，ST 5%，其余 10%。"""
    digits = code[-6:]
    if 'ST' in name.upper():
        return 0.05
    if code.startswith('bj') or digits.startswith(('8', '92', '43')):
        return 0.30
    if digits.startswith(('688', '689', '300', '301')):
        return 0.20
    return 0.10


def _price_limit(prev: float | None, close: float | None, code: str, name: str = '') -> str:
    """按 preclose 精确重建涨跌停价（Decimal 四舍五入）；涨停池不全，不能只信缓存。"""
    from decimal import ROUND_HALF_UP, Decimal
    if not prev or not close:
        return ''
    ratio = Decimal(str(_limit_ratio(code, name)))
    up = (Decimal(str(prev)) * (1 + ratio)).quantize(Decimal('0.01'), ROUND_HALF_UP)
    down = (Decimal(str(prev)) * (1 - ratio)).quantize(Decimal('0.01'), ROUND_HALF_UP)
    value = Decimal(str(close)).quantize(Decimal('0.01'), ROUND_HALF_UP)
    return 'ZT' if value == up else 'DT' if value == down else ''


def _limit_rows(day: str, *, data_dir=None) -> dict[str, tuple[str, int]]:
    """涨停历史缓存里某日的 code -> (ZT/DT, 连板数)；缓存不覆盖该日返回空。"""
    source = os.path.join(data_dir, '涨停历史缓存.csv') if data_dir else ZT_CACHE_FILE
    if not os.path.exists(source):
        return {}
    try:
        zt = pd.read_csv(source, dtype=str, encoding='utf-8-sig')
    except (OSError, ValueError):
        return {}
    if not {'日期', '代码', '类型', '连板数'} <= set(zt.columns):
        return {}
    rows = zt[zt['日期'] == day.replace('-', '')]
    heights = pd.to_numeric(rows['连板数'], errors='coerce').fillna(0).astype(int)
    return {str(c): (str(t), int(h)) for c, t, h in zip(rows['代码'], rows['类型'], heights)}


def _previous_prediction(report_date: str, *, data_dir=None) -> dict[str, Any] | None:
    """report_date 之前最近一个交易日的最后一版预测（留痕 append-only，同日多次重跑取最后一行）。"""
    source = os.path.join(data_dir, 'report_prediction_history.jsonl') if data_dir else PREDICTION_HISTORY
    if not os.path.exists(source):
        return None
    latest: dict[str, dict[str, Any]] = {}
    with open(source, encoding='utf-8') as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            day = _iso(rec.get('report_date'))
            if rec.get('event_type') == 'prediction' and day and day < report_date:
                latest[day] = rec
    return latest[max(latest)] if latest else None


def _breadth(thesis: Any) -> dict[str, Any]:
    state = thesis.get('breadth_relay_state') if isinstance(thesis, dict) else None
    return state if isinstance(state, dict) else {}


def score_previous_plan(report_date: str, today_thesis: dict, price_df: pd.DataFrame | None = None, *, data_dir=None) -> dict[str, Any] | None:
    """上一交易日预案的观察池逐只记分 + 市场指标对比；拿不到上一日预测返回 None。"""
    prev = _previous_prediction(report_date, data_dir=data_dir)
    if not prev:
        return None
    prev_day = _iso(prev.get('report_date'))
    closes_t, closes_p = _closes(price_df, report_date, data_dir=data_dir), _closes(price_df, prev_day, data_dir=data_dir)
    lim_t, lim_p = _limit_rows(report_date, data_dir=data_dir), _limit_rows(prev_day, data_dir=data_dir)

    pool: dict[str, dict] = {}
    for plan in prev.get('scenario_plans') or []:
        for row in (plan.get('observation_pool') or []) if isinstance(plan, dict) else []:
            if isinstance(row, dict) and row.get('code'):
                pool.setdefault(str(row['code']), row)
    rows: list[dict[str, Any]] = []
    for code, row in pool.items():
        name = str(row.get('name') or code).strip()
        c0, c1 = closes_p.get(code), closes_t.get(code)
        pct = (c1 / c0 - 1) * 100 if c0 and c1 else None
        t, p = lim_t.get(code), lim_p.get(code)
        kind = _price_limit(c0, c1, code, name) or (t[0] if t else '')
        if kind == 'ZT':
            status = f'涨停 · {t[1]} 板' if t and t[0] == 'ZT' and t[1] else '涨停'
        elif kind == 'DT':
            status = '跌停'
        elif p and p[0] == 'ZT':
            status = f'断板（昨 {p[1]} 板）'
        else:
            status = ''
        role = str(row.get('role') or '')
        rows.append({'name': name, 'code': code, 'role': _ROLE.get(role, role), 'key': role not in ('', 'observation'),
                     'height': row.get('height'), 'pct': pct, 'status': status})
    rows.sort(key=lambda r: (r['pct'] is None, -(r['pct'] or 0)))

    known: list[float] = [r['pct'] for r in rows if r['pct'] is not None]
    prev_b, now_b = _breadth(prev.get('market_thesis')), _breadth(today_thesis)
    metrics = []
    for key, label, fmt in (('breadth_ratio', '上涨占比', '{:.1%}'), ('limit_up', '涨停家数', '{:.0f}'),
                            ('limit_down', '跌停家数', '{:.0f}'), ('promotion_rate', '晋级率', '{:.1%}')):
        a, b = prev_b.get(key), now_b.get(key)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            metrics.append({'label': label, 'before': fmt.format(a), 'after': fmt.format(b), 'up': b > a, 'same': b == a})
    primary = next((p for p in prev.get('scenario_plans') or [] if isinstance(p, dict)
                    and p.get('scenario_id') == prev.get('primary_scenario_id')), None)
    return {
        'prev_day': prev_day,
        'primary_title': (primary or {}).get('title') or '',
        'invalidation': list((primary or {}).get('invalidation_conditions') or []),
        'rows': rows,
        'metrics': metrics,
        'summary': (f'观察池 {len(rows)} 只：{sum(v > 0 for v in known)} 只收涨、{sum(v < 0 for v in known)} 只收跌，'
                    f'等权 {_pct(sum(known) / len(known))}' if known else f'观察池 {len(rows)} 只：当日收盘价缺失，无法记分'),
    }


def _ladder(echelon: Any, tiers: int = 3) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in echelon or []:
        if not isinstance(row, dict):
            continue
        digits = ''.join(ch for ch in str(row.get('height', '')) if ch.isdigit())
        height = int(digits) if digits else (1 if '首板' in str(row.get('height', '')) else 0)
        if height >= 2:
            out.append({'height': height, 'stocks': [str(s).strip() for s in row.get('stocks') or []]})
    return sorted(out, key=lambda r: -r['height'])[:tiers]


def _broken_leaders(report_date: str, prev_day: str, price_df: pd.DataFrame | None = None, *, data_dir=None) -> list[dict[str, Any]]:
    """昨日 ≥2 板、今日未涨停的风险观察名单；不推断次日反包概率。"""
    if not prev_day:
        return []
    lim_t, lim_p = _limit_rows(report_date, data_dir=data_dir), _limit_rows(prev_day, data_dir=data_dir)
    if not lim_t or not lim_p:
        return []
    closes_t, closes_p = _closes(price_df, report_date, data_dir=data_dir), _closes(price_df, prev_day, data_dir=data_dir)
    try:
        zt = pd.read_csv(os.path.join(data_dir, '涨停历史缓存.csv') if data_dir else ZT_CACHE_FILE, dtype=str, encoding='utf-8-sig')
        names = dict(zip(zt['代码'], zt['名称'].str.strip()))
    except (OSError, ValueError, KeyError):
        names = {}
    out = []
    for code, (kind, height) in lim_p.items():
        if kind != 'ZT' or height < 2 or lim_t.get(code, ('', 0))[0] == 'ZT':
            continue
        # 涨停池会漏票：价格重建仍是涨停的不算断板
        if _price_limit(closes_p.get(code), closes_t.get(code), code, names.get(code, '')) == 'ZT':
            continue
        out.append({'code': code, 'name': names.get(code, code), 'height': height})
    return sorted(out, key=lambda r: -r['height'])


def _ai_beacons(report_date: str, *, data_dir=None) -> list[dict[str, str]]:
    try:
        with open(os.path.join(data_dir, 'tactics_recap.json') if data_dir else TACTICS_RECAP, encoding='utf-8') as f:
            recap = json.load(f)
    except (OSError, ValueError):
        return []
    # 只接受用当日真实行情生成的版本；旧版本曾用写死的 9/24 盘面冒充当天
    if _iso(recap.get('report_date')) != report_date or recap.get('facts_source') != 'report_context':
        return []
    beacons = (recap.get('tomorrow_plan') or {}).get('auction_beacons') or []
    return [b for b in beacons if isinstance(b, dict)]


def _prev_trade_day(price_df: pd.DataFrame | None, day: str) -> str:
    dates: set[str] = set()
    if isinstance(price_df, pd.DataFrame) and 'date' in price_df.columns:
        dates.update(price_df['date'].astype(str).unique())
    if os.path.isdir(PRICE_SLICE_DIR):
        dates.update(n[:10] for n in os.listdir(PRICE_SLICE_DIR) if n.endswith('.csv.gz'))
    earlier = [d for d in dates if d < day]
    return max(earlier) if earlier else ''


def collect_market_facts(report_context: dict, echelon: Any = None, price_df: pd.DataFrame | None = None) -> dict[str, Any]:
    """给 AI 研判层的当日行情事实（替代曾经写死的 9/24 盘面）；trade_date 就是报告日，调用方据此校验。"""
    ctx = report_context if isinstance(report_context, dict) else {}
    day = _iso(ctx.get('report_date'))
    b = _breadth(ctx.get('market_thesis'))
    ladder = _ladder(echelon, tiers=5)
    prev_day = _prev_trade_day(price_df, day)
    closes_t, closes_p = _closes(price_df, day), _closes(price_df, prev_day)

    targets = []
    try:
        with open(TACTICS_RECAP, encoding='utf-8') as f:
            recap = json.load(f)
        targets = (recap.get('tomorrow_plan') or {}).get('focus_targets') or []
    except (OSError, ValueError):
        pass
    performance = []
    for t in targets:
        digits = str(t.get('code') or '')[-6:]
        code = next((c for c in closes_t if c.endswith(digits)), None) if digits else None
        c0, c1 = (closes_p.get(code), closes_t.get(code)) if code else (None, None)
        performance.append({'code': digits, 'name': t.get('name'), 'role': t.get('role'),
                            'close': c1, 'change_pct': round((c1 / c0 - 1) * 100, 2) if c0 and c1 else None})
    top = ladder[0] if ladder else None
    return {
        'trade_date': day,
        'previous_trade_date': prev_day,
        'breadth': {
            'breadth_ratio': b.get('breadth_ratio'),
            'zt_count': b.get('limit_up'),
            'dt_count': b.get('limit_down'),
            'promotion_rate': b.get('promotion_rate'),
            'zt_highest': {'names': top['stocks'], 'height': top['height']} if top else None,
            'zt_ladder_summary': '；'.join(f"{r['height']}板: {'、'.join(r['stocks'][:6])}" for r in ladder),
        },
        'micro_cycle': (ctx.get('micro_cycle') or {}).get('label') or '',
        'focus_targets_performance': performance,
        'facts_note': '收盘价与涨跌停来自报告同一份上下文与价格缓存；没有开盘/最低价，不得推断分时走势。',
    }


def build_next_day_plan(report_context: dict, echelon: Any = None, price_df: pd.DataFrame | None = None,
                        *, output_dir: str | None = None, data_dir: str | None = None) -> dict[str, Any]:
    ctx = report_context if isinstance(report_context, dict) else {}
    report_date = _iso(ctx.get('report_date'))
    if not report_date:
        raise ValueError('构建预案需要有效的收盘报告日期')
    decision = ctx.get('today_decision') if isinstance(ctx.get('today_decision'), dict) else {}
    target = _iso(ctx.get('target_trade_date')) or _iso((decision.get('priority') or {}).get('trading_date'))
    if not target or target <= report_date:
        from data_sources.calendar_provider import CalendarProvider
        target = CalendarProvider(cache_path=os.path.join(data_dir or DATA_DIR, 'trading_calendar_cache.csv')).cached_next_trading_day(report_date) or ''
    readiness = ctx.get('decision_readiness') or decision.get('readiness') or {}
    # 研究榜单和价格强势不能反向提升策略、盘中信号或行情资格。
    allowed = (decision.get('execution_allowed') is True and bool(target)
               and readiness.get('execution_ready') is True and readiness.get('plan_permitted') is True
               and _iso(decision.get('report_date')) == report_date
               and _iso(readiness.get('report_date')) == report_date
               and _iso(readiness.get('target_trade_date')) == target
               and (readiness.get('data') or {}).get('status') == 'ready'
               and (readiness.get('strategy') or {}).get('status') in ('ready', 'verified'))
    quality = ctx.get('quality') or {}
    if quality.get('critical_blocked') or ctx.get('publication_mode') == 'facts_only':
        allowed = False
    thesis = ctx.get('market_thesis') if isinstance(ctx.get('market_thesis'), dict) else {}
    breadth = _breadth(thesis)
    plans = [p for p in ctx.get('scenario_plans') or [] if isinstance(p, dict)]
    # 实时上下文不带 primary_scenario_id；留痕侧（prediction_review）按概率取 max，概率全空时就是第一个
    primary_id = ctx.get('primary_scenario_id') or (plans[0].get('scenario_id') if plans else None)

    scenarios = []
    for plan in plans:
        scenarios.append({
            'title': plan.get('title') or plan.get('scenario_type') or '',
            'type': plan.get('scenario_type') or '',
            'primary': bool(primary_id) and plan.get('scenario_id') == primary_id,
            'premise': list(plan.get('premise') or []),
            'ceiling': plan.get('position_ceiling'),
            'phases': [(label, list(plan.get(key) or [])) for key, label in _PHASES if plan.get(key)],
            'invalidation': list(plan.get('invalidation_conditions') or []),
            'adjustments': list(plan.get('position_adjustments') or []),
            'pool': [{'name': str(r.get('name') or '').strip(), 'code': r.get('code'), 'height': r.get('height'),
                      'ml': r.get('ml') or '', 'role': _ROLE.get(str(r.get('role')), str(r.get('role') or ''))}
                     for r in (plan.get('observation_pool') or [])[:8] if isinstance(r, dict)],
        })
    scenarios.sort(key=lambda s: not s['primary'])

    source_options = {'data_dir': data_dir} if data_dir else {}
    scorecard = score_previous_plan(report_date, thesis, price_df, **source_options) if report_date else None
    res_ladder = _ladder(echelon)
    res_facts = {k: breadth.get(k) for k in ('breadth_ratio', 'limit_up', 'limit_down', 'promotion_rate', 'state')}
    if not res_ladder and report_date:
        snap_path = os.path.join(data_dir or DATA_DIR, 'report_daily_snapshots', f'{report_date}.json')
        if os.path.exists(snap_path):
            try:
                with open(snap_path, encoding='utf-8') as sf:
                    snap_data = json.load(sf)
                    if _iso(snap_data.get('report_date') or snap_data.get('date')) != report_date:
                        snap_data = {}
                    if not res_ladder:
                        if snap_data.get('ladder'):
                            res_ladder = _ladder(snap_data.get('ladder'))
                        elif snap_data.get('limit_pool_rows'):
                            rows = snap_data.get('limit_pool_rows', [])
                            h_map = {}
                            for r in rows:
                                h = r.get('连板数', 1)
                                if h >= 2:
                                    h_map.setdefault(h, []).append(r.get('name'))
                            res_ladder = [{'height': h, 'stocks': h_map[h]} for h in sorted(h_map.keys(), reverse=True)]
                    for key in ('limit_up', 'limit_down', 'promotion_rate', 'breadth_ratio'):
                        if res_facts.get(key) is None:
                            res_facts[key] = snap_data.get(key)
            except Exception:
                pass

    from crowding_plan_data import build_crowding_plan_data
    from cb_plan_data import load_cb_plan_data
    crowding = build_crowding_plan_data(report_date, output_dir=output_dir or OUTPUT_DIR,
                                      data_dir=data_dir or DATA_DIR, report_context=ctx)
    cb = load_cb_plan_data(report_date, output_dir=output_dir or OUTPUT_DIR, data_dir=data_dir or DATA_DIR)
    return {
        'report_date': report_date,
        'target_date': target,
        'facts': res_facts,
        'micro_cycle': (ctx.get('micro_cycle') or {}).get('label') or (ctx.get('micro_cycle') or {}).get('stage') or '',
        'ladder': res_ladder,
        'decision': {
            'position': decision.get('position') or '',
            'default_action': (decision.get('default_action') or '按已确认条件执行') if allowed else '不开新仓，只观察条件是否成立',
            'execution_allowed': allowed,
            'mainline': decision.get('mainline') or '',
            'gates': [g for g in decision.get('watch_items') or [] if isinstance(g, dict)],
            'candidates': [c for c in decision.get('candidates') or [] if isinstance(c, dict)][:10],
        },
        'scenarios': scenarios,
        'avoid': _broken_leaders(report_date, (scorecard or {}).get('prev_day', ''), price_df, **source_options),
        'scorecard': scorecard,
        'ai_beacons': _ai_beacons(report_date, **source_options),
        'readiness': readiness,
        'crowding': crowding,
        'cb': cb,
        'warnings': ([] if target else ['下一交易日尚未从交易日历确认；不得按自然日推算。']),
    }



from plan_page_io import (load_plan_context, generate_plan_html, render_plan_panel_html,
                          render_plan_teaser_html, export_plan_standalone_reports)
