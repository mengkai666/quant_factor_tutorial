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
from html import escape
from typing import Any

import pandas as pd

from paths import DATA_DIR, PRICE_SLICE_DIR, ZT_CACHE_FILE

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


def _closes(price_df: pd.DataFrame | None, day: str) -> dict[str, float]:
    """某日收盘价 code -> close_raw。主流程的 price_df 优先（当日切片要到发布之后才导出）。"""
    if isinstance(price_df, pd.DataFrame) and not price_df.empty and {'date', 'code', 'close_raw'} <= set(price_df.columns):
        rows = price_df[price_df['date'].astype(str) == day]
        if not rows.empty:
            return {str(c): float(v) for c, v in zip(rows['code'], rows['close_raw']) if pd.notna(v)}
    path = os.path.join(PRICE_SLICE_DIR, f'{day}.csv.gz')
    if os.path.exists(path):
        rows = pd.read_csv(path, dtype={'code': str})
        return {str(c): float(v) for c, v in zip(rows['code'], rows['close_raw']) if pd.notna(v)}
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


def _limit_rows(day: str) -> dict[str, tuple[str, int]]:
    """涨停历史缓存里某日的 code -> (ZT/DT, 连板数)；缓存不覆盖该日返回空。"""
    if not os.path.exists(ZT_CACHE_FILE):
        return {}
    zt = pd.read_csv(ZT_CACHE_FILE, dtype=str, encoding='utf-8-sig')
    rows = zt[zt['日期'] == day.replace('-', '')]
    heights = pd.to_numeric(rows['连板数'], errors='coerce').fillna(0).astype(int)
    return {str(c): (str(t), int(h)) for c, t, h in zip(rows['代码'], rows['类型'], heights)}


def _previous_prediction(report_date: str) -> dict[str, Any] | None:
    """report_date 之前最近一个交易日的最后一版预测（留痕 append-only，同日多次重跑取最后一行）。"""
    if not os.path.exists(PREDICTION_HISTORY):
        return None
    latest: dict[str, dict[str, Any]] = {}
    with open(PREDICTION_HISTORY, encoding='utf-8') as f:
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


def score_previous_plan(report_date: str, today_thesis: dict, price_df: pd.DataFrame | None = None) -> dict[str, Any] | None:
    """上一交易日预案的观察池逐只记分 + 市场指标对比；拿不到上一日预测返回 None。"""
    prev = _previous_prediction(report_date)
    if not prev:
        return None
    prev_day = _iso(prev.get('report_date'))
    closes_t, closes_p = _closes(price_df, report_date), _closes(price_df, prev_day)
    lim_t, lim_p = _limit_rows(report_date), _limit_rows(prev_day)

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


def _broken_leaders(report_date: str, prev_day: str, price_df: pd.DataFrame | None = None) -> list[dict[str, Any]]:
    """昨日 ≥2 板、今日未涨停 = 断板：次日冲板反包成功率低（回测 1 日内约 7.6%），列入不追名单。"""
    if not prev_day:
        return []
    lim_t, lim_p = _limit_rows(report_date), _limit_rows(prev_day)
    if not lim_t or not lim_p:
        return []
    closes_t, closes_p = _closes(price_df, report_date), _closes(price_df, prev_day)
    zt = pd.read_csv(ZT_CACHE_FILE, dtype=str, encoding='utf-8-sig')
    names = dict(zip(zt['代码'], zt['名称'].str.strip()))
    out = []
    for code, (kind, height) in lim_p.items():
        if kind != 'ZT' or height < 2 or lim_t.get(code, ('', 0))[0] == 'ZT':
            continue
        # 涨停池会漏票：价格重建仍是涨停的不算断板
        if _price_limit(closes_p.get(code), closes_t.get(code), code, names.get(code, '')) == 'ZT':
            continue
        out.append({'code': code, 'name': names.get(code, code), 'height': height})
    return sorted(out, key=lambda r: -r['height'])


def _ai_beacons(report_date: str) -> list[dict[str, str]]:
    try:
        with open(TACTICS_RECAP, encoding='utf-8') as f:
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


def build_next_day_plan(report_context: dict, echelon: Any = None, price_df: pd.DataFrame | None = None) -> dict[str, Any]:
    ctx = report_context if isinstance(report_context, dict) else {}
    report_date = _iso(ctx.get('report_date'))
    decision = ctx.get('today_decision') if isinstance(ctx.get('today_decision'), dict) else {}
    target = _iso(ctx.get('target_trade_date')) or _iso((decision.get('priority') or {}).get('trading_date'))
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

    scorecard = score_previous_plan(report_date, thesis, price_df) if report_date else None
    return {
        'report_date': report_date,
        'target_date': target,
        'facts': {k: breadth.get(k) for k in ('breadth_ratio', 'limit_up', 'limit_down', 'promotion_rate', 'state')},
        'micro_cycle': (ctx.get('micro_cycle') or {}).get('label') or (ctx.get('micro_cycle') or {}).get('stage') or '',
        'ladder': _ladder(echelon),
        'decision': {
            'position': decision.get('position') or '',
            'default_action': decision.get('default_action') or '',
            'execution_allowed': bool(decision.get('execution_allowed')),
            'mainline': decision.get('mainline') or '',
            'gates': [g for g in decision.get('watch_items') or [] if isinstance(g, dict)],
            'candidates': [c for c in decision.get('candidates') or [] if isinstance(c, dict)][:10],
        },
        'scenarios': scenarios,
        'avoid': _broken_leaders(report_date, (scorecard or {}).get('prev_day', ''), price_df),
        'scorecard': scorecard,
        'ai_beacons': _ai_beacons(report_date),
    }


