"""Classified-pool screening replay with matured-label-only rule search.

Current-cache reconstruction, not point-in-time quotes or executable returns.
The hypothesis grid is predeclared; labels never participate in same-day gates.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict

import numpy as np
import pandas as pd

from crowding_tracking import build_dynamic_tracking, tracking_rules

POOLS = ('new', 'old', 'repair', 'boundary')
POOL_NAMES = dict(new='新生 / 新团', old='老团延续', repair='旧团修复 / 再验证', boundary='窗口边界存量')
LABEL = dict(horizon=5, min_return_pct=5.0, max_adverse_pct=10.0,
             description='入池收盘后5个交易日收益≥5%，且期间相对入池收盘最低收益≥-10%')


def hypothesis_grid(overrides=None):
    base = dict(tracking_rules(overrides), min_r5=4.0, min_r20=8.0, min_persistence=2)
    variants = [('baseline', '原追踪条件', {}),
                ('earlier', '单日先行', dict(admission_days=1)),
                ('patient', '三日确认', dict(admission_days=3)),
                ('tight_rank', '候选前5%', dict(top_fraction=.05)),
                ('wide_rank', '候选前20%', dict(top_fraction=.20)),
                ('low_drawdown', '回撤收紧10%', dict(max_drawdown_pct=10.0)),
                ('broad_momentum', '收益门槛放宽', dict(min_r5=2.0, min_r20=5.0)),
                ('strict_momentum', '收益及持续性收紧', dict(min_r5=6.0, min_r20=12.0, min_persistence=3))]
    return [dict(id=key, title=title, rules={**base, **delta}) for key, title, delta in variants]


def _stats(events):
    known = [e for e in events if e['success'] is not None]
    n, wins = len(known), sum(e['success'] for e in known)
    rate = wins / n if n else None
    # A conservative descriptive ranking score, not an independence claim.
    z = 1.96
    lower = ((rate + z*z/(2*n) - z*math.sqrt(rate*(1-rate)/n + z*z/(4*n*n))) /
             (1 + z*z/n)) if n else None
    return dict(samples=n, passed=wins, failed=n-wins, pending=len(events)-n,
                entry_days=len({e['date'] for e in known}), label_rate=rate,
                wilson_lower=lower, latest_label_date=max((e['known_on'] for e in known), default=None))


def choose_rule(events_by_rule, before, *, min_samples=20, min_entry_days=6):
    stats = {key: _stats([e for e in events if e['known_on'] and e['known_on'] < before])
             for key, events in events_by_rule.items()}
    eligible = [key for key, s in stats.items() if s['samples'] >= min_samples and s['entry_days'] >= min_entry_days]
    chosen = max(eligible, key=lambda key: (stats[key]['wilson_lower'], key == 'baseline', stats[key]['samples'], key)) if eligible else 'baseline'
    return dict(rule_id=chosen, status='历史样本择优（未验证交易有效性）' if eligible else '样本不足 / 沿用原条件',
                trained_before=before, **stats[chosen], comparisons=stats)


def _outcome(values, index, dates, complete):
    end = index + LABEL['horizon']
    if end >= len(dates):
        return dict(known_on=None, success=None, future_return_pct=None, future_min_pct=None, outcome_status='尚未满5个交易日')
    segment = values[index:end+1]
    if not all(complete[index:end+1]) or not np.isfinite(segment).all() or (segment <= 0).any():
        return dict(known_on=None, success=None, future_return_pct=None, future_min_pct=None, outcome_status='后续窗口缺数 / 不纳入标签统计')
    changes = (segment[1:] / segment[0] - 1) * 100
    ret, adverse = float(changes[-1]), float(changes.min())
    return dict(known_on=dates[end], success=ret >= LABEL['min_return_pct'] and adverse >= -LABEL['max_adverse_pct'],
                future_return_pct=round(ret, 4), future_min_pct=round(adverse, 4), outcome_status='已到期价格标签（非成交盈亏）')


def build_screening_replay(daily, mats, names, *, requested_date, rules=None, min_samples=20, min_entry_days=6):
    hypotheses = hypothesis_grid(rules)
    if type(min_samples) is not int or min_samples < 1 or type(min_entry_days) is not int or min_entry_days < 1:
        raise ValueError('样本/入池日下限须为正整数')
    if not daily or mats is None:
        return dict(schema='crowding-screening-replay/v1', hypotheses=hypotheses, label=LABEL,
                    windows={}, dates=[], prices={}, top15={}, names={}, limitation='没有全市场价格窗口，不能验选。')
    start6 = (pd.Timestamp(requested_date) - pd.DateOffset(months=6)).strftime('%Y-%m-%d')
    # Include 30 preceding trading rows for audit indicators. No forward filling.
    all_dates = mats['price'].index.tolist()
    first = next((i for i, d in enumerate(all_dates) if d >= start6), len(all_dates))
    offset = max(0, first-30)
    dates = all_dates[offset:]
    columns = mats['price'].columns.tolist()
    arrays = {key: mats[key].reindex(dates).to_numpy(dtype=float) for key in ('price', 'ma5', 'r1', 'r5', 'r20', 'dd20', 'persistence')}
    # Match the existing price-proxy ranking precision, including tied cutoffs.
    raw_score = (mats['r5'].rank(axis=1, pct=True)*40 + mats['r20'].rank(axis=1, pct=True)*40 + mats['persistence']*4).reindex(dates).round(4).to_numpy(dtype=float)
    indices = {d:i for i,d in enumerate(dates)}
    column_index = {c:i for i,c in enumerate(columns)}
    observed = {d['date']:d for d in daily}
    complete = [bool(observed.get(d, {}).get('coverage_ok')) for d in dates]
    top15 = mats['r5'].rank(axis=1, pct=True).ge(.85).reindex(dates).to_numpy()
    # Sparse score union supports audit of every listed code, not just winners.
    score_union = {}
    for i, d in enumerate(dates):
        mask = (arrays['r5'][i] > 2) & (arrays['r20'][i] > 5) & (arrays['persistence'][i] >= 2)
        score_union[d] = {columns[j]:float(raw_score[i,j]) for j in np.flatnonzero(mask & np.isfinite(raw_score[i]))}
    windows = {}
    for months in (2, 6):
        start = (pd.Timestamp(requested_date)-pd.DateOffset(months=months)).strftime('%Y-%m-%d')
        window_days = [d for d in daily if d['date'] >= start]
        if not window_days:
            continue
        baseline = build_dynamic_tracking(window_days, mats, names, window_start=start, requested_date=requested_date, rules=rules)
        base_days = {d['date']:d for d in baseline['daily']}
        pools_at = {}
        for d in baseline['daily']:
            pools_at[d['date']] = {
                r['code']: ('repair' if r['first_admitted'] and
                            (not r['active'] or '修复' in r['last_known_status']) else r['pool'])
                for r in d['rows']}
        # Already-qualified at the left boundary is not a newly born opportunity,
        # including candidates seen only by a wider hypothesis.
        i0 = indices[window_days[0]['date']]
        boundary_codes = {columns[j] for j in np.flatnonzero(
            (arrays['r5'][i0] > 2) & (arrays['r20'][i0] > 5) &
            (arrays['persistence'][i0] >= 2))} if window_days[0]['coverage_ok'] else set()
        for pools in pools_at.values():
            for code in boundary_codes:
                pools.setdefault(code, 'boundary')

        events = {h['id']:[] for h in hypotheses}
        snapshots = {h['id']:{} for h in hypotheses}
        for h in hypotheses:
            rule, state = h['rules'], {}
            for observation in window_days:
                date = observation['date']; i = indices[date]
                known = (np.isfinite(arrays['r1'][i]) & np.isfinite(arrays['r5'][i]) & np.isfinite(arrays['r20'][i]) & np.isfinite(arrays['dd20'][i]) & np.isfinite(arrays['ma5'][i]) & (arrays['price'][i]>0))
                eligible = known & (arrays['r5'][i]>rule['min_r5']) & (arrays['r20'][i]>rule['min_r20']) & (arrays['persistence'][i]>=rule['min_persistence'])
                eligible = eligible & np.isfinite(raw_score[i]) if observation['coverage_ok'] else np.zeros(len(columns), dtype=bool)
                ordered = np.sort(raw_score[i, eligible])[::-1]
                count = len(ordered); k = max(1, math.ceil(count*rule['top_fraction'])) if count else 0
                cutoff = float(ordered[k-1]) if k else None
                qualified = eligible & (raw_score[i] >= cutoff if cutoff is not None else False) & ((arrays['price'][i]>=arrays['ma5'][i]) | np.isclose(arrays['price'][i], arrays['ma5'][i], rtol=1e-12, atol=1e-12)) & (arrays['dd20'][i]>=-rule['max_drawdown_pct'])
                for j in np.flatnonzero(qualified):
                    state.setdefault(columns[j], dict(active=False, streak=0, failed=0, ever=False))
                selected, support = {}, {}
                for symbol, s in state.items():
                    j = column_index[symbol]
                    if not observation['coverage_ok'] or not known[j]:
                        s['streak'] = s['failed'] = 0
                    elif qualified[j]:
                        s['streak'] += 1; s['failed'] = 0
                        if not s['active'] and s['streak'] >= rule['admission_days']:
                            pool = pools_at[date].get(symbol, 'new')
                            event = dict(code=symbol, date=date, pool=pool, rule_id=h['id'], recovery=s['ever'],
                                         r5=float(arrays['r5'][i,j]), r20=float(arrays['r20'][i,j]), dd20=float(arrays['dd20'][i,j]),
                                         **_outcome(arrays['price'][:,j], i, dates, complete))
                            events[h['id']].append(event)
                            s['active'] = s['ever'] = True
                        support[symbol] = s['streak']
                        if s['active']:
                            selected[symbol] = s['streak']
                    else:
                        s['streak'] = 0; s['failed'] += 1
                        if s['failed'] >= rule['exit_days']:
                            s['active'] = False
                snapshots[h['id']][date] = dict(cutoff=cutoff, candidates=count, selected=selected, support=support)
        per_pool = {pool:{key:[e for e in value if e['pool']==pool] for key,value in events.items()} for pool in POOLS}
        validation_index = max(1, math.floor(len(window_days)*.8))
        holdout_start = window_days[validation_index]['date'] if validation_index < len(window_days) else None
        frozen = {pool:choose_rule(per_pool[pool], holdout_start, min_samples=min_samples, min_entry_days=min_entry_days) for pool in POOLS} if holdout_start else {}
        replay_days = []
        for observation in window_days:
            date = observation['date']
            choices = {pool:choose_rule(per_pool[pool], date, min_samples=min_samples, min_entry_days=min_entry_days) for pool in POOLS}
            selected, supports = {}, {}
            for pool in POOLS:
                snap = snapshots[choices[pool]['rule_id']][date]
                for symbol, streak in snap['support'].items():
                    if pools_at[date].get(symbol, 'new') == pool:
                        supports[symbol] = streak
                for symbol, streak in snap['selected'].items():
                    if pools_at[date].get(symbol, 'new') == pool:
                        selected[symbol] = dict(pool=pool, rule_id=choices[pool]['rule_id'], support=streak)
            replay_days.append(dict(date=date, coverage_ok=observation['coverage_ok'], coverage=observation.get('coverage',0),
                                    cycle_id=base_days[date]['cycle_id'], choices=choices, pools=pools_at[date],
                                    selected=selected, support=supports,
                                    cutoffs={h['id']:{k:v for k,v in snapshots[h['id']][date].items() if k in ('cutoff','candidates')} for h in hypotheses}))
        validation = {}
        for pool, policy in frozen.items():
            key = policy['rule_id']
            held = [e for e in per_pool[pool][key] if e['date'] >= holdout_start]
            validation[pool] = dict(policy=policy, **_stats(held), baseline=_stats([e for e in per_pool[pool]['baseline'] if e['date'] >= holdout_start]))
        windows[str(months)] = dict(months=months, requested_start=start, actual_start=window_days[0]['date'],
                                    missing_prefix=bool(all_dates and all_dates[0]>start), observations=len(window_days),
                                    complete_days=sum(d['coverage_ok'] for d in window_days), cycles=baseline['cycles'],
                                    days=replay_days, events=events, holdout_start=holdout_start, validation=validation,
                                    min_samples=min_samples, min_entry_days=min_entry_days,
                                    pool_definition='当天原追踪身份为共同参照；已入过池但退出或修复的个股归修复池；左边界既有收益候选为存量。研究参数不改写参照身份。')
    serialized_prices = {c:[float(v) if np.isfinite(v) and v>0 else None for v in arrays['price'][:,j]] for j,c in enumerate(columns)}
    result = dict(schema='crowding-screening-replay/v1', requested_date=requested_date, hypotheses=hypotheses,
                  label=LABEL, dates=dates, complete=complete, prices=serialized_prices,
                  top15={c:''.join('1' if x else '0' for x in top15[:,j]) for j,c in enumerate(columns)},
                  scores=score_union, names={c:names.get(c,c) for c in columns}, windows=windows,
                  knowledge_status='reconstructed_from_current_cache', known_at=None,
                  limitation='前复权当前缓存重建；存在复权修订/存活样本偏差。价格标签非交易胜率；同股重复及同期事件相关，8组比较可能过拟合。未检验量能、开盘成交、涨跌停、T+1及费用。窗口不足不伪造半年；研究条件不替换原追踪池。')
    result['config_id'] = hashlib.sha256(json.dumps(dict(hypotheses=hypotheses, label=LABEL, numeric_policy='ma5-relative-tolerance-1e-12'), sort_keys=True).encode()).hexdigest()[:16]
    # Hash only causal per-day policy artifacts, not current-cache future prices/labels.
    for window in windows.values():
        for d in window['days']:
            d['version'] = hashlib.sha256(json.dumps(dict(config_id=result['config_id'], day=d), sort_keys=True, allow_nan=False).encode()).hexdigest()[:16]
    return result


def audit_stock(replay, months, date, symbol):
    """Same-day audit for ANY code in the price universe, including never-selected codes."""
    window = replay['windows'][str(months)]
    snapshot = next(d for d in window['days'] if d['date']==date)
    if symbol not in replay['prices']:
        return dict(code=symbol, status='未覆盖代码', selected=False, gates=[])
    pool = snapshot['pools'].get(symbol, 'new')
    policy = snapshot['choices'][pool]
    rule = next(h['rules'] for h in replay['hypotheses'] if h['id']==policy['rule_id'])
    index = replay['dates'].index(date)
    values = replay['prices'][symbol]
    seq = values[max(0,index-20):index+1]
    evidence = len(seq)==21 and all(v is not None and v>0 for v in seq)
    metrics = dict(r5=None, r20=None, dd20=None, persistence=None, above_ma5=None)
    if evidence:
        price = values[index]
        metrics = dict(r5=(price/values[index-5]-1)*100, r20=(price/values[index-20]-1)*100,
                       dd20=(price/max(values[index-19:index+1])-1)*100,
                       persistence=replay['top15'][symbol][index-4:index+1].count('1'),
                       above_ma5=bool(price>=sum(values[index-4:index+1])/5 or math.isclose(price, sum(values[index-4:index+1])/5, rel_tol=1e-12, abs_tol=1e-12)))
    cutoff = snapshot['cutoffs'][policy['rule_id']]['cutoff']
    score = replay['scores'].get(date,{}).get(symbol)
    support = snapshot['support'].get(symbol,0)
    selected = symbol in snapshot['selected']
    gates = [dict(label='当日全市场截面覆盖', passed=snapshot['coverage_ok']),
             dict(label='个股连续21个交易日价格完整', passed=evidence)]
    tests = [('5日收益门槛', metrics['r5'] is not None and metrics['r5']>rule['min_r5']),
             ('20日收益门槛', metrics['r20'] is not None and metrics['r20']>rule['min_r20']),
             ('近5日前15%持续性', metrics['persistence'] is not None and metrics['persistence']>=rule['min_persistence']),
             ('候选相对领先排名', score is not None and cutoff is not None and score>=cutoff),
             ('收盘不低于MA5', metrics['above_ma5'] is True),
             ('20日高点回撤', metrics['dd20'] is not None and metrics['dd20']>=-rule['max_drawdown_pct']),
             ('连续确认 / 已在池后继续达标', selected or support>=rule['admission_days'])]
    gates += [dict(label=label, passed=bool(passed) if evidence and snapshot['coverage_ok'] else None) for label,passed in tests]
    return dict(code=symbol, name=replay['names'][symbol], date=date, pool=pool, policy=policy, rules=rule,
                selected=selected, support=support, metrics=metrics, gates=gates,
                status='数据不足 / 不能确认' if not evidence or not snapshot['coverage_ok'] else '当日可选中（价格条件）' if selected else '当日未选中',
                retrospective=_outcome(np.array([np.nan if v is None else v for v in values]), index, replay['dates'], replay['complete']))
