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
body{margin:0;background:#0d1117;color:#c9d1d9;font:14px/1.65 -apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:22px;margin:0 0 4px;color:#f0f6fc}h2{font-size:16px;margin:28px 0 10px;color:#f0f6fc;border-left:3px solid #58a6ff;padding-left:8px}
.sub{color:#8b949e;font-size:12.5px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 14px;min-width:0}
.k{color:#8b949e;font-size:11.5px}.v{font-size:18px;font-weight:700;color:#f0f6fc}
.gate h3{margin:0 0 4px;font-size:13px}.gate .hl{font-weight:700;color:#f0f6fc}.gate .chk{color:#8b949e;font-size:12.5px;margin-top:6px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:7px 8px;border-bottom:1px solid #21262d;text-align:left;vertical-align:top}
th{color:#8b949e;font-weight:600;font-size:12px}.tw{overflow-x:auto}
.up{color:#f85149}.down{color:#3fb950}.muted{color:#8b949e}.tag{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11.5px;border:1px solid #30363d}
.primary{border-color:#d29922}.primary .tag{color:#d29922;border-color:#d29922}
ul{margin:4px 0 0;padding-left:18px}.phase{margin-top:6px}.phase b{color:#58a6ff;font-weight:600;margin-right:6px}
a{color:#58a6ff}footer{margin-top:36px;color:#8b949e;font-size:12px}
"""


def _li(items: list) -> str:
    return '<ul>' + ''.join(f'<li>{_e(x)}</li>' for x in items) + '</ul>' if items else '<span class="muted">—</span>'


def generate_plan_html(plan: dict[str, Any]) -> str:
    f, d = plan.get('facts') or {}, plan.get('decision') or {}
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
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="report-date" content="{_e(plan.get('report_date'))}">
<title>明日预案 · {_e(plan.get('target_date') or '')}</title><style>{_CSS}</style></head><body><div class="wrap">
<div class="sub"><a href="../reports/{_e(plan.get('report_date'))}.html">← 返回 {_e(plan.get('report_date'))} 主线强度报告</a></div>
<h1>明日预案 · {_e(plan.get('target_date') or '下一交易日')}</h1>
<div class="sub">基于 {_e(plan.get('report_date'))} 收盘数据自动生成 · 每个交易日收盘后随主报告更新 · {_e(plan.get('micro_cycle'))}</div>
<h2>今日盘面事实</h2><div class="grid">{''.join(f"<div class='card'><div class='k'>{_e(k)}</div><div class='v'>{_e(v)}</div></div>" for k, v in cards)}</div>
<div class="card tw" style="margin-top:10px"><table><tr><th>连板高度</th><th>标的</th></tr>{ladder or "<tr><td colspan=2 class=muted>无 2 板以上个股</td></tr>"}</table></div>
<h2>明日三道开关</h2><p class="sub">默认动作：{_e(d.get('default_action') or '—')}{'' if d.get('execution_allowed') else ' · 条件未确认前不执行'}</p>
<div class="grid">{gates or "<div class='card muted'>今日报告未产出开关判断</div>"}</div>
{f"<div class='card tw' style='margin-top:10px'><table><tr><th>候选</th><th>角色</th><th>触发条件</th><th>失效 / 止损</th></tr>{cands}</table></div>" if cands else ''}
<h2>情景推演</h2>{scen_html or "<p class='muted'>今日报告未产出情景计划（数据质量未达标时会关闭情景推演）。</p>"}
<h2>不追名单</h2><div class="card"><p class="sub" style="margin:0">昨日 2 板以上、今日断板的个股。回测显示断板后 1 日内反包成功约 7.6%、3 日内 15.8%，次日冲板不追。</p><ul>{avoid_html}</ul></div>
<h2>昨日预案对账</h2>{score_html}
{ai_html}
<footer>数据自动跑批生成 · 规则结论来自报告同一套上下文 · 仅供研究参考，不构成投资建议</footer>
</div></body></html>"""


def render_plan_teaser_html(report_date: Any) -> str:
    """主报告里的入口卡；用绝对 SITE_URL，主报告的归档副本和 latest 副本都能跳对。"""
    day = _iso(report_date)
    if not day:
        return ''
    try:
        from paths import SITE_URL
        href = f"{str(SITE_URL).rstrip('/')}/plan/latest.html"
    except Exception:
        href = './plan/latest.html'
    return (f"<a href='{_e(href)}' target='_blank' rel='noopener' style='display:block;text-decoration:none;margin:18px 0;"
            f"padding:14px 18px;background:#161b22;border:1px solid #30363d;border-left:4px solid #58a6ff;border-radius:8px;color:#c9d1d9'>"
            f"<div style='color:#58a6ff;font-size:11px;font-weight:700'>独立页面</div>"
            f"<div style='font-size:16px;font-weight:700;color:#f0f6fc;margin:3px 0'>明日预案 · 三道开关 · 情景推演 · 昨日对账</div>"
            f"<div style='font-size:13px'>基于 {_e(day)} 收盘数据 →</div></a>")