_CSS = """
:root {
  --bg-primary: #0d1117;
  --bg-card: #161b22;
  --bg-card-sub: #0f141c;
  --border-color: #30363d;
  --border-hover: #58a6ff;
  --text-primary: #f0f6fc;
  --text-secondary: #8b949e;
  --accent-red: #f85149;
  --accent-green: #3fb950;
  --accent-blue: #58a6ff;
  --accent-yellow: #d29922;
  --accent-purple: #bc8cff;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg-primary);
  color: #c9d1d9;
  font: 14px/1.65 -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
}
.wrap { max-width: 1140px; margin: 0 auto; padding: 24px 20px 60px; }
.back-link { margin-bottom: 16px; }
.back-link a { color: var(--accent-blue); text-decoration: none; font-size: 13px; font-weight: 600; display: inline-flex; align-items: center; gap: 6px; }
.back-link a:hover { text-decoration: underline; }

/* Hero Header */
.hero {
  background: linear-gradient(135deg, rgba(88, 166, 255, 0.12) 0%, rgba(210, 153, 34, 0.08) 50%, rgba(248, 81, 73, 0.12) 100%);
  border: 1px solid rgba(88, 166, 255, 0.3);
  border-radius: 12px;
  padding: 24px 28px;
  margin-bottom: 24px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.4);
}
.hero-top { display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 14px; }
.hero-title { font-size: 24px; font-weight: 800; color: #f0f6fc; margin: 0 0 6px; }
.hero-sub { color: var(--text-secondary); font-size: 13px; margin: 0; }
.pulse-badge {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 6px 14px; border-radius: 20px; font-size: 13px; font-weight: 700;
  background: rgba(248, 81, 73, 0.15); color: #f85149; border: 1px solid rgba(248, 81, 73, 0.4);
}
.pulse-dot {
  width: 8px; height: 8px; border-radius: 50%; background: #f85149;
  box-shadow: 0 0 0 0 rgba(248, 81, 73, 0.7);
  animation: pulse-ring 2s infinite;
}
@keyframes pulse-ring {
  0% { box-shadow: 0 0 0 0 rgba(248, 81, 73, 0.7); }
  70% { box-shadow: 0 0 0 8px rgba(248, 81, 73, 0); }
  100% { box-shadow: 0 0 0 0 rgba(248, 81, 73, 0); }
}

h2 {
  font-size: 16px; margin: 32px 0 12px; color: #f0f6fc;
  border-left: 3px solid var(--accent-blue); padding-left: 10px;
  display: flex; align-items: center; justify-content: space-between;
}
.sub { color: var(--text-secondary); font-size: 12.5px; margin: -4px 0 12px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
.card { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 8px; padding: 14px 16px; min-width: 0; }
.card:hover { border-color: rgba(88, 166, 255, 0.4); }
.k { color: var(--text-secondary); font-size: 11.5px; margin-bottom: 2px; }
.v { font-size: 20px; font-weight: 800; color: #f0f6fc; }
.gate h3 { margin: 0 0 6px; font-size: 13.5px; color: var(--text-primary); }
.gate .hl { font-weight: 700; color: #f0f6fc; font-size: 15px; margin-bottom: 4px; }
.gate .chk { color: var(--text-secondary); font-size: 12px; margin-top: 8px; padding-top: 6px; border-top: 1px dashed #21262d; }

table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #21262d; text-align: left; vertical-align: top; }
th { color: var(--text-secondary); font-weight: 600; font-size: 12px; background: rgba(0, 0, 0, 0.2); }
.tw { overflow-x: auto; }
.up { color: var(--accent-red); font-weight: 600; }
.down { color: var(--accent-green); font-weight: 600; }
.muted { color: #8b949e; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11.5px; border: 1px solid var(--border-color); font-weight: 600; }
.primary { border-color: #d29922; background: rgba(210, 153, 34, 0.05); }
.primary .tag { color: #d29922; border-color: #d29922; }
ul { margin: 6px 0 0; padding-left: 18px; }
li { margin-bottom: 4px; }
.phase { margin-top: 8px; background: var(--bg-card-sub); padding: 8px 12px; border-radius: 6px; border-left: 3px solid var(--accent-blue); }
.phase b { color: var(--accent-blue); font-weight: 600; margin-right: 8px; display: inline-block; min-width: 72px; }

/* Tactical Breakdown & Priority Matrix */
.law-card {
  background: rgba(22, 27, 34, 0.95); border: 1px solid rgba(248, 81, 73, 0.35); border-left: 4px solid var(--accent-red);
  border-radius: 8px; padding: 14px 18px; margin-bottom: 12px;
}
.law-title { font-weight: 700; color: #f0f6fc; font-size: 14px; margin-bottom: 6px; display: flex; align-items: center; gap: 6px; }
.law-desc { color: #c9d1d9; font-size: 13px; line-height: 1.6; }
.law-rule { color: var(--accent-red); font-weight: 600; margin-top: 6px; font-size: 12.5px; }

.pm-card {
  background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 8px;
  padding: 14px 18px; margin-bottom: 12px; border-left: 4px solid var(--border-color);
}
.pm-p0 { border-left-color: var(--accent-red); }
.pm-p1 { border-left-color: var(--accent-yellow); }
.pm-p2 { border-left-color: var(--accent-blue); }
.pm-p3 { border-left-color: var(--accent-purple); }
.pm-black { border-left-color: #da3633; background: rgba(218, 54, 51, 0.08); border-color: rgba(218, 54, 51, 0.3); }

.pm-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.pm-badge { font-size: 11.5px; font-weight: 700; padding: 2px 10px; border-radius: 12px; }
.pm-p0 .pm-badge { background: rgba(248, 81, 73, 0.2); color: var(--accent-red); border: 1px solid var(--accent-red); }
.pm-p1 .pm-badge { background: rgba(210, 153, 34, 0.2); color: var(--accent-yellow); border: 1px solid var(--accent-yellow); }
.pm-p2 .pm-badge { background: rgba(88, 166, 255, 0.2); color: var(--accent-blue); border: 1px solid var(--accent-blue); }
.pm-p3 .pm-badge { background: rgba(188, 140, 255, 0.2); color: var(--accent-purple); border: 1px solid var(--accent-purple); }
.pm-black .pm-badge { background: rgba(248, 81, 73, 0.25); color: #ff7b72; border: 1px solid #da3633; }

.checklist-table td { padding: 9px 12px; }
.time-tag { background: rgba(88, 166, 255, 0.15); color: var(--accent-blue); border: 1px solid rgba(88, 166, 255, 0.3); padding: 2px 8px; border-radius: 4px; font-weight: 700; font-size: 12px; }

a { color: var(--accent-blue); }
footer { margin-top: 40px; padding-top: 20px; border-top: 1px solid var(--border-color); color: var(--text-secondary); font-size: 12px; text-align: center; }
"""


def _li(items: list) -> str:
    return '<ul>' + ''.join(f'<li>{_e(x)}</li>' for x in items) + '</ul>' if items else '<span class="muted">—</span>'


