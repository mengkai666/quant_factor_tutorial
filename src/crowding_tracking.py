"""两个月价格观察的动态新老跟踪；无预填股票名单、无未来收益参与选样。"""
from __future__ import annotations

import hashlib
import json
import math

import pandas as pd

TRACKING_RULE_VERSION = 'crowding-tracking/v1'
DEFAULT_TRACKING_RULES = dict(top_fraction=0.10, admission_days=2, strengthening_days=5,
                              exit_days=2, max_drawdown_pct=15.0)


def tracking_rules(overrides: dict | None = None) -> dict:
    overrides = {} if overrides is None else overrides
    if not isinstance(overrides, dict) or set(overrides) - set(DEFAULT_TRACKING_RULES):
        raise ValueError('跟踪参数必须为对象且只包含已声明字段。')
    rules = {**DEFAULT_TRACKING_RULES, **overrides}
    for key in ('admission_days', 'strengthening_days', 'exit_days'):
        if type(rules[key]) is not int or rules[key] < 1:
            raise ValueError(f'{key} 必须为正整数。')
    for key in ('top_fraction', 'max_drawdown_pct'):
        if type(rules[key]) not in (int, float) or not math.isfinite(rules[key]):
            raise ValueError(f'{key} 必须为有限数值。')
    if not 0 < rules['top_fraction'] <= 1 or not 0 < rules['max_drawdown_pct'] <= 100:
        raise ValueError('排名比例须在(0,1]，回撤阈值须在(0,100]。')
    if rules['strengthening_days'] < rules['admission_days']:
        raise ValueError('强化天数不能短于入池确认天数。')
    return rules


def _version(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode('utf-8')).hexdigest()[:16]


