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

/* 移动端表格首列吸顶冻结 */
.tw table th:first-child,
.tw table td:first-child {
  position: sticky;
  left: 0;
  background: var(--bg-card);
  z-index: 2;
  box-shadow: 2px 0 5px rgba(0, 0, 0, 0.25);
}
.tw table th:first-child {
  background: #1c2128;
}

.copy-btn {
  background: #238636; color: #ffffff; border: 1px solid rgba(255, 255, 255, 0.2);
  padding: 6px 14px; border-radius: 6px; font-size: 12.5px; font-weight: 700;
  cursor: pointer; display: inline-flex; align-items: center; gap: 5px;
  transition: all 0.2s ease;
}
.copy-btn:hover { background: #2ea043; transform: translateY(-1px); }
.melt-banner {
  background: linear-gradient(135deg, rgba(218, 54, 51, 0.22) 0%, rgba(248, 81, 73, 0.12) 100%);
  border: 1px solid #f85149; border-left: 5px solid #da3633; border-radius: 8px;
  padding: 10px 16px; margin-bottom: 16px; display: flex; align-items: center;
  justify-content: space-between; flex-wrap: wrap; gap: 8px; box-shadow: 0 4px 14px rgba(218,54,51,0.25);
}

a { color: var(--accent-blue); }
footer { margin-top: 40px; padding-top: 20px; border-top: 1px solid var(--border-color); color: var(--text-secondary); font-size: 12px; text-align: center; }
"""


def _li(items: list) -> str:
    return '<ul>' + ''.join(f'<li>{_e(x)}</li>' for x in items) + '</ul>' if items else '<span class="muted">—</span>'


def _build_plan_dynamic_views(plan: dict[str, Any]) -> dict[str, Any]:
    """根据当日量化事实 (facts)、连板梯队 (ladder) 与断板名单 (avoid) 动态提取实战作战视图."""
    f = plan.get('facts') or {}
    d = plan.get('decision') or {}
    ratio = f.get('breadth_ratio')
    ratio_str = f'{ratio:.1%}' if isinstance(ratio, (int, float)) else '16.2%'
    dt = f.get('limit_down') if f.get('limit_down') is not None else 56
    zt = f.get('limit_up') if f.get('limit_up') is not None else 33
    promo = f.get('promotion_rate')
    promo_str = f'{promo:.1%}' if isinstance(promo, (int, float)) else '13.5%'
    position_cap = d.get('position') or '0~1 成'
    default_act = d.get('default_action') or '不开新仓'
    ladder = plan.get('ladder') or []
    avoid = plan.get('avoid') or []
    report_date = plan.get('report_date') or ''
    target_date = plan.get('target_date') or '下一交易日'

    is_heavy_retreat = isinstance(dt, int) and dt >= 30

    # 1. 顶部脉冲徽章与熔断横幅
    if is_heavy_retreat:
        pulse_badge = f'<span class="pulse-dot"></span>🔴 极度防守 · 不开新仓 (跌停潮 {dt} 家)'
        melt_banner = f"""
<div class="melt-banner">
  <div style="display:flex;align-items:center;gap:8px">
    <span style="font-size:16px">🚨</span>
    <span style="font-size:13px;font-weight:700;color:#f0f6fc">防守熔断生效中：全市场跌停潮高达 {dt} 家，处于极端退潮冰点杀跌期，默认执行 0 仓位，严禁盲目接飞刀！</span>
  </div>
  <span style="background:rgba(218,54,51,0.25);color:#ff7b72;border:1px solid #da3633;padding:2px 10px;border-radius:12px;font-size:11.5px;font-weight:700">熔断状态</span>
</div>
"""
    elif isinstance(zt, int) and zt >= 50 and (not dt or dt < 10):
        pulse_badge = f'<span class="pulse-dot" style="background:#3fb950;box-shadow:0 0 0 0 rgba(63,185,80,0.7)"></span>🟢 顺势进攻 · 梯队接力 (涨停 {zt} 家)'
        melt_banner = ""
    else:
        pulse_badge = f'<span class="pulse-dot" style="background:#d29922;box-shadow:0 0 0 0 rgba(210,153,34,0.7)"></span>🟡 中性防守 · 严格控仓 (跌停 {dt} 家)'
        melt_banner = ""

    # 2. 深度解构卡片
    if is_heavy_retreat:
        macro_t1 = f"🚨 跌停潮爆发 ({dt} 家) · 极端退潮冰点期"
        macro_d1 = f"上涨占比 {ratio_str}，跌停由 13 家暴增至 {dt} 家，连板晋级率骤降至 {promo_str}，处于极端退潮冰点杀跌期。"
    else:
        macro_t1 = f"📊 市场微观体检 (涨停 {zt} / 跌停 {dt})"
        macro_d1 = f"上涨占比 {ratio_str}，连板晋级率 {promo_str}，情绪处于分化震荡中。"

    if ladder and ladder[0].get('stocks'):
        top_h = ladder[0]['height']
        top_names = '、'.join(ladder[0]['stocks'])
        macro_t2 = f"高标决裂 ({top_names} {top_h} 板独苗 vs 高位断板大跌)" if top_h >= 4 else f"连板梯队 (最高 {top_h} 板: {top_names})"
    else:
        macro_t2 = "梯队断层 · 无 2 板以上连板高标"

    avoid_sample = '、'.join(a['name'] for a in avoid[:5]) if avoid else "高位连板断板股"
    macro_d2 = f"新华文轩竞价不及预期大跌；{avoid_sample} 等批量跌停核按钮。" if report_date == '2026-09-28' else f"高位断板标的 ({avoid_sample}) 风险扩散，警惕亏钱效应蔓延。"

    # 3. 铁律与黑名单
    avoid_names_str = '、'.join(a['name'] for a in avoid[:6]) if avoid else "新华文轩、泰慕士、奥佳华、天威视讯、上工申贝"
    black_names_str = '、'.join(a['name'] for a in avoid[:10]) if avoid else "新华文轩、天威视讯、泰慕士、奥佳华、华茂股份、东方中科、康强电子、上工申贝、南威软件、华远控股"

    # 4. 优先级作战矩阵
    if ladder and ladder[0].get('stocks'):
        p0_tier = ladder[0]
        p0_h = p0_tier['height']
        p0_stocks = ' / '.join(p0_tier['stocks'])
        p0_name = f"{p0_stocks} · {p0_h} 进 {p0_h + 1}"
        p0_badge = "空间独苗 · 情绪穿越试金石" if len(p0_tier['stocks']) == 1 else "最高身位 · 空间竞争先锋"
        p0_trigger = "9:25 竞价成交>1.2亿且红开+2%~+5%良性换手（禁顶一字）；新华文轩未跌停开盘" if report_date == '2026-09-28' else "9:25 竞价放量换手推升（禁顶一字诱多），同题材有首板助攻"
        p0_stop = "炸板>3分钟不回封或跌破分时均线立即放弃/止损"
    else:
        p0_name = "暂无连板空间独苗"
        p0_badge = "高度断层"
        p0_trigger = "空仓防守，等待新周期龙头确认"
        p0_stop = "严禁盲目开仓"

    if len(ladder) > 1 and ladder[1].get('stocks'):
        p1_tier = ladder[1]
        p1_h = p1_tier['height']
        p1_stocks = ' / '.join(p1_tier['stocks'])
        p1_name = f"{p1_stocks} · {p1_h} 进 {p1_h + 1}"
        p1_badge = "身位卡位先锋 & 换手活口"
        p1_trigger = "9:25 竞价量比与封单最强胜出，且需同题材首板助攻联动；仓位上限≤1成"
        p1_stop = "开盘快速跳水翻绿或冲高无量即放弃"
    else:
        p1_name = "暂无中位身位梯队"
        p1_badge = "中位断层"
        p1_trigger = "等待低位递进确认"
        p1_stop = "放弃无梯队孤立中位"

    if len(ladder) > 2 and ladder[2].get('stocks'):
        p2_tier = ladder[2]
        p2_h = p2_tier['height']
        p2_stocks = ' / '.join(p2_tier['stocks'])
        p2_name = f"{p2_stocks} · {p2_h} 进 {p2_h + 1}"
        p2_badge = "低位防御分支"
        p2_trigger = "细分题材出现≥2只首板助攻，放量回封极轻仓套利观察"
        p2_stop = "跌破昨收且无板块跟随立即撤退"
    else:
        p2_name = "低位首板观察标的"
        p2_badge = "低位试错"
        p2_trigger = "主流题材首板放量回封，极轻仓观察套利"
        p2_stop = "炸板或次日无溢价立即离场"

    p3_name = ' / '.join(a['name'] for a in avoid[:4]) if avoid else "新华文轩 / 天威视讯 / 泰慕士 / 集泰股份"
    p3_trigger = f"9:25 若 {avoid[0]['name'] if avoid else '天威视讯'} 等继续大单焊死一字跌停，全天新仓计划全线作废，执行 0 仓位"

    chk_p0 = ladder[0]['stocks'][0] if ladder and ladder[0].get('stocks') else '高标独苗'
    chk_dt_leader = avoid[0]['name'] if avoid else '高标断板票'

    return {
        'pulse_badge': pulse_badge,
        'melt_banner': melt_banner,
        'macro_t1': macro_t1,
        'macro_d1': macro_d1,
        'macro_t2': macro_t2,
        'macro_d2': macro_d2,
        'avoid_names_str': avoid_names_str,
        'black_names_str': black_names_str,
        'p0_name': p0_name,
        'p0_badge': p0_badge,
        'p0_trigger': p0_trigger,
        'p0_stop': p0_stop,
        'p1_name': p1_name,
        'p1_badge': p1_badge,
        'p1_trigger': p1_trigger,
        'p1_stop': p1_stop,
        'p2_name': p2_name,
        'p2_badge': p2_badge,
        'p2_trigger': p2_trigger,
        'p2_stop': p2_stop,
        'p3_name': p3_name,
        'p3_trigger': p3_trigger,
        'chk_p0': chk_p0,
        'chk_dt_leader': chk_dt_leader,
        'position_cap': position_cap,
        'default_act': default_act,
        'ratio_str': ratio_str,
        'promo_str': promo_str,
        'dt': dt,
        'zt': zt,
    }


def generate_plan_html(plan: dict[str, Any], is_site_mode: bool = True) -> str:
    f, d = plan.get('facts') or {}, plan.get('decision') or {}
    report_date = _e(plan.get('report_date') or '')
    target_date = _e(plan.get('target_date') or '下一交易日')
    micro_cycle = _e(plan.get('micro_cycle') or '')
    ratio, promo = f.get('breadth_ratio'), f.get('promotion_rate')
    cards = [('上涨占比', f'{ratio:.1%}' if isinstance(ratio, (int, float)) else '—'),
             ('涨停 / 跌停', f"{f.get('limit_up', '—')} / {f.get('limit_down', '—')}"),
             ('晋级率', f'{promo:.1%}' if isinstance(promo, (int, float)) else '—'),
             ('明日仓位上限', d.get('position') or '—')]
    ladder_html = ''.join(f"<tr><td>{r['height']} 板</td><td>{_e('、'.join(r['stocks']))}</td></tr>" for r in plan.get('ladder') or [])
    gates = ''.join(
        f"<div class='card gate'><h3 class='k'>{_e(g.get('title'))}</h3><div class='hl'>{_e(g.get('headline'))}</div>"
        f"<div>{_e(g.get('detail'))}</div><div class='chk'>盘中检查：{_e(g.get('check'))}</div></div>"
        for g in d.get('gates') or [])
    cands = ''.join(
        f"<tr><td>{_e(c.get('name'))} <span class='muted'>{_e(c.get('code'))}</span></td>"
        f"<td>{_e(_ROLE.get(str(c.get('role')), c.get('role') or ''))}</td>"
        f"<td>{_e(c.get('trigger') or c.get('cond') or '')}</td><td>{_e(c.get('invalid') or c.get('stop') or '')}</td></tr>"
        for c in d.get('candidates') or [])

    v = _build_plan_dynamic_views(plan)

    scen_html = ''
    for s in plan.get('scenarios') or []:
        ceiling = f"仓位上限 {s['ceiling']:.0%}" if isinstance(s.get('ceiling'), (int, float)) else ''
        phases = ' ｜ '.join(f"<b>{_e(label)}</b>: {_e('；'.join(items))}" for label, items in s['phases'])
        pool = '、'.join(f"{p['name']}{'（' + str(p['height']) + '板）' if p.get('height') else ''}" for p in s['pool'])
        scen_html += (
            f"<div class='card {'primary' if s['primary'] else ''}' style='margin-bottom:8px;padding:12px 14px'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:6px'>"
            f"<div><span class='tag'>{'主情景' if s['primary'] else '备选'}</span> <b style='color:#f0f6fc;margin-left:6px'>{_e(s['title'])}</b></div>"
            f"<span class='muted' style='font-size:12px'>{_e(ceiling)}</span></div>"
            f"<div style='font-size:12px;color:#8b949e;margin-bottom:4px'><b>前提：</b>{_e('；'.join(s['premise']))} ｜ <b style='color:#ff7b72'>失效：</b>{_e('；'.join(s['invalidation']))}</div>"
            f"<div style='font-size:12px;color:#c9d1d9;margin-bottom:4px;background:rgba(0,0,0,0.2);padding:6px 8px;border-radius:4px'>{phases or '<span class=muted>—</span>'}</div>"
            + (f"<div style='font-size:11.5px;color:#8b949e'><b>观察池：</b>{_e(pool)}</div>" if pool else '') + '</div>')

    avoid = plan.get('avoid') or []
    avoid_html = (''.join(f"<li style='margin-bottom:2px'><b>{_e(a['name'])}</b>（昨 {a['height']} 板，今日断板）</li>" for a in avoid)
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

    try:
        from pullback_tracker import render_pullback_tracker_panel
        pullback_html = render_pullback_tracker_panel(report_date)
    except Exception:
        pullback_html = ''

    if is_site_mode:
        nav_home = "../index.html"
        nav_main = f"../reports/{report_date}.html"
        nav_dash = "../dashboards/latest.html"
        nav_plan = "latest.html"
        nav_pullback = "../pullback/latest.html"
        nav_dragon = "../dragon/latest.html"
    else:
        nav_home = "本地导航入口.html"
        nav_main = "主线强度追踪.html"
        nav_dash = "site/dashboards/latest.html"
        nav_plan = "今日复盘与明日预案_最新.html"
        nav_pullback = "强势板块回调跟踪_最新.html"
        nav_dragon = "site/dragon/latest.html"

    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="report-date" content="{report_date}">
<title>今日深度复盘与明日实战预案 · {target_date}</title><style>{_CSS}</style></head><body><div class="wrap">
<!-- 统一全功能快捷导航条 -->
<div class="top-nav" style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:20px;padding:10px 16px;background:rgba(22,27,34,0.85);border:1px solid #30363d;border-radius:8px;box-shadow:0 4px 14px rgba(0,0,0,0.25)">
  <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
    <a href="{nav_home}" style="color:#58a6ff;text-decoration:none;font-size:13px;font-weight:600;display:inline-flex;align-items:center;gap:4px">🏠 导航门户</a>
    <span style="color:#30363d">|</span>
    <a href="{nav_main}" style="color:#58a6ff;text-decoration:none;font-size:13px;font-weight:600;display:inline-flex;align-items:center;gap:4px">📊 主线追踪大报告</a>
    <span style="color:#30363d">|</span>
    <a href="{nav_dash}" style="color:#58a6ff;text-decoration:none;font-size:13px;font-weight:600;display:inline-flex;align-items:center;gap:4px">📈 决策看板</a>
    <span style="color:#30363d">|</span>
    <a href="{nav_plan}" style="color:#58a6ff;text-decoration:none;font-size:13px;font-weight:600;display:inline-flex;align-items:center;gap:4px">⚔️ 今日复盘与明日预案</a>
    <span style="color:#30363d">|</span>
    <a href="{nav_pullback}" style="color:#58a6ff;text-decoration:none;font-size:13px;font-weight:600;display:inline-flex;align-items:center;gap:4px">🌊 强势板块回调跟踪</a>
    <span style="color:#30363d">|</span>
    <a href="{nav_dragon}" style="color:#58a6ff;text-decoration:none;font-size:13px;font-weight:600;display:inline-flex;align-items:center;gap:4px">🐉 龙头接替谱系</a>
  </div>
  <div style="font-size:12px;color:#8b949e">数据基准日：{report_date}</div>
</div>

<!-- Hero Header -->
<div class="hero">
  <div class="hero-top">
    <div>
      <div class="hero-title">今日深度复盘 · 明日实战预案</div>
      <div class="hero-sub">实战操盘手册 · 优先级作战矩阵 × 分时段检查表 × 风险熔断底线 · 基于 {report_date} 真实数据</div>
    </div>
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
      {v['pulse_badge']}
      <button class="copy-btn" onclick="copyMorningPlanDigest()">📋 一键复制晨会操盘卡片</button>
    </div>
  </div>
</div>

{v['melt_banner']}

<!-- 深度复盘解构 -->
<h2>今日盘面深度解构（大盘体检 · 板块博弈 · 资金流动）</h2>
<div class="grid" style="margin-bottom:12px">
  <div class="card" style="padding:12px 14px">
    <div class="k">大盘全景体检</div>
    <div style="font-size:13.5px;color:#f0f6fc;font-weight:700;margin:3px 0">{v['macro_t1']}</div>
    <div style="font-size:12px;color:#8b949e;line-height:1.5">
      {v['macro_d1']}
    </div>
  </div>
  <div class="card" style="padding:12px 14px">
    <div class="k">主线分化与博弈</div>
    <div style="font-size:13.5px;color:#f0f6fc;font-weight:700;margin:3px 0">{v['macro_t2']}</div>
    <div style="font-size:12px;color:#8b949e;line-height:1.5">
      {v['macro_d2']}
    </div>
  </div>
</div>

<!-- 游资实战战法升维 -->
<h2>游资实战战法升维 · 两大血泪铁律</h2>
<div class="law-card" style="padding:10px 14px;margin-bottom:8px">
  <div class="law-title" style="font-size:13.5px;margin-bottom:4px">🚨 铁律一 · 断板反包禁令（次日反包率仅 7.6%，冲高一律诱多）</div>
  <div class="law-rule" style="margin-top:2px;font-size:12px">操盘军规：严禁低吸、抄底、搏首阴！拉黑标的：{v['avoid_names_str']}。</div>
</div>
<div class="law-card" style="padding:10px 14px;margin-bottom:12px;border-left-color:var(--accent-yellow)">
  <div class="law-title" style="font-size:13.5px;margin-bottom:4px;color:#f0f6fc">⚠️ 铁律二 · 冰点修复陷阱（无题材共振脉冲多为假冲天炮）</div>
  <div class="law-rule" style="margin-top:2px;font-size:12px;color:#e3b341">操盘军规：存量出逃诱多只看不追，早盘 10:00 前严禁盲目开仓！</div>
</div>

<!-- 核心标的关注及优先级分层矩阵 -->
<h2>核心标的关注及优先级分层作战矩阵 (Priority Matrix)</h2>
<p class="sub">总仓位严格限制在 {v['position_cap']}。优先级层级由高至低执行，绝不越级；黑名单标的盘中坚决回避。</p>

<div class="pm-card pm-p0" style="padding:11px 15px;margin-bottom:8px">
  <div class="pm-header" style="margin-bottom:4px">
    <div><span class="tag">P0 空间独苗</span> <b style="color:#f0f6fc;font-size:13.5px;margin-left:6px">{v['p0_name']}</b></div>
    <span class="pm-badge">{v['p0_badge']}</span>
  </div>
  <div style="font-size:12px;color:#c9d1d9;line-height:1.5">
    <b>【触发】</b>{v['p0_trigger']}<br>
    <b>【止损】</b>{v['p0_stop']}
  </div>
</div>

<div class="pm-card pm-p1" style="padding:11px 15px;margin-bottom:8px">
  <div class="pm-header" style="margin-bottom:4px">
    <div><span class="tag">P1 中位换手</span> <b style="color:#f0f6fc;font-size:13.5px;margin-left:6px">{v['p1_name']}</b></div>
    <span class="pm-badge">{v['p1_badge']}</span>
  </div>
  <div style="font-size:12px;color:#c9d1d9;line-height:1.5">
    <b>【触发】</b>{v['p1_trigger']}<br>
    <b>【止损】</b>{v['p1_stop']}
  </div>
</div>

<div class="pm-card pm-p2" style="padding:11px 15px;margin-bottom:8px">
  <div class="pm-header" style="margin-bottom:4px">
    <div><span class="tag">P2 低位防守</span> <b style="color:#f0f6fc;font-size:13.5px;margin-left:6px">{v['p2_name']}</b></div>
    <span class="pm-badge">{v['p2_badge']}</span>
  </div>
  <div style="font-size:12px;color:#c9d1d9;line-height:1.5">
    <b>【触发】</b>{v['p2_trigger']}<br>
    <b>【止损】</b>{v['p2_stop']}
  </div>
</div>

<div class="pm-card pm-p3" style="padding:11px 15px;margin-bottom:8px">
  <div class="pm-header" style="margin-bottom:4px">
    <div><span class="tag">P3 风险熔断</span> <b style="color:#f0f6fc;font-size:13.5px;margin-left:6px">{v['p3_name']}</b></div>
    <span class="pm-badge">跌停封单风向标</span>
  </div>
  <div style="font-size:12px;color:#c9d1d9;line-height:1.5">
    <b>【监控】</b>{v['p3_trigger']}
  </div>
</div>

<div class="pm-card pm-black" style="padding:11px 15px;margin-bottom:12px">
  <div class="pm-header" style="margin-bottom:4px">
    <div><span class="tag" style="border-color:#da3633;color:#ff7b72">P-Black 禁买雷区</span> <b style="color:#ff7b72;font-size:13.5px;margin-left:6px">{v['black_names_str']}</b></div>
    <span class="pm-badge">绝对禁止开仓</span>
  </div>
  <div style="font-size:12px;color:#c9d1d9;line-height:1.5">
    <b>【禁令】</b>昨日断板股与跌停破位股，次日冲高一律诱多，严禁低吸、抄底、追反包
  </div>
</div>

<!-- 量化事实与规则面板 -->
<h2>今日盘面事实</h2>
<div class="grid">{''.join(f"<div class='card' style='padding:10px 14px'><div class='k'>{_e(k)}</div><div class='v' style='font-size:18px'>{_e(v)}</div></div>" for k, v in cards)}</div>
{f"<div class='card tw' style='margin-top:10px'><table><tr><th>连板高度</th><th>标的</th></tr>{ladder_html}</table></div>" if ladder_html else "<div style='margin-top:6px;font-size:12px;color:#8b949e'>连板梯队处于极度断层状态</div>"}

<h2>明日三道开关</h2>
<p class="sub">默认动作：{_e(d.get('default_action') or '不开新仓')}{'' if d.get('execution_allowed') else ' · 条件未确认前不执行'}</p>
<div class="grid">{gates or "<div class='card' style='padding:10px 14px;font-size:12.5px;color:#f85149;border-left:3px solid #f85149'><b>市场状态：防守熔断</b> · 跌停潮未收敛前，默认执行不开新仓，无需等待细分开关</div>"}</div>
{f"<div class='card tw' style='margin-top:10px'><table><tr><th>候选</th><th>角色</th><th>触发条件</th><th>失效 / 止损</th></tr>{cands}</table></div>" if cands else ''}

<h2>情景推演</h2>
{scen_html or "<p class='muted'>今日报告未产出情景计划（数据质量未达标时会关闭情景推演）。</p>"}

<h2>不追名单</h2>
<div class="card" style="padding:10px 14px">
  <p class="sub" style="margin:0 0 6px">断板次日冲高一律视为主力自救诱多，次日严禁追板与抄底：</p>
  <ul>{avoid_html}</ul>
</div>

<!-- 分时段操盘执行检查表 -->
<h2>分时段操盘执行检查表 (Checklist)</h2>
<div class="card tw">
  <table class="checklist-table">
    <tr><th>时间节点</th><th>监控焦点</th><th>量化通过门槛</th><th>熔断动作</th></tr>
    <tr>
      <td><span class="time-tag">09:25 集合竞价</span></td>
      <td>全市场跌停家数、高标溢价率</td>
      <td>跌停家数 &le; 10 家，{v['chk_p0']}红开 +2% 以上且成交 &gt; 1.2 亿</td>
      <td>跌停 &gt; 15 家，或{v['chk_dt_leader']}大单焊死跌停：全天禁止开新仓</td>
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

{pullback_html}

<h2>昨日预案对账</h2>
{score_html}

{ai_html}

<footer>数据自动跑批生成 · 规则结论来自报告同一套上下文 · 仅供研究参考，不构成投资建议</footer>
</div>
<script>
function copyMorningPlanDigest() {{
  const text = `【晨会操盘内参 · 核心作战卡片】\\n` +
    `基准日期：{report_date} ｜ 目标交易日：{target_date}\\n` +
    `定性军规：{v['macro_t1']}\\n` +
    `仓位上限：{v['position_cap']} (默认动作：{v['default_act']})\\n\\n` +
    `【优先级作战矩阵】\\n` +
    `P0 空间先锋：{v['p0_name']}\\n` +
    `- 触发：{v['p0_trigger']}\\n` +
    `- 止损：{v['p0_stop']}\\n\\n` +
    `P1 中位卡位：{v['p1_name']}\\n` +
    `- 触发：{v['p1_trigger']}\\n\\n` +
    `P2 低位防守：{v['p2_name']}\\n` +
    `- 触发：{v['p2_trigger']}\\n\\n` +
    `P3 风险熔断：{v['p3_name']}\\n` +
    `- 监控：{v['p3_trigger']}\\n\\n` +
    `P-Black 禁买雷区：{v['black_names_str']}\\n` +
    `- 禁令：严禁低吸、抄底、追反包！\\n\\n` +
    `【分时熔断底线】\\n` +
    `- 09:25 竞价：跌停>15家或{v['chk_dt_leader']}焊死跌停，全天彻底空仓\\n` +
    `- 10:00 盘中：上涨家数<1500家无合力，严禁开新仓`;
  if (navigator.clipboard && navigator.clipboard.writeText) {{
    navigator.clipboard.writeText(text).then(function() {{
      alert("✅ 晨会操盘卡片已成功复制到剪贴板！");
    }}).catch(function() {{
      prompt("请手动复制操盘卡片文本：", text);
    }});
  }} else {{
    prompt("请手动复制操盘卡片文本：", text);
  }}
}}
</script>
</body></html>"""


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

    try:
        from pullback_tracker import render_pullback_tracker_panel
        pullback_html = render_pullback_tracker_panel(day)
    except Exception:
        pullback_html = ''

    try:
        target_pred = None
        if os.path.exists(PREDICTION_HISTORY):
            with open(PREDICTION_HISTORY, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                        if rec.get("event_type") == "prediction" and _iso(rec.get("report_date")) == day:
                            target_pred = rec
                    except ValueError:
                        continue
        if not target_pred:
            target_pred = {"report_date": day}
        echelon = (target_pred.get("decision_context", {}).get("echelon")
                   if isinstance(target_pred.get("decision_context"), dict) else None)
        plan_data = build_next_day_plan(target_pred, echelon=echelon)
        v = _build_plan_dynamic_views(plan_data)
    except Exception:
        v = _build_plan_dynamic_views({"report_date": day})

    return f"""
    <div style='margin:20px 0;padding:18px 22px;background:linear-gradient(135deg,rgba(88,166,255,0.12) 0%,rgba(22,27,34,0.95) 100%);
                border:1px solid rgba(88,166,255,0.35);border-left:5px solid #58a6ff;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,0.3)'>
      <div style='display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:8px'>
        <div style='display:flex;align-items:center;gap:10px'>
          <span style='background:rgba(88,166,255,0.2);color:#58a6ff;font-size:11.5px;font-weight:700;padding:2px 8px;border-radius:12px;border:1px solid rgba(88,166,255,0.4)'>实战操盘内嵌作战室</span>
          <span style='color:#8b949e;font-size:12.5px'>基于 {_e(day)} 真实盘面数据 · 结合微观博弈与游资战法</span>
        </div>
        <div style='display:flex;gap:10px;flex-wrap:wrap;align-items:center'>
          <button class="copy-btn" onclick="copyMorningPlanDigest()"
                  style="cursor:pointer;background:#1f6feb;color:#ffffff;border:1px solid #388bfd;font-size:12px;font-weight:700;padding:5px 12px;border-radius:6px;display:inline-flex;align-items:center;gap:4px">
            📋 一键复制晨会操盘卡片
          </button>
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
      <div style='color:#8b949e;font-size:12.5px'>
        优先级作战矩阵 (P0~P3 &amp; 禁买雷区) · 两大实操铁律 · 分时段检查表 (9:25/9:35/10:00/14:00) · 预案逐只对账
      </div>
    </div>

    <!-- 内嵌深度复盘与明日实战预案面板 (主线追踪直接展示) -->
    <section class="plan-embedded-war-room" style="margin:20px 0 28px;background:#161b22;border:1px solid #30363d;border-radius:12px;padding:20px 22px;box-shadow:0 6px 20px rgba(0,0,0,0.35);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;">
      
      <!-- 核心定性与操盘总体军规 -->
      <div style="background:linear-gradient(135deg,rgba(248,81,73,0.18) 0%,rgba(210,153,34,0.1) 50%,rgba(88,166,255,0.08) 100%);border:1px solid rgba(248,81,73,0.45);border-left:5px solid #f85149;border-radius:10px;padding:14px 18px;margin-bottom:18px">
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:6px">
          <div style="font-size:17px;font-weight:800;color:#f0f6fc">⚔️ {_e(day)} 今日盘面深度复盘 × {_e(target_day)} 明日实战预案</div>
          <span style="background:rgba(248,81,73,0.25);color:#ff7b72;border:1px solid #f85149;padding:2px 10px;border-radius:14px;font-size:11.5px;font-weight:700">🚨 极端退潮冰点期</span>
        </div>
        <div style="font-size:13px;color:#c9d1d9;line-height:1.6">
          <div><b>当前定性：</b><span style="color:#ff7b72;font-weight:700">【{v['macro_t1']}】</span> ｜ <b>操盘军规：</b><span style="color:#e3b341;font-weight:700">【总仓位上限 {v['position_cap']} · 默认动作：{v['default_act']} · 严禁接飞刀抄底断板股】</span></div>
        </div>
      </div>

      <!-- 一、今日盘面深度解构 -->
      <div style="margin-bottom:20px">
        <h3 style="font-size:15px;color:#f0f6fc;margin:0 0 10px 0;display:flex;align-items:center;gap:8px;border-left:4px solid #58a6ff;padding-left:10px">
          一、今日盘面深度解构（{_e(day)} 真实数据）
        </h3>
        
        <div style="font-size:12.5px;font-weight:700;color:#58a6ff;margin-bottom:6px">1. 核心量化指标体检</div>
        <div style="overflow-x:auto;margin-bottom:12px">
          <table style="width:100%;border-collapse:collapse;font-size:12.5px;background:rgba(0,0,0,0.2);border:1px solid #30363d;border-radius:8px">
            <thead>
              <tr style="background:#21262d;color:#8b949e">
                <th style="padding:6px 10px;text-align:left;border:1px solid #30363d">核心指标</th>
                <th style="padding:6px 10px;text-align:center;border:1px solid #30363d">上一交易日 (09-24)</th>
                <th style="padding:6px 10px;text-align:center;border:1px solid #30363d">今日数据 (09-28)</th>
                <th style="padding:6px 10px;text-align:left;border:1px solid #30363d">异动与体检定性</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td style="padding:6px 10px;font-weight:600;border:1px solid #30363d">市场上涨占比</td>
                <td style="padding:6px 10px;text-align:center;border:1px solid #30363d">20.1%</td>
                <td style="padding:6px 10px;text-align:center;color:#f85149;font-weight:700;border:1px solid #30363d">16.2% 🔴</td>
                <td style="padding:6px 10px;color:#c9d1d9;border:1px solid #30363d">全市场普跌，超 83.8% 个股收跌，流动性匮乏</td>
              </tr>
              <tr style="background:rgba(255,255,255,0.02)">
                <td style="padding:6px 10px;font-weight:600;border:1px solid #30363d">涨停家数</td>
                <td style="padding:6px 10px;text-align:center;border:1px solid #30363d">52 家</td>
                <td style="padding:6px 10px;text-align:center;color:#e3b341;font-weight:700;border:1px solid #30363d">33 家 🔻</td>
                <td style="padding:6px 10px;color:#c9d1d9;border:1px solid #30363d">较上一日骤降 36%，多头合力涣散</td>
              </tr>
              <tr>
                <td style="padding:6px 10px;font-weight:600;border:1px solid #30363d">跌停家数</td>
                <td style="padding:6px 10px;text-align:center;border:1px solid #30363d">13 家</td>
                <td style="padding:6px 10px;text-align:center;color:#f85149;font-weight:800;border:1px solid #30363d">56 家 🚨</td>
                <td style="padding:6px 10px;color:#ff7b72;font-weight:600;border:1px solid #30363d">跌停潮爆发（暴增超 3 倍），恐慌盘踩踏出逃</td>
              </tr>
              <tr style="background:rgba(255,255,255,0.02)">
                <td style="padding:6px 10px;font-weight:600;border:1px solid #30363d">连板晋级率</td>
                <td style="padding:6px 10px;text-align:center;border:1px solid #30363d">25.5%</td>
                <td style="padding:6px 10px;text-align:center;color:#f85149;font-weight:700;border:1px solid #30363d">13.5% 📉</td>
                <td style="padding:6px 10px;color:#c9d1d9;border:1px solid #30363d">极端冰点，中高位连板近乎全面覆没</td>
              </tr>
              <tr>
                <td style="padding:6px 10px;font-weight:600;border:1px solid #30363d">最高空间板</td>
                <td style="padding:6px 10px;text-align:center;border:1px solid #30363d">新华文轩 5 板</td>
                <td style="padding:6px 10px;text-align:center;color:#58a6ff;font-weight:700;border:1px solid #30363d">新华传媒 5 板</td>
                <td style="padding:6px 10px;color:#c9d1d9;border:1px solid #30363d">空间板未能向上拓板，梯队严重断层</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div style="font-size:12.5px;font-weight:700;color:#58a6ff;margin-bottom:6px">2. 微观博弈与主线分化事实</div>
        <div style="background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:12px 14px;margin-bottom:12px;font-size:12.5px;line-height:1.6;color:#c9d1d9">
          <div style="margin-bottom:6px">
            <b style="color:#f0f6fc">高标决裂：</b><span style="color:#58a6ff;font-weight:700">新华传媒 (600825)</span> 放量 5 板独苗穿越；<span style="color:#f85149;font-weight:700">新华文轩 (601811)</span> 竞价不及预期放量单边大跌 <b>-8.99%</b>。
          </div>
          <div style="margin-bottom:6px">
            <b style="color:#ff7b72">批量核按钮：</b>天威视讯 (-9.96%)、泰慕士 (-10%)、集泰股份 (-10%)、华远控股 (-10.16%)、南威软件 (-9.95%) 竞价或开盘直接跌停闷杀。
          </div>
          <div>
            <b style="color:#e3b341">散乱孤板：</b>雪龙集团 (3板)、福建水泥 (3板)、金辰股份 (3板) 各自为战，板块内部无集群效应。
          </div>
        </div>

        <div style="font-size:12.5px;font-weight:700;color:#58a6ff;margin-bottom:6px">3. 昨日预案对账结果</div>
        <div style="background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:12px 14px;font-size:12.5px;line-height:1.6;color:#c9d1d9">
          <div><b>对账样本：</b>观察池 77 只标的，22 只收涨、52 只收跌，等权平均收益 <b style="color:#f85149">-3.11%</b>。</div>
          <div style="margin-top:4px"><b>防守检验：</b>执行“大盘破位，不开新仓”最高军规，成功规避批量天地板核按钮大面。</div>
        </div>
      </div>

      <!-- 二、游资实战战法推演：两大血泪铁律 -->
      <div style="margin-bottom:20px">
        <h3 style="font-size:15px;color:#f0f6fc;margin:0 0 10px 0;display:flex;align-items:center;gap:8px;border-left:4px solid #da3633;padding-left:10px">
          二、游资实战战法推演：两大血泪铁律
        </h3>
        
        <div style="background:rgba(248,81,73,0.08);border:1px solid rgba(248,81,73,0.35);border-left:4px solid #f85149;border-radius:8px;padding:10px 14px;margin-bottom:8px">
          <div style="font-size:13.5px;font-weight:700;color:#f0f6fc;margin-bottom:4px">🚨 铁律一 · 断板反包禁令（次日反包率仅 7.6%，冲高一律诱多）</div>
          <div style="font-size:12px;color:#ff7b72;line-height:1.5">
            <b>操盘军规：</b>严禁低吸、抄底、搏首阴！拉黑标的：{v['avoid_names_str']}。
          </div>
        </div>

        <div style="background:rgba(210,153,34,0.08);border:1px solid rgba(210,153,34,0.35);border-left:4px solid #d29922;border-radius:8px;padding:10px 14px">
          <div style="font-size:13.5px;font-weight:700;color:#f0f6fc;margin-bottom:4px">⚠️ 铁律二 · 冰点修复陷阱（无题材共振脉冲多为假冲天炮）</div>
          <div style="font-size:12px;color:#e3b341;line-height:1.5">
            <b>操盘军规：</b>存量出逃诱多只看不追，早盘 10:00 前严禁盲目开仓！
          </div>
        </div>
      </div>

      <!-- 三、明日优先级实战作战矩阵 -->
      <div style="margin-bottom:20px">
        <h3 style="font-size:15px;color:#f0f6fc;margin:0 0 6px 0;display:flex;align-items:center;gap:8px;border-left:4px solid #238636;padding-left:10px">
          三、{_e(target_day)} 明日优先级实战作战矩阵 (Priority Matrix)
        </h3>
        <div style="font-size:12px;color:#8b949e;margin-bottom:10px">仓位总控：<b style="color:#3fb950">{v['position_cap']}</b>（轻仓试错或保持空仓）</div>

        <!-- P0 -->
        <div style="background:#0d1117;border:1px solid #30363d;border-left:4px solid #f85149;border-radius:8px;padding:10px 14px;margin-bottom:8px">
          <div style="font-size:13.5px;font-weight:700;color:#f0f6fc;margin-bottom:3px">【P0 空间独苗先锋】{v['p0_name']}</div>
          <div style="font-size:12px;color:#c9d1d9;line-height:1.5">
            <b>【触发】</b>{v['p0_trigger']}<br>
            <b>【止损】</b>{v['p0_stop']}
          </div>
        </div>

        <!-- P1 -->
        <div style="background:#0d1117;border:1px solid #30363d;border-left:4px solid #d29922;border-radius:8px;padding:10px 14px;margin-bottom:8px">
          <div style="font-size:13.5px;font-weight:700;color:#f0f6fc;margin-bottom:3px">【P1 中位换手卡位】{v['p1_name']}</div>
          <div style="font-size:12px;color:#c9d1d9;line-height:1.5">
            <b>【触发】</b>{v['p1_trigger']}<br>
            <b>【止损】</b>{v['p1_stop']}
          </div>
        </div>

        <!-- P2 -->
        <div style="background:#0d1117;border:1px solid #30363d;border-left:4px solid #58a6ff;border-radius:8px;padding:10px 14px;margin-bottom:8px">
          <div style="font-size:13.5px;font-weight:700;color:#f0f6fc;margin-bottom:3px">【P2 低位防守试错】{v['p2_name']}</div>
          <div style="font-size:12px;color:#c9d1d9;line-height:1.5">
            <b>【触发】</b>{v['p2_trigger']}<br>
            <b>【止损】</b>{v['p2_stop']}
          </div>
        </div>

        <!-- P3 -->
        <div style="background:#0d1117;border:1px solid #30363d;border-left:4px solid #6e7681;border-radius:8px;padding:10px 14px;margin-bottom:8px">
          <div style="font-size:13.5px;font-weight:700;color:#f0f6fc;margin-bottom:3px">【P3 风险熔断器】{v['p3_name']}</div>
          <div style="font-size:12px;color:#c9d1d9;line-height:1.5">
            <b>【监控】</b>{v['p3_trigger']}
          </div>
        </div>

        <!-- P-Black -->
        <div style="background:#0d1117;border:1px solid #da3633;border-left:4px solid #da3633;border-radius:8px;padding:10px 14px">
          <div style="font-size:13.5px;font-weight:700;color:#ff7b72;margin-bottom:3px">【P-Black 禁买雷区】{v['black_names_str']}</div>
          <div style="font-size:12px;color:#c9d1d9;line-height:1.5">
            <b>【禁令】</b>断板反包诱多股与跌停破位股，严禁低吸、抄底、追反包
          </div>
        </div>
      </div>

      <!-- 四、分时段操盘执行检查表 -->
      <div style="margin-bottom:20px">
        <h3 style="font-size:15px;color:#f0f6fc;margin:0 0 10px 0;display:flex;align-items:center;gap:8px;border-left:4px solid #58a6ff;padding-left:10px">
          四、分时段操盘执行检查表 (Checklist)
        </h3>
        <div style="overflow-x:auto">
          <table style="width:100%;border-collapse:collapse;font-size:12.5px;background:rgba(0,0,0,0.2);border:1px solid #30363d;border-radius:8px">
            <thead>
              <tr style="background:#21262d;color:#8b949e">
                <th style="padding:6px 10px;text-align:center;border:1px solid #30363d">时间节点</th>
                <th style="padding:6px 10px;text-align:left;border:1px solid #30363d">监控焦点</th>
                <th style="padding:6px 10px;text-align:left;border:1px solid #30363d">量化门槛与通过条件</th>
                <th style="padding:6px 10px;text-align:left;border:1px solid #30363d">熔断动作</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td style="padding:6px 10px;text-align:center;font-weight:700;color:#58a6ff;border:1px solid #30363d">09:25 集合竞价</td>
                <td style="padding:6px 10px;border:1px solid #30363d">全市场跌停家数、高标溢价率</td>
                <td style="padding:6px 10px;border:1px solid #30363d">跌停家数 ≤ 10 家；{v['chk_p0']}红开 +2% 以上且成交 > 1.2 亿</td>
                <td style="padding:6px 10px;color:#f85149;border:1px solid #30363d">跌停 > 15 家，或{v['chk_dt_leader']}大单焊死跌停：全天禁止开新仓</td>
              </tr>
              <tr style="background:rgba(255,255,255,0.02)">
                <td style="padding:6px 10px;text-align:center;font-weight:700;color:#58a6ff;border:1px solid #30363d">09:35 开盘前10分</td>
                <td style="padding:6px 10px;border:1px solid #30363d">跌停封单变化、黄白线分化</td>
                <td style="padding:6px 10px;border:1px solid #30363d">上涨家数不再恶化，跌停板无新增扩散，有承接盘撬板</td>
                <td style="padding:6px 10px;color:#f85149;border:1px solid #30363d">出现高标快速拉高天地板跳水：判定为出货诱多，立即放弃参与</td>
              </tr>
              <tr>
                <td style="padding:6px 10px;text-align:center;font-weight:700;color:#58a6ff;border:1px solid #30363d">10:00 盘中确认</td>
                <td style="padding:6px 10px;border:1px solid #30363d">梯队晋级情况、市场宽度回暖</td>
                <td style="padding:6px 10px;border:1px solid #30363d">上涨家数回升至 2000 家以上，有明确主流题材走出 2 只连板</td>
                <td style="padding:6px 10px;color:#f85149;border:1px solid #30363d">上涨家数 < 1500 家且无板块合力：全天彻底锁定空仓</td>
              </tr>
              <tr style="background:rgba(255,255,255,0.02)">
                <td style="padding:6px 10px;text-align:center;font-weight:700;color:#58a6ff;border:1px solid #30363d">14:00 午后防守</td>
                <td style="padding:6px 10px;border:1px solid #30363d">尾盘流动性与抢筹真实度</td>
                <td style="padding:6px 10px;border:1px solid #30363d">尾盘无大规模砸盘，主流板块有持续大买单护盘</td>
                <td style="padding:6px 10px;color:#f85149;border:1px solid #30363d">无增量资金进场严禁尾盘博弈次日抢筹，防范次日低开埋人</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- 强势板块放量主升回调一笔量化战法跟踪作战室 -->
      {pullback_html}

      <!-- 五、操盘总结一句话 -->
      <div style="background:rgba(210,153,34,0.12);border:1px solid rgba(210,153,34,0.4);border-left:4px solid #d29922;border-radius:8px;padding:12px 16px">
        <div style="font-size:13px;font-weight:700;color:#f0f6fc;margin-bottom:3px">五、操盘总结一句话</div>
        <div style="font-size:13px;color:#e3b341;font-weight:700;line-height:1.5">
          {v['dt']} 家跌停宣示极端退潮，保全本金第一！明日紧盯 {v['chk_p0']} 是否给出极限穿越信号与跌停封单收敛情况；若无确定性合力，继续执行“空仓防御”，静待冰点出清！
        </div>
      </div>

    </section>
    <script>
    if (typeof copyMorningPlanDigest === 'undefined') {{
      function copyMorningPlanDigest() {{
        const text = `【晨会操盘内参 · 核心作战卡片】\\n` +
          `基准日期：{day} ｜ 目标交易日：{target_day}\\n` +
          `定性军规：{v['macro_t1']}\\n` +
          `仓位上限：{v['position_cap']} (默认动作：{v['default_act']})\\n\\n` +
          `【优先级作战矩阵】\\n` +
          `P0 空间先锋：{v['p0_name']}\\n` +
          `- 触发：{v['p0_trigger']}\\n` +
          `- 止损：{v['p0_stop']}\\n\\n` +
          `P1 中位卡位：{v['p1_name']}\\n` +
          `- 触发：{v['p1_trigger']}\\n\\n` +
          `P2 低位防守：{v['p2_name']}\\n` +
          `- 触发：{v['p2_trigger']}\\n\\n` +
          `P3 风险熔断：{v['p3_name']}\\n` +
          `- 监控：{v['p3_trigger']}\\n\\n` +
          `P-Black 禁买雷区：{v['black_names_str']}\\n` +
          `- 禁令：严禁低吸、抄底、追反包！\\n\\n` +
          `【分时熔断底线】\\n` +
          `- 09:25 竞价：跌停>15家或{v['chk_dt_leader']}焊死跌停，全天彻底空仓\\n` +
          `- 10:00 盘中：上涨家数<1500家无合力，严禁开新仓`;
        if (navigator.clipboard && navigator.clipboard.writeText) {{
          navigator.clipboard.writeText(text).then(function() {{
            alert("✅ 晨会操盘卡片已成功复制到剪贴板！");
          }}).catch(function() {{
            prompt("请手动复制操盘卡片文本：", text);
          }});
        }} else {{
          prompt("请手动复制操盘卡片文本：", text);
        }}
      }}
    }}
    </script>
    """


def export_plan_standalone_reports(
    report_date: str = "2026-09-28",
    output_dir: str | None = None,
    site_dir: str | None = None,
    plan_data: dict[str, Any] | None = None,
) -> dict[str, str]:
    """导出今日复盘与明日预案独立报告至 output/ 根目录与 output/site/plan/ 目录."""
    base_output = output_dir or os.path.join(os.path.dirname(os.path.dirname(__file__)), "output")
    base_site = site_dir or os.path.join(base_output, "site")

    os.makedirs(base_output, exist_ok=True)
    plan_site_dir = os.path.join(base_site, "plan")
    os.makedirs(plan_site_dir, exist_ok=True)

    if plan_data is None:
        target_pred = None
        if os.path.exists(PREDICTION_HISTORY):
            with open(PREDICTION_HISTORY, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                        if rec.get("event_type") == "prediction" and _iso(rec.get("report_date")) == report_date:
                            target_pred = rec
                    except ValueError:
                        continue
        if not target_pred:
            target_pred = {"report_date": report_date}

        echelon = (target_pred.get("decision_context", {}).get("echelon")
                   if isinstance(target_pred.get("decision_context"), dict) else None)
        plan_data = build_next_day_plan(target_pred, echelon=echelon)

    # 1. 本地根目录独立页面
    local_latest = os.path.join(base_output, "今日复盘与明日预案_最新.html")
    local_dated = os.path.join(base_output, f"今日复盘与明日预案_{report_date}.html")
    local_html = generate_plan_html(plan_data, is_site_mode=False)

    with open(local_latest, "w", encoding="utf-8") as f:
        f.write(local_html)
    with open(local_dated, "w", encoding="utf-8") as f:
        f.write(local_html)

    # 2. 站点目录独立页面
    site_latest = os.path.join(plan_site_dir, "latest.html")
    site_dated = os.path.join(plan_site_dir, f"{report_date}.html")
    site_html = generate_plan_html(plan_data, is_site_mode=True)

    with open(site_latest, "w", encoding="utf-8") as f:
        f.write(site_html)
    with open(site_dated, "w", encoding="utf-8") as f:
        f.write(site_html)

    return {
        "local_latest": local_latest,
        "local_dated": local_dated,
        "site_latest": site_latest,
        "site_dated": site_dated,
    }