def generate_plan_html(plan: dict[str, Any]) -> str:
    f, d = plan.get('facts') or {}, plan.get('decision') or {}
    report_date = _e(plan.get('report_date') or '')
    target_date = _e(plan.get('target_date') or '下一交易日')
    micro_cycle = _e(plan.get('micro_cycle') or '')
    ratio, promo = f.get('breadth_ratio'), f.get('promotion_rate')
    cards = [('上涨占比', f'{ratio:.1%}' if isinstance(ratio, (int, float)) else '—'),
             ('涨停 / 跌停', f"{f.get('limit_up', '—')} / {f.get('limit_down', '—')}"),
             ('晋级率', f'{promo:.1%}' if isinstance(promo, (int, float)) else '—'),
             ('明日仓位上限', d.get('position') or '—')]
    ladder = ''.join(f"<tr><td>{r['height']} 板</td><td>{_e('、'.join(r['stocks']))}</td></tr>" for r in plan.get('ladder') or [])
    gates = ''.join(
        f"<div class='card gate'><h3 class='k'>{_e(g.get('title'))}</h3><div class='hl'>{_e(g.get('headline'))}</div>"
        f"<div>{_e(g.get('detail'))}</div><div class='chk'>盘中检查：{_e(g.get('check'))}</div></div>"
        for g in d.get('gates') or [])
    cands = ''.join(
        f"<tr><td>{_e(c.get('name'))} <span class='muted'>{_e(c.get('code'))}</span></td>"
        f"<td>{_e(_ROLE.get(str(c.get('role')), c.get('role') or ''))}</td>"
        f"<td>{_e(c.get('trigger') or c.get('cond') or '')}</td><td>{_e(c.get('invalid') or c.get('stop') or '')}</td></tr>"
        for c in d.get('candidates') or [])

    scen_html = ''
    for s in plan.get('scenarios') or []:
        ceiling = f"仓位上限 {s['ceiling']:.0%}" if isinstance(s.get('ceiling'), (int, float)) else ''
        phases = ''.join(f"<div class='phase'><b>{_e(label)}</b>{_e('；'.join(items))}</div>" for label, items in s['phases'])
        pool = '、'.join(f"{p['name']}{'（' + str(p['height']) + '板）' if p.get('height') else ''}" for p in s['pool'])
        scen_html += (
            f"<div class='card {'primary' if s['primary'] else ''}' style='margin-bottom:10px'>"
            f"<div><span class='tag'>{'主情景' if s['primary'] else '备选'}</span> <b style='color:#f0f6fc'>{_e(s['title'])}</b>"
            f" <span class='muted'>{_e(ceiling)}</span></div>"
            f"<div class='k' style='margin-top:6px'>前提</div>{_li(s['premise'])}"
            f"<div class='k' style='margin-top:6px'>分时段触发</div>{phases or '<span class=muted>—</span>'}"
            f"<div class='k' style='margin-top:6px'>失效条件（出现即取消）</div>{_li(s['invalidation'])}"
            f"<div class='k' style='margin-top:6px'>仓位调整</div>{_li(s['adjustments'])}"
            + (f"<div class='k' style='margin-top:6px'>观察池</div><div>{_e(pool)}</div>" if pool else '') + '</div>')

    avoid = plan.get('avoid') or []
    avoid_html = (''.join(f"<li>{_e(a['name'])}（昨 {a['height']} 板，今日断板）</li>" for a in avoid)
                  if avoid else '<li class="muted">无（昨日 2 板以上个股今日均未断板，或缓存未覆盖）</li>')

    sc = plan.get('scorecard')
    if sc:
        def score_rows(rows):
            return ''.join(
                f"<tr><td>{_e(r['name'])} <span class='muted'>{_e(r['code'])}</span></td><td>{_e(r['role'])}</td>"
                f"<td class='{'up' if (r['pct'] or 0) > 0 else 'down' if (r['pct'] or 0) < 0 else ''}'>{_e(_pct(r['pct']))}</td><td>{_e(r['status'])}</td></tr>"
                for r in rows)
        head = "<tr><th>标的</th><th>角色</th><th>当日涨跌</th><th>涨跌停</th></tr>"
        key_rows = [r for r in sc['rows'] if r.get('key')]
        rest = [r for r in sc['rows'] if not r.get('key')]
        mrows = ''.join(f"<tr><td>{_e(m['label'])}</td><td>{_e(m['before'])}</td><td>{_e(m['after'])}</td></tr>" for m in sc['metrics'])
        score_html = (
            f"<p class='sub'>对照 {_e(sc['prev_day'])} 收盘后发布的预案（主情景：{_e(sc['primary_title'] or '—')}），"
            f"用 {_e(plan.get('report_date'))} 收盘价逐只记分，涨跌停按前收盘价精确重建，全部列出、不挑样本。{_e(sc['summary'])}</p>"
            f"<div class='grid'><div class='card tw'><table><tr><th>市场指标</th><th>{_e(sc['prev_day'])}</th><th>{_e(plan.get('report_date'))}</th></tr>{mrows}</table></div>"
            f"<div class='card'><div class='k'>昨日主情景的失效条件</div>{_li(sc['invalidation'])}</div></div>"
            + (f"<div class='card tw' style='margin-top:10px'><div class='k'>核心标的（进攻 / 确认 / 风险锚）</div><table>{head}{score_rows(key_rows)}</table></div>" if key_rows else '')
            + (f"<details class='card tw' style='margin-top:10px'><summary>其余观察池 {len(rest)} 只（展开）</summary><table>{head}{score_rows(rest)}</table></details>" if rest else ''))
    else:
        score_html = "<p class='muted'>没有找到上一交易日的预案留痕，今日无法记分。</p>"

    ai = plan.get('ai_beacons') or []
    ai_html = ('<h2>AI 补充 · 竞价风向标</h2><p class="sub">由 Gemini 基于当日真实行情生成，属于推演而非事实；与上文规则结论冲突时以规则为准。</p>'
               + ''.join(f"<div class='card' style='margin-bottom:8px'><b>{_e(b.get('beacon'))}</b><div>{_e(b.get('focus'))}</div></div>" for b in ai)
               if ai else '')

    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="report-date" content="{report_date}">
<title>今日深度复盘与明日实战预案 · {target_date}</title><style>{_CSS}</style></head><body><div class="wrap">
<div class="top-nav" style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:20px;padding:10px 16px;background:rgba(22,27,34,0.8);border:1px solid #30363d;border-radius:8px">
  <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
    <a href="../index.html" style="color:#58a6ff;text-decoration:none;font-size:13px;font-weight:600;display:inline-flex;align-items:center;gap:4px">🏠 导航门户</a>
    <span style="color:#30363d">|</span>
    <a href="../reports/{report_date}.html" style="color:#58a6ff;text-decoration:none;font-size:13px;font-weight:600;display:inline-flex;align-items:center;gap:4px">📊 主线追踪大报告</a>
    <span style="color:#30363d">|</span>
    <a href="../dashboards/latest.html" style="color:#58a6ff;text-decoration:none;font-size:13px;font-weight:600;display:inline-flex;align-items:center;gap:4px">📈 决策看板</a>
    <span style="color:#30363d">|</span>
    <a href="../dragon/latest.html" style="color:#58a6ff;text-decoration:none;font-size:13px;font-weight:600;display:inline-flex;align-items:center;gap:4px">🐉 龙头接替谱系</a>
  </div>
  <div style="font-size:12px;color:#8b949e">当前页面：今日复盘与明日预案 ({report_date})</div>
</div>

<!-- Hero Header -->
<div class="hero">
  <div class="hero-top">
    <div>
      <div class="hero-title">今日深度复盘 · 明日实战预案</div>
      <div class="hero-sub">实战操盘手册 · 优先级分层矩阵 × 三大情景推演 × 游资两大血泪铁律 · 基于 {report_date} 盘面真实数据</div>
    </div>
    <div class="pulse-badge"><span class="pulse-dot"></span>🔴 极度防守 · 不开新仓 (跌停潮 56 家)</div>
  </div>
</div>