def build_dynamic_tracking(daily: list[dict], mats: dict | None, names: dict,
                           *, window_start: str, requested_date: str,
                           rules: dict | None = None) -> dict:
    """逐日回放：周期是现有核心代理的观察分段，不声称全市场只有一条资金周期。

    入池沿用既有候选资格(5日>4%、20日>8%、近5日至少2日在前15%)，
    再要求候选内相对领先、MA5与回撤门槛、多日确认。退出样本持续留档。
    空截面/个股缺价不计为失败，且打断确认连续性。所有首见日期仅限本窗口。
    """
    rules = tracking_rules(rules)
    rule_id = TRACKING_RULE_VERSION + ':' + _version(rules)
    states, cycles, observations = {}, [], []
    current_cycle = None
    prior_proxy = None
    prior_complete = False
    for observation in daily:
        date, complete = observation['date'], bool(observation['coverage_ok'])
        signal_known = complete and not (observation.get('proxy_state') == '过期' and not observation.get('confirmed_proxy'))
        proxy = observation.get('confirmed_proxy') if signal_known else prior_proxy
        has_candidates = bool(observation.get('candidate_codes'))
        # 竞争→首次确认是同一轮的阶段推进，不把刚确认的核心误归到“旧团”。
        new_cycle = ((current_cycle is None and has_candidates) or
                     (current_cycle is not None and prior_proxy is not None and proxy != prior_proxy))
        if signal_known and new_cycle:
            if current_cycle:
                current_cycle['closed_on'] = date
                current_cycle['status'] = '已切换'
            current_cycle = dict(id=f'price-cycle:{date}', start=date, end=date, closed_on=None,
                                 anchor_code=proxy, anchor_name=names.get(proxy, proxy) if proxy else None,
                                 confirmed_on=date if proxy else None,
                                 kind='核心代理观察' if proxy else '候选竞争观察', status='观察中',
                                 left_censored=not prior_complete, admission_codes=[])
            cycles.append(current_cycle)
        elif signal_known and current_cycle and proxy and prior_proxy is None:
            current_cycle.update(anchor_code=proxy, anchor_name=names.get(proxy, proxy),
                                 confirmed_on=date, kind='核心代理观察')
        if current_cycle:
            current_cycle['end'] = date
            current_cycle['status'] = '观察中' if signal_known else '数据不足 / 保留上一观察段'
        cycle_id = current_cycle['id'] if current_cycle else None
        if signal_known:
            prior_proxy = proxy

        scores = {item['code']: item['score'] for item in observation.get('candidate_scores', [])}
        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        limit = max(1, math.ceil(len(ordered) * rules['top_fraction'])) if ordered else 0
        cutoff = ordered[limit - 1][1] if limit else None
        # 边界同分全部保留，不用代码顺序选择赢家，也不依赖页面前六名。
        leading = {symbol for symbol, score in ordered if cutoff is not None and score >= cutoff}
        metrics = {}
        def metric(symbol):
            if symbol not in metrics:
                if mats is None or date not in mats['price'].index or symbol not in mats['price']:
                    metrics[symbol] = dict(r1=None, r5=None, r20=None, dd20=None, above_ma5=None)
                else:
                    def numeric(key):
                        value = mats[key].at[date, symbol]
                        return float(value) if pd.notna(value) and math.isfinite(float(value)) else None
                    price, ma = mats['price'].at[date, symbol], mats['ma5'].at[date, symbol]
                    metrics[symbol] = {key: numeric(key) for key in ('r1', 'r5', 'r20', 'dd20')}
                    metrics[symbol]['above_ma5'] = bool(price >= ma) if pd.notna(price) and pd.notna(ma) else None
            return metrics[symbol]
        qualified = set()
        if complete:
            for symbol in leading:
                m = metric(symbol)
                if (all(m.get(key) is not None for key in ('r1', 'r5', 'r20', 'dd20', 'above_ma5'))
                        and m['above_ma5'] and m['dd20'] >= -rules['max_drawdown_pct']):
                    qualified.add(symbol)
        for symbol in sorted(qualified - set(states)):
            states[symbol] = dict(code=symbol, name=names.get(symbol, symbol), first_seen=date,
                                  first_admitted=None, first_cycle_id=None, episode_cycle_id=None,
                                  first_seen_cycle_id=cycle_id, last_matched_cycle_id=cycle_id,
                                  last_supported=None, last_evidence_date=None, last_metrics={},
                                  left_censored=not prior_complete, support_streak=0, failed_streak=0,
                                  episode_support_days=0, active=False, status='新生候选',
                                  last_known_status=None,
                                  episodes=[], events=[], cycle_ids=[], ever_recovered=False)
        rows = []
        for symbol in sorted(states):
            s, m = states[symbol], metric(symbol)
            previous_status = s['status']
            evidence_ok = complete and all(m.get(key) is not None for key in ('r1', 'r5', 'r20', 'dd20', 'above_ma5'))
            if not evidence_ok:
                s['support_streak'] = s['failed_streak'] = 0
                status, reason = '数据不足', '截面或连续价格缺失：暂停确认，不视为退出；新老身份沿用最后已知记录。'
            else:
                s['last_evidence_date'], s['last_metrics'] = date, m
                if symbol in qualified:
                    s['last_matched_cycle_id'] = cycle_id
                    s['support_streak'] += 1
                    s['failed_streak'] = 0
                    s['last_supported'] = date
                    if not s['active'] and s['support_streak'] >= rules['admission_days']:
                        recovered = s['first_admitted'] is not None
                        s['active'] = True
                        s['episode_support_days'] = s['support_streak']
                        s['episode_cycle_id'] = cycle_id
                        s['ever_recovered'] = recovered
                        if not recovered:
                            s['first_admitted'], s['first_cycle_id'] = date, cycle_id
                        s['episodes'].append(dict(admitted_on=date, cycle_id=cycle_id, exited_on=None,
                                                  recovery=recovered, entry_metrics=dict(m)))
                        if current_cycle and symbol not in current_cycle['admission_codes']:
                            current_cycle['admission_codes'].append(symbol)
                        s['events'].append(dict(date=date, status='旧团重新入池' if recovered else '确认入池',
                                                reason=f"连续{rules['admission_days']}日满足全部条件；只确认价格跟踪资格。", cycle_id=cycle_id))
                    elif s['active']:
                        s['episode_support_days'] += 1
                    if cycle_id and s['active'] and cycle_id not in s['cycle_ids']:
                        s['cycle_ids'].append(cycle_id)
                    is_old = s['first_admitted'] is not None and (s['ever_recovered'] or s['first_cycle_id'] != cycle_id)
                    strengthened = s['support_streak'] >= rules['strengthening_days']
                    status = (('旧团修复强化' if strengthened else '旧团修复') if s['ever_recovered'] else
                              ('老团强化' if strengthened else '老团延续')) if is_old and s['active'] else (
                        '持续强化' if s['active'] and s['support_streak'] >= rules['strengthening_days'] else
                        '新团跟踪' if s['active'] else '旧团修复待验证' if s['first_admitted'] else '新生候选')
                    reason = f"满足候选内前{rules['top_fraction']:.0%}、MA5及回撤条件；连续支持{s['support_streak']}日。"
                    if s['left_censored'] and not is_old:
                        status = ('存量强化' if s['active'] and s['support_streak'] >= rules['strengthening_days']
                                  else '存量跟踪' if s['active'] else '存量待验证')
                else:
                    s['support_streak'] = 0
                    s['failed_streak'] += 1
                    if s['active'] and s['failed_streak'] >= rules['exit_days']:
                        s['active'] = False
                        s['episodes'][-1]['exited_on'] = date
                    status = '分歧观察' if s['active'] else '已退出' if s['first_admitted'] else '候选未通过'
                    misses = []
                    if symbol not in scores:
                        misses.append('不再满足既有收益/持续性门槛')
                    elif symbol not in leading:
                        misses.append('相对领先排名不足')
                    if not m['above_ma5']:
                        misses.append('跌至MA5下方')
                    if m['dd20'] < -rules['max_drawdown_pct']:
                        misses.append('回撤超限')
                    reason = '；'.join(misses) + f"；连续不满足{s['failed_streak']}日，{rules['exit_days']}日退出。"
            s['status'] = status
            if evidence_ok:
                s['last_known_status'] = status
            if status != previous_status:
                s['events'].append(dict(date=date, status=status, reason=reason, cycle_id=cycle_id))
            identity = ('边界存量' if s['left_censored'] else '旧团' if s['first_admitted'] and
                        (not s['active'] or s['ever_recovered'] or s['first_cycle_id'] != cycle_id) else '新候选/新团')
            pool = 'boundary' if s['left_censored'] else 'old' if identity == '旧团' else 'new'
            # 已退出单独筛选，但保留旧身份；缺数不能伪装为当前合格。
            rows.append(dict(code=symbol, name=s['name'], identity=identity, pool=pool, status=status,
                             last_known_status=s['last_known_status'],
                             first_seen=s['first_seen'], first_admitted=s['first_admitted'],
                             first_cycle_id=s['first_cycle_id'], first_seen_cycle_id=s['first_seen_cycle_id'],
                             matched_cycle_id=s['last_matched_cycle_id'],
                             support_streak=s['support_streak'], failed_streak=s['failed_streak'],
                             episode_support_days=s['episode_support_days'], active=s['active'],
                             evidence_ok=evidence_ok, qualified_today=symbol in qualified,
                             metrics=m if evidence_ok else dict(s['last_metrics']), metrics_as_of=s['last_evidence_date'],
                             last_supported=s['last_supported'], reason=reason))
        rows.sort(key=lambda row: (not row['active'], not row['qualified_today'], row['pool'], row['code']))
        snapshot = dict(date=date, cycle_id=cycle_id, coverage_ok=complete, candidate_count=len(scores),
                        cycle_phase='数据不足' if not signal_known else '核心价格确认' if proxy else '候选竞争',
                        rank_cutoff=cutoff, leading_count=len(leading), qualified_count=len(qualified), rows=rows,
                        rule_id=rule_id)
        snapshot['version'] = _version(snapshot)
        observations.append(snapshot)
        prior_complete = prior_complete or complete
    for s in states.values():
        s.pop('last_metrics')
    return dict(rule_version=TRACKING_RULE_VERSION, rule_id=rule_id, rules=rules,
                window_start=window_start, requested_date=requested_date,
                knowledge_status='reconstructed_from_current_cache', known_at=None,
                cycles=cycles, daily=observations, stocks=list(states.values()),
                limitation='价格代理非真实资金周期；窗口起点强势股为左截断存量，窗口内首见不代表上市以来首次；阈值未校准，不输出交易信号。')