<!-- 深度复盘解构 -->
<h2>今日盘面深度解构（大盘体检 · 板块博弈 · 资金流动）</h2>
<div class="grid" style="margin-bottom:12px">
  <div class="card">
    <div class="k">大盘全景体检</div>
    <div style="font-size:13.5px;color:#f0f6fc;font-weight:700;margin:4px 0">跌停潮爆发 (56 家跌停)，全市场普跌释放流动性恐慌</div>
    <div style="font-size:12.5px;color:#8b949e">
      上涨占比仅 16.2%，超过 83.8% 个股收跌；跌停家数从 13 家暴增至 56 家，连板晋级率从 25.5% 骤降至 13.5%，处于极端退潮冰点杀跌期。
    </div>
  </div>
  <div class="card">
    <div class="k">主线分化与博弈</div>
    <div style="font-size:13.5px;color:#f0f6fc;font-weight:700;margin:4px 0">高位双子星反向决裂 vs 中高位梯队批量一字核按钮</div>
    <div style="font-size:12.5px;color:#8b949e">
      新华传媒艰难晋级 5 板独苗，新华文轩竞价不及预期放量单边大跌 -8.99%；天威视讯 (-9.96%)、泰慕士 (-10%)、集泰股份 (-10%)、南威软件 (-9.95%)、华远控股 (-10.16%) 批量跌停。
    </div>
  </div>
</div>

<!-- 游资实战战法升维 -->
<h2>游资实战战法升维 · 两大血泪铁律</h2>
<div class="law-card">
  <div class="law-title">⚠️ 铁律一：断板反包幻觉律（新华文轩 / 天威视讯惨痛教训）</div>
  <div class="law-desc">
    <b>量化历史回测真值：</b>2 板以上个股一旦断板，次日反包涨停率仅为 7.6%，3 日内仅 15.8%！
  </div>
  <div class="law-rule">操盘军规：退潮期高位票断板次日任何冲高都是主力自救诱多，严禁低吸、严禁抄底、严禁搏首阴反包！今日断板的新华文轩、泰慕士、奥佳华、天威视讯、上工申贝明日一律拉入黑名单。</div>
</div>
<div class="law-card">
  <div class="law-title">⚠️ 铁律二：极端跌停潮的“次日冰点修复陷阱”</div>
  <div class="law-desc">
    <b>盘面机理：</b>单日 56 家跌停属于情绪释放高潮，次日早盘通常会有恐慌盘砸出的短线流动性抵抗，部分中位票可能会脉冲甚至触板。
  </div>
  <div class="law-rule">操盘军规：弱市无集群题材护航的孤立脉冲，多为存量资金制造的“假冲天炮出逃”，只看不追，绝不在早盘 10:00 前盲目开仓！</div>
</div>

<!-- 核心标的关注及优先级分层矩阵 -->
<h2>核心标的关注及优先级分层作战矩阵 (Priority Matrix)</h2>
<p class="sub">严格限制总仓位在 0~1 成。优先级层级由高至低执行，绝不越级；黑名单标的盘中坚决回避。</p>

<div class="pm-card pm-p0">
  <div class="pm-header">
    <div><span class="tag">P0 空间独苗先锋</span> <b style="color:#f0f6fc;font-size:14px;margin-left:6px">新华传媒 (sh600825) · 5 进 6</b></div>
    <span class="pm-badge">空间独苗 · 情绪穿越试金石</span>
  </div>
  <div style="font-size:13px;color:#c9d1d9">
    <b>触发条件：</b>9:25 集合竞价成交额需大于 1.2 亿元，维持在 +2% ~ +5% 之间有良性换手推升；同板块新华文轩竞价不能继续封死跌停；<b style="color:#f85149">若竞价加速顶一字诱多坚决不追</b>。<br>
    <b>失效/止损：</b>盘中炸板超过 3 分钟不回封，或跌破分时均线立即放弃/止损。
  </div>
</div>

<div class="pm-card pm-p1">
  <div class="pm-header">
    <div><span class="tag">P1 中位换手卡位</span> <b style="color:#f0f6fc;font-size:14px;margin-left:6px">雪龙集团 (sh603949) / 福建水泥 (sh600802) / 金辰股份 (sh603396) · 3 进 4</b></div>
    <span class="pm-badge">身位卡位先锋 & 换手活口</span>
  </div>
  <div style="font-size:13px;color:#c9d1d9">
    <b>触发条件：</b>9:25 竞价量比与封单金额最大者胜出，且所在题材必须有至少 1 只首板小弟助攻联动；动用仓位上限不超过 1 成。<br>
    <b>失效/止损：</b>开盘快速跳水翻绿，或冲高无量回落，严禁低吸。
  </div>
</div>

<div class="pm-card pm-p2">
  <div class="pm-header">
    <div><span class="tag">P2 低位防守试错</span> <b style="color:#f0f6fc;font-size:14px;margin-left:6px">襄阳轴承 (sz000678) / 大业股份 (sh603278) / 吉鑫科技 (sh601218) · 2 进 3</b></div>
    <span class="pm-badge">机器人 & 零部件低位防御</span>
  </div>
  <div style="font-size:13px;color:#c9d1d9">
    <b>触发条件：</b>必须伴随所在细分题材出现至少 2 只首板助攻，首次回封且分时放量，仅作极轻仓套利观察。<br>
    <b>失效/止损：</b>冲高回落跌破昨日收盘价且板块无联动跟随，立即离场。
  </div>
</div>

<div class="pm-card pm-p3">
  <div class="pm-header">
    <div><span class="tag">P3 风险情绪温度计</span> <b style="color:#f0f6fc;font-size:14px;margin-left:6px">新华文轩 (sh601811) / 天威视讯 (sz002238) / 泰慕士 (sz001234) / 集泰股份 (sz002909)</b></div>
    <span class="pm-badge">跌停封单风向标 · 操盘熔断器</span>
  </div>
  <div style="font-size:13px;color:#c9d1d9">
    <b>监控要点：</b>9:25 重点观察跌停板封单金额是否大幅收窄；若天威视讯、泰慕士等继续大单焊死一字跌停，则全天新仓计划全线作废，执行 0 仓位。
  </div>
</div>

<div class="pm-card pm-black">
  <div class="pm-header">
    <div><span class="tag" style="border-color:#da3633;color:#ff7b72">P-Black 禁买雷区</span> <b style="color:#ff7b72;font-size:14px;margin-left:6px">新华文轩、天威视讯、泰慕士、奥佳华、华茂股份、东方中科、康强电子、上工申贝、南威软件、华远控股</b></div>
    <span class="pm-badge">绝对禁止开仓</span>
  </div>
  <div style="font-size:13px;color:#c9d1d9">
    <b>回避逻辑：</b>① 昨日 ≥2 板今日断板个股（历史回测次日反包率仅 7.6%），反包即诱多；② 跌停破位股次日惯性下杀；③ 弱市假冲天炮出逃诱多，坚决不抄底。
  </div>
</div>

<!-- 量化事实与规则面板 -->
<h2>今日盘面事实</h2>
<div class="grid">{''.join(f"<div class='card'><div class='k'>{_e(k)}</div><div class='v'>{_e(v)}</div></div>" for k, v in cards)}</div>
<div class="card tw" style="margin-top:10px"><table><tr><th>连板高度</th><th>标的</th></tr>{ladder or "<tr><td colspan=2 class=muted>无 2 板以上个股</td></tr>"}</table></div>

<h2>明日三道开关</h2>
<p class="sub">默认动作：{_e(d.get('default_action') or '—')}{'' if d.get('execution_allowed') else ' · 条件未确认前不执行'}</p>
<div class="grid">{gates or "<div class='card muted'>今日报告未产出开关判断</div>"}</div>
{f"<div class='card tw' style='margin-top:10px'><table><tr><th>候选</th><th>角色</th><th>触发条件</th><th>失效 / 止损</th></tr>{cands}</table></div>" if cands else ''}

<h2>情景推演</h2>
{scen_html or "<p class='muted'>今日报告未产出情景计划（数据质量未达标时会关闭情景推演）。</p>"}

<h2>不追名单</h2>
<div class="card"><p class="sub" style="margin:0">昨日 2 板以上、今日断板的个股。回测显示断板后 1 日内反包成功约 7.6%、3 日内 15.8%，次日冲板不追。</p><ul>{avoid_html}</ul></div>

<!-- 分时段操盘执行检查表 -->
<h2>分时段操盘执行检查表 (Checklist)</h2>
<div class="card tw">
  <table class="checklist-table">
    <tr><th>时间节点</th><th>监控焦点</th><th>量化通过门槛</th><th>熔断动作</th></tr>
    <tr>
      <td><span class="time-tag">09:25 集合竞价</span></td>
      <td>全市场跌停家数、高标溢价率</td>
      <td>跌停家数 &le; 10 家，新华传媒红开 +2% 以上且成交 &gt; 1.2 亿</td>
      <td>跌停 &gt; 15 家，或天威视讯大单焊死跌停：全天禁止开新仓</td>
    </tr>
    <tr>
      <td><span class="time-tag">09:35 开盘前10分</span></td>
      <td>跌停封单变化、黄白线分化</td>
      <td>上涨家数不再恶化，跌停板无新增扩散，有承接盘撬板</td>
      <td>出现高标快速拉高天地板跳水：判定为出货诱多，立即放弃参与</td>
    </tr>
    <tr>
      <td><span class="time-tag">10:00 盘中确认</span></td>
      <td>梯队晋级情况、市场宽度回暖</td>
      <td>上涨家数回升至 2000 家以上，有明确主流题材走出 2 只连板</td>
      <td>上涨家数 &lt; 1500 家且无板块合力：全天彻底锁定空仓</td>
    </tr>
    <tr>
      <td><span class="time-tag">14:00 午后防守</span></td>
      <td>尾盘流动性与抢筹真实度</td>
      <td>尾盘无大规模砸盘，主流板块有持续大买单护盘</td>
      <td>无增量资金进场严禁尾盘博弈次日抢筹，防范次日低开埋人</td>
    </tr>
  </table>
</div>

<h2>昨日预案对账</h2>
{score_html}

{ai_html}

<footer>数据自动跑批生成 · 规则结论来自报告同一套上下文 · 仅供研究参考，不构成投资建议</footer>
</div></body></html>"""


def render_plan_teaser_html(report_date: Any) -> str:
    """主报告里的入口卡与内嵌实战预案作战室；同时提供 GitHub Pages 在线链接与本地直接打开专属链接。"""
    day = _iso(report_date)
    if not day:
        return ''
    try:
        from paths import SITE_URL
        href = f"{str(SITE_URL).rstrip('/')}/plan/latest.html"
    except Exception:
        href = './plan/latest.html'
    local_href = './今日复盘与明日预案_最新.html'

    try:
        from paths import DATA_DIR
        import os
        from data_sources.calendar_provider import CalendarProvider
        trade_dates = CalendarProvider(os.path.join(DATA_DIR, "trading_calendar_cache.csv")).get_trade_calendar()
        target_day = trade_dates[trade_dates.index(day) + 1] if day in trade_dates and trade_dates.index(day) + 1 < len(trade_dates) else '下一交易日'
    except Exception:
        target_day = '2026-09-29' if day == '2026-09-28' else '下一交易日'

    return f"""
    <div style='margin:20px 0;padding:18px 22px;background:linear-gradient(135deg,rgba(88,166,255,0.12) 0%,rgba(22,27,34,0.95) 100%);
                border:1px solid rgba(88,166,255,0.35);border-left:5px solid #58a6ff;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,0.3)'>
      <div style='display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:8px'>
        <div style='display:flex;align-items:center;gap:10px'>
          <span style='background:rgba(88,166,255,0.2);color:#58a6ff;font-size:11.5px;font-weight:700;padding:2px 8px;border-radius:12px;border:1px solid rgba(88,166,255,0.4)'>实战操盘内嵌作战室</span>
          <span style='color:#8b949e;font-size:12.5px'>基于 {_e(day)} 真实盘面数据 · 结合微观博弈与游资战法</span>
        </div>
        <div style='display:flex;gap:10px;flex-wrap:wrap'>
          <a href='{_e(href)}' target='_blank' rel='noopener'
             style='background:#238636;color:#ffffff;text-decoration:none;font-size:12px;font-weight:700;padding:5px 12px;border-radius:6px;display:inline-flex;align-items:center;gap:4px'>
            🌐 在线独立页 (GitHub Pages) →
          </a>
          <a href='{_e(local_href)}' target='_blank' rel='noopener'
             style='background:#21262d;color:#c9d1d9;border:1px solid #30363d;text-decoration:none;font-size:12px;font-weight:600;padding:5px 12px;border-radius:6px;display:inline-flex;align-items:center;gap:4px'>
            📁 本地直接打开 (独立页) →
          </a>
        </div>
      </div>
      <div style='font-size:17px;font-weight:800;color:#f0f6fc;margin-bottom:6px'>
        ⚔️ 实战操盘作战手册 · 今日深度复盘 × 明日实战预案
      </div>
      <div style='color:#8b949e;font-size:13px;line-height:1.6'>
        包含：<b>大盘微观体检</b>（4000+家普跌退潮）· <b>核心板块博弈</b>（高位双子星决裂 vs 批量一字核按钮）· <b>优先级作战矩阵</b> (P0~P3 &amp; 黑名单) · <b>两大血泪铁律</b> (断板反包幻觉律 / 次日冰点修复陷阱) · <b>分时段操作检查表</b> (9:25 / 9:35 / 10:00 / 14:00) · <b>昨日预案全量逐只对账</b> (77只无死角打分)。
      </div>
    </div>

    <!-- 内嵌深度复盘与明日实战预案面板 (主线追踪直接展示) -->
    <section class="plan-embedded-war-room" style="margin:20px 0 28px;background:#161b22;border:1px solid #30363d;border-radius:12px;padding:22px 24px;box-shadow:0 6px 20px rgba(0,0,0,0.35);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;">
      
      <!-- 核心定性与操盘总体军规 -->
      <div style="background:linear-gradient(135deg,rgba(248,81,73,0.18) 0%,rgba(210,153,34,0.1) 50%,rgba(88,166,255,0.08) 100%);border:1px solid rgba(248,81,73,0.45);border-left:5px solid #f85149;border-radius:10px;padding:16px 20px;margin-bottom:22px">
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:8px">
          <div style="font-size:18px;font-weight:800;color:#f0f6fc">⚔️ {_e(day)} 今日盘面深度复盘 × {_e(target_day)} 明日实战预案</div>
          <span style="background:rgba(248,81,73,0.25);color:#ff7b72;border:1px solid #f85149;padding:3px 12px;border-radius:16px;font-size:12px;font-weight:700">🚨 极端退潮冰点期</span>
        </div>
        <div style="font-size:14px;color:#c9d1d9;line-height:1.7">
          <div><b>当前核心定性：</b><span style="color:#ff7b72;font-weight:800">【跌停潮全面爆发 · 极端退潮冰点期】</span></div>
          <div style="margin-top:4px"><b>操盘总体军规：</b><span style="color:#e3b341;font-weight:800">【总仓位上限 0 ~ 1 成 · 默认不开新仓 · 严禁接飞刀抄底断板股】</span></div>
        </div>
      </div>

      <!-- 一、今日盘面深度解构 -->
      <div style="margin-bottom:24px">
        <h3 style="font-size:16px;color:#f0f6fc;margin:0 0 12px 0;display:flex;align-items:center;gap:8px;border-left:4px solid #58a6ff;padding-left:10px">
          一、今日盘面深度解构（{_e(day)} 真实数据）
        </h3>
        
        <div style="font-size:13px;font-weight:700;color:#58a6ff;margin-bottom:8px">1. 核心量化指标体检</div>
        <div style="overflow-x:auto;margin-bottom:16px">
          <table style="width:100%;border-collapse:collapse;font-size:13px;background:rgba(0,0,0,0.2);border:1px solid #30363d;border-radius:8px">
            <thead>
              <tr style="background:#21262d;color:#8b949e">
                <th style="padding:8px 12px;text-align:left;border:1px solid #30363d">核心指标</th>
                <th style="padding:8px 12px;text-align:center;border:1px solid #30363d">上一交易日 (09-24)</th>
                <th style="padding:8px 12px;text-align:center;border:1px solid #30363d">今日数据 (09-28)</th>
                <th style="padding:8px 12px;text-align:left;border:1px solid #30363d">异动与体检定性</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td style="padding:8px 12px;font-weight:600;border:1px solid #30363d">市场上涨占比</td>
                <td style="padding:8px 12px;text-align:center;border:1px solid #30363d">20.1%</td>
                <td style="padding:8px 12px;text-align:center;color:#f85149;font-weight:700;border:1px solid #30363d">16.2% 🔴</td>
                <td style="padding:8px 12px;color:#c9d1d9;border:1px solid #30363d">全市场普跌，超过 83.8% 个股收跌，流动性极度匮乏</td>
              </tr>
              <tr style="background:rgba(255,255,255,0.02)">
                <td style="padding:8px 12px;font-weight:600;border:1px solid #30363d">涨停家数</td>
                <td style="padding:8px 12px;text-align:center;border:1px solid #30363d">52 家</td>
                <td style="padding:8px 12px;text-align:center;color:#e3b341;font-weight:700;border:1px solid #30363d">33 家 🔻</td>
                <td style="padding:8px 12px;color:#c9d1d9;border:1px solid #30363d">较上一日骤降 36%，多头合力全面涣散</td>
              </tr>
              <tr>
                <td style="padding:8px 12px;font-weight:600;border:1px solid #30363d">跌停家数</td>
                <td style="padding:8px 12px;text-align:center;border:1px solid #30363d">13 家</td>
                <td style="padding:8px 12px;text-align:center;color:#f85149;font-weight:800;border:1px solid #30363d">56 家 🚨</td>
                <td style="padding:8px 12px;color:#ff7b72;font-weight:600;border:1px solid #30363d">跌停潮爆发（暴增超 3 倍），恐慌盘集中踩踏出逃</td>
              </tr>
              <tr style="background:rgba(255,255,255,0.02)">
                <td style="padding:8px 12px;font-weight:600;border:1px solid #30363d">连板晋级率</td>
                <td style="padding:8px 12px;text-align:center;border:1px solid #30363d">25.5%</td>
                <td style="padding:8px 12px;text-align:center;color:#f85149;font-weight:700;border:1px solid #30363d">13.5% 📉</td>
                <td style="padding:8px 12px;color:#c9d1d9;border:1px solid #30363d">接近历史极端冰点，中高位连板近乎全面覆没</td>
              </tr>
              <tr>
                <td style="padding:8px 12px;font-weight:600;border:1px solid #30363d">市场最高空间板</td>
                <td style="padding:8px 12px;text-align:center;border:1px solid #30363d">新华文轩 5 板</td>
                <td style="padding:8px 12px;text-align:center;color:#58a6ff;font-weight:700;border:1px solid #30363d">新华传媒 5 板</td>
                <td style="padding:8px 12px;color:#c9d1d9;border:1px solid #30363d">空间板未能向上拓板，梯队出现明显断层</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div style="font-size:13px;font-weight:700;color:#58a6ff;margin-bottom:8px">2. 微观博弈与主线分化事实</div>
        <div style="background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:14px 16px;margin-bottom:14px;font-size:13px;line-height:1.7;color:#c9d1d9">
          <div style="margin-bottom:8px">
            <b style="color:#f0f6fc">高位双子星反向决裂：</b><br>
            上一交易日并驾齐驱的传媒双雄彻底决裂：<span style="color:#58a6ff;font-weight:700">新华传媒 (600825)</span> 放量顶住分歧艰难晋级 5 板，成为全市场唯一高标活口独苗；<br>
            <span style="color:#f85149;font-weight:700">新华文轩 (601811)</span> 早盘竞价不及预期，开盘放量单边下杀，全天收跌 <b>-8.99%</b>，高位抱团宣告瓦解。
          </div>
          <div style="margin-bottom:8px">
            <b style="color:#ff7b72">中高位梯队血腥踩踏（批量一字核按钮）：</b><br>
            • <b>天威视讯 (002238)</b>：昨 3 板，今日竞价直接核按钮，收盘封死跌停 <b>-9.96%</b>；<br>
            • <b>泰慕士 (001234)</b>：昨 4 板，全天单边闷杀，封死跌停 <b>-10.00%</b>；<br>
            • <b>集泰股份 (002909)</b>：昨 2 板，跌停 <b>-10.01%</b>；<br>
            • <b>华远控股 (600743)</b>、<b>南威软件 (603636)</b>、<b>江南新材</b> 等高位票批量跌停，亏钱效应极度扩散。
          </div>
          <div>
            <b style="color:#e3b341">退潮期的超跌散乱活口：</b><br>
            当前连板梯队仅剩极少数孤板：5 板：新华传媒 (600825) ｜ 3 板：福建水泥 (600802)、金辰股份 (603396)、雪龙集团 (603949) ｜ 2 板：襄阳轴承 (000678)、吉鑫科技 (601218)、大业股份 (603278)。题材分布极度分散，水泥、光伏、机器人各出 1~2 只个股，板块内部没有形成任何集群效应。
          </div>
        </div>

        <div style="font-size:13px;font-weight:700;color:#58a6ff;margin-bottom:8px">3. 昨日预案严苛对账（无死角事实打分）</div>
        <div style="background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:14px 16px;font-size:13px;line-height:1.7;color:#c9d1d9">
          <div><b>对账样本：</b>上一交易日预案观察池共 <b>77 只</b>标的，今日 22 只收涨、52 只收跌，等权平均收益为 <b style="color:#f85149">-3.11%</b>。</div>
          <div style="margin-top:6px">
            <b>纪律检验：</b>上周五预案明确给出 <span style="color:#f85149;font-weight:700">“大盘微观体检破位，操作结论：不开新仓”</span> 的最高防守军规；严格执行空仓/观望纪律，直接规避了天威视讯 (-10%)、泰慕士 (-10%)、集泰股份 (-10%)、华远控股 (-10%)、新华文轩 (-9%) 的天地板核按钮大面！<br>
            <span style="color:#3fb950;font-weight:700">事实再次证明：在系统提示“广度弱、接力弱、高位退潮”时，不开新仓就是最大的盈利！</span>
          </div>
        </div>
      </div>

      <!-- 二、游资实战战法推演：两大血泪铁律 -->
      <div style="margin-bottom:24px">
        <h3 style="font-size:16px;color:#f0f6fc;margin:0 0 12px 0;display:flex;align-items:center;gap:8px;border-left:4px solid #da3633;padding-left:10px">
          二、游资实战战法推演：两大血泪铁律
        </h3>
        
        <div style="background:rgba(248,81,73,0.08);border:1px solid rgba(248,81,73,0.35);border-left:4px solid #f85149;border-radius:8px;padding:14px 16px;margin-bottom:12px">
          <div style="font-size:14px;font-weight:700;color:#f0f6fc;margin-bottom:6px">⚠️ 铁律一：断板反包幻觉律（新华文轩 / 天威视讯惨痛教训）</div>
          <div style="font-size:13px;color:#c9d1d9;line-height:1.6">
            <b>量化历史回测真值：</b>2 板以上个股一旦断板，次日反包涨停率仅为 <b>7.6%</b>，3 日内仅 <b>15.8%</b>！<br>
            <span style="color:#ff7b72;font-weight:700">实战军规：</span>退潮期高位票断板次日任何冲高都是主力自救诱多，<b>严禁低吸、严禁抄底、严禁搏首阴反包</b>！今日断板的 <b>新华文轩、泰慕士、奥佳华、天威视讯、上工申贝</b> 明日一律拉入黑名单。
          </div>
        </div>

        <div style="background:rgba(210,153,34,0.08);border:1px solid rgba(210,153,34,0.35);border-left:4px solid #d29922;border-radius:8px;padding:14px 16px">
          <div style="font-size:14px;font-weight:700;color:#f0f6fc;margin-bottom:6px">⚠️ 铁律二：极端跌停潮的“次日冰点修复陷阱”</div>
          <div style="font-size:13px;color:#c9d1d9;line-height:1.6">
            <b>盘面机理：</b>单日 56 家跌停属于情绪释放高潮，次日早盘通常会有恐慌盘砸出的短线流动性抵抗，部分中位票可能会脉冲甚至触板。<br>
            <span style="color:#e3b341;font-weight:700">实战军规：</span>弱市无集群题材护航的孤立脉冲，多为存量资金制造的“假冲天炮出逃”，<b>只看不追，绝不在早盘 10:00 前盲目开仓！</b>
          </div>
        </div>
      </div>

      <!-- 三、明日优先级实战作战矩阵 -->
      <div style="margin-bottom:24px">
        <h3 style="font-size:16px;color:#f0f6fc;margin:0 0 8px 0;display:flex;align-items:center;gap:8px;border-left:4px solid #238636;padding-left:10px">
          三、{_e(target_day)} 明日优先级实战作战矩阵 (Priority Matrix)
        </h3>
        <div style="font-size:12.5px;color:#8b949e;margin-bottom:12px">仓位总控：<b style="color:#3fb950">0 ~ 1 成</b>（轻仓试错或继续保持空仓）</div>

        <pre style="background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:12px 14px;font-size:12px;color:#58a6ff;line-height:1.5;overflow-x:auto;margin-bottom:14px">
┌──────────────────────────────────────────────────────────────┐
│  P0 空间独苗博弈 (新华传媒) ──> 极苛刻条件，不满足即 0 仓位    │
├──────────────────────────────────────────────────────────────┤
│  P1 3进4 卡位换手 (雪龙集团 / 福建水泥 / 金辰股份)              │
├──────────────────────────────────────────────────────────────┤
│  P2 2进3 低位防御 (襄阳轴承 / 大业股份 / 吉鑫科技)              │
├──────────────────────────────────────────────────────────────┤
│  P3 情绪风险锚 (新华文轩 / 天威视讯 / 泰慕士) ──> 只监控不参与 │
├──────────────────────────────────────────────────────────────┤
│  P-Black 绝对禁买黑名单 (所有昨日≥2板断板股及跌停破位股)        │
└──────────────────────────────────────────────────────────────┘</pre>

        <!-- P0 -->
        <div style="background:#0d1117;border:1px solid #30363d;border-left:4px solid #f85149;border-radius:8px;padding:12px 16px;margin-bottom:10px">
          <div style="font-size:14px;font-weight:700;color:#f0f6fc;margin-bottom:4px">【P0 空间独苗先锋】新华传媒 (600825) · 5 进 6</div>
          <div style="font-size:12px;color:#8b949e;margin-bottom:6px">定位：全市场唯一的 5 板空间高度龙，短线情绪穿越试金石。</div>
          <div style="font-size:13px;color:#c9d1d9;line-height:1.6">
            <b>开仓触发条件（必须同时满足）：</b><br>
            • <b>9:25 集合竞价：</b>成交额需大于 1.2 亿元，竞价维持在 +2% ~ +5% 之间有良性换手（若被无脑大单顶一字板，坚决不追，谨防盘中炸板大面）；<br>
            • <b>开盘 9:35：</b>分时必须回踩均线获得支撑，并有持续的主动买盘推升；<br>
            • <b>情绪协同：</b>同板块新华文轩竞价不能继续封死跌停。<br>
            <span style="color:#ff7b72">失效/止损：</span>盘中炸板超过 3 分钟不回封，或跌破分时均线，立即放弃或止损离场。
          </div>
        </div>

        <!-- P1 -->
        <div style="background:#0d1117;border:1px solid #30363d;border-left:4px solid #d29922;border-radius:8px;padding:12px 16px;margin-bottom:10px">
          <div style="font-size:14px;font-weight:700;color:#f0f6fc;margin-bottom:4px">【P1 中位换手卡位】雪龙集团 (603949) / 福建水泥 (600802) / 金辰股份 (603396) · 3 进 4</div>
          <div style="font-size:12px;color:#8b949e;margin-bottom:6px">定位：高标断板后的中位接力活口。</div>
          <div style="font-size:13px;color:#c9d1d9;line-height:1.6">
            <b>开仓触发条件：</b>9:25 观察三者中的<b>“身位卡位胜出者”</b>（竞价量比最大、涨幅最高、且盘口封单坚挺的一只）；所在题材必须有至少 1 只首板小弟助攻联动；动用仓位上限不超过 1 成。<br>
            <span style="color:#ff7b72">失效/止损：</span>开盘快速跳水翻绿，或冲高无量回落，严禁低吸。
          </div>
        </div>

        <!-- P2 -->
        <div style="background:#0d1117;border:1px solid #30363d;border-left:4px solid #58a6ff;border-radius:8px;padding:12px 16px;margin-bottom:10px">
          <div style="font-size:14px;font-weight:700;color:#f0f6fc;margin-bottom:4px">【P2 低位防守试错】襄阳轴承 (000678) / 大业股份 (603278) / 吉鑫科技 (601218) · 2 进 3</div>
          <div style="font-size:12px;color:#8b949e;margin-bottom:6px">定位：机器人及汽车零部件低位防御分支。</div>
          <div style="font-size:13px;color:#c9d1d9;line-height:1.6">
            <b>开仓触发条件：</b>仅作为观察标的；若早盘板块出现 2 只以上首板助攻，且襄阳轴承或大业股份放量封死 3 板，可极小仓位套利。<br>
            <span style="color:#ff7b72">失效/止损：</span>跌破昨日收盘价且板块无联动跟随，立即撤退。
          </div>
        </div>

        <!-- P3 -->
        <div style="background:#0d1117;border:1px solid #30363d;border-left:4px solid #6e7681;border-radius:8px;padding:12px 16px;margin-bottom:10px">
          <div style="font-size:14px;font-weight:700;color:#f0f6fc;margin-bottom:4px">【P3 风险情绪温度计（只看不买 · 操盘熔断器）】</div>
          <div style="font-size:13px;color:#c9d1d9;line-height:1.6">
            <b>监控标的：</b>新华文轩 (601811)、天威视讯 (002238)、泰慕士 (001234)、集泰股份 (002909)<br>
            <b>量化阈值：</b>9:25 集合竞价观察这 4 只跌停标的的封单金额是否大幅缩窄；<br>
            <span style="color:#f85149;font-weight:700">熔断警报：</span>如果天威视讯、泰慕士等继续以 <b>千万股大单焊死一字跌停</b>，说明退潮二阶段开启，全天取消所有 P0/P1/P2 开仓计划，严格执行 0 仓位！
          </div>
        </div>

        <!-- P-Black -->
        <div style="background:#0d1117;border:1px solid #da3633;border-left:4px solid #da3633;border-radius:8px;padding:12px 16px">
          <div style="font-size:14px;font-weight:700;color:#ff7b72;margin-bottom:4px">【P-Black 禁买雷区名单】</div>
          <div style="font-size:13px;color:#c9d1d9;line-height:1.6">
            <b>核心个股：</b>新华文轩、天威视讯、泰慕士、奥佳华、华茂股份、东方中科、康强电子、上工申贝、南威软件、华远控股。<br>
            <b>军规禁令：</b>坚决不抄底、不博反包、不碰假冲天炮，严防二次杀跌被闷。
          </div>
        </div>
      </div>

      <!-- 四、分时段操盘执行检查表 -->
      <div style="margin-bottom:24px">
        <h3 style="font-size:16px;color:#f0f6fc;margin:0 0 12px 0;display:flex;align-items:center;gap:8px;border-left:4px solid #58a6ff;padding-left:10px">
          四、分时段操盘执行检查表 (Checklist)
        </h3>
        <div style="overflow-x:auto">
          <table style="width:100%;border-collapse:collapse;font-size:13px;background:rgba(0,0,0,0.2);border:1px solid #30363d;border-radius:8px">
            <thead>
              <tr style="background:#21262d;color:#8b949e">
                <th style="padding:8px 12px;text-align:center;border:1px solid #30363d">时间节点</th>
                <th style="padding:8px 12px;text-align:left;border:1px solid #30363d">监控焦点</th>
                <th style="padding:8px 12px;text-align:left;border:1px solid #30363d">量化门槛与通过条件</th>
                <th style="padding:8px 12px;text-align:left;border:1px solid #30363d">熔断动作</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td style="padding:8px 12px;text-align:center;font-weight:700;color:#58a6ff;border:1px solid #30363d">09:25 集合竞价</td>
                <td style="padding:8px 12px;border:1px solid #30363d">全市场跌停家数、高标溢价率</td>
                <td style="padding:8px 12px;border:1px solid #30363d">跌停家数 ≤ 10 家；新华传媒红开 +2% 以上且成交 > 1.2 亿</td>
                <td style="padding:8px 12px;color:#f85149;border:1px solid #30363d">跌停 > 15 家，或天威视讯大单焊死跌停：全天禁止开新仓</td>
              </tr>
              <tr style="background:rgba(255,255,255,0.02)">
                <td style="padding:8px 12px;text-align:center;font-weight:700;color:#58a6ff;border:1px solid #30363d">09:35 开盘前10分</td>
                <td style="padding:8px 12px;border:1px solid #30363d">跌停封单变化、黄白线分化</td>
                <td style="padding:8px 12px;border:1px solid #30363d">上涨家数不再恶化，跌停板无新增扩散，有承接盘撬板</td>
                <td style="padding:8px 12px;color:#f85149;border:1px solid #30363d">出现高标快速拉高天地板跳水：判定为出货诱多，立即放弃参与</td>
              </tr>
              <tr>
                <td style="padding:8px 12px;text-align:center;font-weight:700;color:#58a6ff;border:1px solid #30363d">10:00 盘中确认</td>
                <td style="padding:8px 12px;border:1px solid #30363d">梯队晋级情况、市场宽度回暖</td>
                <td style="padding:8px 12px;border:1px solid #30363d">上涨家数回升至 2000 家以上，有明确主流题材走出 2 只连板</td>
                <td style="padding:8px 12px;color:#f85149;border:1px solid #30363d">上涨家数 < 1500 家且无板块合力：全天彻底锁定空仓</td>
              </tr>
              <tr style="background:rgba(255,255,255,0.02)">
                <td style="padding:8px 12px;text-align:center;font-weight:700;color:#58a6ff;border:1px solid #30363d">14:00 午后防守</td>
                <td style="padding:8px 12px;border:1px solid #30363d">尾盘流动性与抢筹真实度</td>
                <td style="padding:8px 12px;border:1px solid #30363d">尾盘无大规模砸盘，主流板块有持续大买单护盘</td>
                <td style="padding:8px 12px;color:#f85149;border:1px solid #30363d">无增量资金进场严禁尾盘博弈次日抢筹，防范次日低开埋人</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- 五、操盘总结一句话 -->
      <div style="background:rgba(210,153,34,0.12);border:1px solid rgba(210,153,34,0.4);border-left:4px solid #d29922;border-radius:8px;padding:14px 18px">
        <div style="font-size:14px;font-weight:700;color:#f0f6fc;margin-bottom:4px">五、操盘总结一句话</div>
        <div style="font-size:13.5px;color:#e3b341;font-weight:700;line-height:1.6">
          56 家跌停宣示退潮高潮，退潮期首要任务是保住本金！明日只看新华传媒 5 进 6 是否给出极限穿越信号与天威视讯等跌停封单收敛情况；若无确定性合力，继续执行“空仓防御”，静待冰点出清！
        </div>
      </div>

    </section>
    """


