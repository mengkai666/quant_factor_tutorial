# -*- coding: utf-8 -*-
"""每日复盘决策看板生成器 (v1, 数据驱动 6 场景版)

从盘面数据 + timing_signal 分类结果, 一键渲染出人眼可读的策略看板。
本模块只做视图, 不做业务计算; 所有数值来自调用方注入的 ctx dict。

调用位置: src/主线强度追踪.py::main() 内, publish 前后。
产出:     output/复盘决策看板_YYYY-MM-DD.html (单文件、无外部依赖)

ctx 字段约定 (缺失字段自动降级为 '—'):
  date_str        : 'YYYY-MM-DD'  报告口径日期
  scene           : 'E_主升加速'   timing_signal 场景码
  action          : '锁仓主升 / 去弱留强'
  level           : '强进攻 (高潮期)'
  color           : '#ff4444'      场景主色
  position        : '7-9成仓位'
  win_rate        : 0.71           历史 T+3 破新高胜率
  desc            : 场景一句话说明
  # 三因子
  curr_h          : 6              空间板
  prev_h          : 5              昨日空间板
  pressure_5d     : 5              5日压力位
  zt              : 128            涨停家数
  dt              : 8              跌停家数
  ad_ratio        : 0.778          A/D 比
  ladder          : 10             梯队分
  h3/h4/h5/h6p    : int            各高度家数
  # 决策 4 情形 (T+1 情形树)
  scenarios       : list[dict]     可选, 默认套用模板
  # 历史同型样本 (用于底部对照表)
  history_cases   : list[dict]     可选, 默认为空
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any

from report_logic import (
    assess_data_quality,
    build_market_state,
    build_scenario_probabilities,
    build_data_credibility_summary,
    build_lianban_review,
    build_mainline_review,
    compute_ladder_metrics,
    compute_market_ratios,
    compute_mainline_concentration,
    normalize_catalyst,
    sanitize_html_for_policy,
)


def build_dashboard_ctx(timing=None, advance_decline=None, sentiment_df=None,
                        echelon=None, previous_echelon=None, report_date=None, focus_df=None,
                        focus_catalysts=None, report_context=None, regime=None,
                        data_quality=None, ladder_review=None) -> dict:
    """从 timing + 盘面 + focus_pool + 催化归因结果组装看板 ctx.

    focus_catalysts: {股票名: {catalyst: {tag, text, url} | None, raw: {...}}}
      来自 catalyst_attribution.attribute_focus_pool(). None 时看板隐藏催化列.
    """
    """从 timing + 盘面数据组装看板 ctx (供 generate_dashboard_* 使用).

    把因子提取逻辑集中在这里, 避免 main 与 generate_html 两处重复构造.
    任何字段缺失都安全降级, 不抛异常.
    """
    timing = timing or {}
    advance_decline = advance_decline or {}
    unified_context = report_context if isinstance(report_context, dict) else {}
    legacy_regime = dict(regime) if isinstance(regime, dict) else {}
    legacy_quality = dict(data_quality) if isinstance(data_quality, dict) else {}
    legacy_ladder_review = dict(ladder_review) if isinstance(ladder_review, dict) else {}
    try:
        from timing_signal import (
            _compute_ad_ratio, _compute_ladder, _get_5day_pressure, _to_int,
        )
    except Exception:
        # 极端情况: timing_signal 不可用, 用本地兜底
        def _to_int(x, default=0):
            try:
                return int(x)
            except Exception:
                return default

        def _compute_ad_ratio(ad):
            up = _to_int(ad.get('up', 0)); dn = _to_int(ad.get('down', 0))
            tot = up + dn
            return (up / tot if tot >= 1000 else None), up, dn

        def _compute_ladder(ech):
            return None, 0, 0, 0, 0

        def _get_5day_pressure(sdf, prev_h):
            return prev_h

    curr_h = _to_int(advance_decline.get('zt_max_height', 0))
    prev_h = _to_int(advance_decline.get('zt_max_height_prev', 0))
    zt = _to_int(advance_decline.get('zt', 0))
    dt = _to_int(advance_decline.get('dt', 0))
    zt_prev = _to_int(advance_decline.get('zt_prev', 0))
    ad_val, _up, _dn = _compute_ad_ratio(advance_decline)
    ratios = compute_market_ratios(
        advance_decline.get('up', _up),
        advance_decline.get('down', _dn),
        advance_decline.get('market_total'),
        preserve_missing=True,
    )
    ladder, h3, h4, h5, h6p = _compute_ladder(echelon)
    ladder_metrics = compute_ladder_metrics(
        echelon, previous_echelon=previous_echelon,
    )
    concentration_rows = []
    for group in echelon or ():
        if not isinstance(group, dict):
            continue
        height = group.get('height')
        for stock in group.get('stock_details') or ():
            if not isinstance(stock, dict):
                continue
            concentration_rows.append({
                'mainline': stock.get('ml') or stock.get('sub'),
                'height': height,
            })
    mainline_concentration = compute_mainline_concentration(concentration_rows)
    if ladder_metrics.get('height_count'):
        ladder = ladder_metrics.get('ladder', ladder)
        h3 = ladder_metrics.get('h3', h3)
        h4 = ladder_metrics.get('h4', h4)
        h5 = ladder_metrics.get('h5', h5)
        h6p = ladder_metrics.get('h6p', h6p)
    pressure = _get_5day_pressure(sentiment_df, prev_h if prev_h > 0 else curr_h)

    context_report_date = unified_context.get('report_date') if unified_context else None
    date_key = str(report_date or context_report_date) if (report_date or context_report_date) else datetime.now().strftime('%Y-%m-%d')
    if len(date_key) == 8 and date_key.isdigit():
        date_key = f'{date_key[:4]}-{date_key[4:6]}-{date_key[6:]}'

    quality_args = dict(
        report_date=date_key,
        trade_day=advance_decline.get('trade_day', True),
        market_total=advance_decline.get('market_total', ratios.get('market_total', 0)),
        market_covered=advance_decline.get('market_covered', ratios.get('observed', 0)),
        primary_source=advance_decline.get('primary_source', advance_decline.get('source')),
        fallback_source=advance_decline.get('fallback_source'),
        used_fallback=advance_decline.get('used_fallback', False),
        used_stale=advance_decline.get('used_stale', False),
        ad_incomplete=advance_decline.get('ad_incomplete', False),
        ad_up=advance_decline.get('up'),
        ad_down=advance_decline.get('down'),
        ad_flat=advance_decline.get('flat'),
        ad_reconciliation_enabled=advance_decline.get(
            'ad_reconciliation_enabled', False
        ),
        market_scope=advance_decline.get('market_scope', '沪深北全A'),
        missing_fields=advance_decline.get('missing_fields', []),
        errors=advance_decline.get('errors', []),
        data_timestamp=advance_decline.get('data_timestamp'),
        source_timestamp=advance_decline.get('source_timestamp'),
        report_generated_at=advance_decline.get('report_generated_at'),
        max_delay_minutes=advance_decline.get('max_delay_minutes', 30),
        stale_after_minutes=advance_decline.get('stale_after_minutes', 180),
    )
    # 旧调用方没有该字段时保持兼容；生产入口会显式提供真实前缀集合.
    if 'market_prefixes' in advance_decline:
        quality_args['market_prefixes'] = advance_decline.get('market_prefixes') or ()
    historical_samples = advance_decline.get('historical_samples', timing.get('historical_samples', 0))
    historical_stats = advance_decline.get('historical_stats', timing.get('historical_stats'))
    if unified_context:
        quality = dict(unified_context.get('quality') or {})
        market_state = dict((unified_context.get('facts') or {}).get('market_state') or {})
        market_state.setdefault('publication_mode', unified_context.get('publication_mode', 'facts_only'))
        scenario_probabilities = list(unified_context.get('scenarios') or [])
    else:
        if legacy_quality:
            quality = dict(legacy_quality)
            if not quality.get('status'):
                quality['status'] = 'ok' if quality.get('ok') is True else 'degraded'
            quality.setdefault('market_scope', '沪深北全A')
            quality.setdefault(
                'publication_mode',
                'decision' if quality['status'] == 'ok' else 'observation',
            )
        else:
            quality = assess_data_quality(**quality_args)
        scene_hint = legacy_regime.get('code') or timing.get('scene')
        market_state = build_market_state(
            quality, scene=scene_hint, historical_samples=historical_samples,
        )
        if legacy_regime:
            market_state.update(legacy_regime)
            regime_title = legacy_regime.get('title') or legacy_regime.get('label')
            if regime_title:
                market_state['title'] = regime_title
                market_state['label'] = regime_title
                market_state['scene'] = regime_title
        scenario_probabilities = build_scenario_probabilities(
            scene=scene_hint, ad_ratio=ratios.get('breadth_ratio'),
            zt=zt, dt=dt, curr_h=curr_h, pressure_5d=pressure,
            ladder=ladder, h5=h5, data_quality=quality,
            historical_samples=historical_samples,
            historical_stats=historical_stats,
        )
    if not unified_context and legacy_ladder_review:
        merged_ladder = dict(ladder_metrics)
        merged_ladder.update(legacy_ladder_review)
        distribution = legacy_ladder_review.get('distribution')
        if isinstance(distribution, dict):
            normalized_distribution = {}
            for key, value in distribution.items():
                try:
                    normalized_distribution[int(key)] = int(value)
                except (TypeError, ValueError):
                    continue
            if normalized_distribution:
                merged_ladder['distribution'] = normalized_distribution
                merged_ladder['counts'] = normalized_distribution
                merged_ladder['board_counts'] = normalized_distribution
                merged_ladder['height_count'] = sum(normalized_distribution.values())
                merged_ladder['first_board_count'] = normalized_distribution.get(1, 0)
                merged_ladder['second_board_count'] = normalized_distribution.get(2, 0)
                merged_ladder['h3'] = normalized_distribution.get(3, 0)
                merged_ladder['h4'] = normalized_distribution.get(4, 0)
                merged_ladder['h5'] = normalized_distribution.get(5, 0)
                merged_ladder['h6p'] = sum(
                    count for height, count in normalized_distribution.items() if height >= 6
                )
        promotions = legacy_ladder_review.get('promotions')
        if isinstance(promotions, dict):
            first_promotion = promotions.get(1) or promotions.get('1')
            if isinstance(first_promotion, dict):
                eligible = first_promotion.get('eligible')
                advanced = first_promotion.get('advanced')
                rate = first_promotion.get('rate')
                try:
                    rate_text = f'{float(rate) * 100:.1f}%'
                except (TypeError, ValueError):
                    rate_text = '样本不足'
                if advanced is not None and eligible is not None:
                    rate_text += f'（{advanced}/{eligible}）'
                rate_row = {
                    **first_promotion,
                    'successes': advanced,
                    'trials': eligible,
                    'text': rate_text,
                }
                merged_ladder['first_board_to_second'] = rate_row
                advancement_rates = dict(merged_ladder.get('advancement_rates') or {})
                advancement_rates['1_to_2'] = rate_row
                merged_ladder['advancement_rates'] = advancement_rates
        ladder_metrics = merged_ladder
        ladder = ladder_metrics.get('ladder', ladder)
        h3 = ladder_metrics.get('h3', h3)
        h4 = ladder_metrics.get('h4', h4)
        h5 = ladder_metrics.get('h5', h5)
        h6p = ladder_metrics.get('h6p', h6p)
    facts = unified_context.get('facts') if isinstance(unified_context.get('facts'), dict) else {}
    # 主报告已经计算过一套带前后交易日匹配的梯队指标；看板必须复用这套
    # canonical facts，不能再次用可能被裁剪/变形的 echelon 重新计算，
    # 否则会出现同一份报告里“样本不足 0/0”和“真实 10/29”并存。
    canonical_ladder_metrics = facts.get('ladder_metrics')
    if isinstance(canonical_ladder_metrics, dict) and canonical_ladder_metrics:
        ladder_metrics = canonical_ladder_metrics
        ladder = ladder_metrics.get('ladder', ladder)
        h3 = ladder_metrics.get('h3', h3)
        h4 = ladder_metrics.get('h4', h4)
        h5 = ladder_metrics.get('h5', h5)
        h6p = ladder_metrics.get('h6p', h6p)
    data_credibility = facts.get('data_credibility') if isinstance(facts.get('data_credibility'), dict) else None
    if data_credibility is None:
        data_credibility = build_data_credibility_summary(
            quality, report_date=date_key,
            report_generated_at=unified_context.get('generated_at') if unified_context else None,
        )
    if not unified_context and legacy_quality:
        data_credibility = dict(data_credibility)
        for key in ('name_conflicts', 'limit_pool_status', 'limit_pool_source', 'notes'):
            if key in legacy_quality:
                data_credibility[key] = legacy_quality.get(key)
        reasons = [str(item) for item in (data_credibility.get('reasons') or []) if str(item).strip()]
        conflict_count = legacy_quality.get('name_conflicts')
        if isinstance(conflict_count, (int, float)) and conflict_count > 0:
            reasons.append(f'名称冲突 {int(conflict_count)}')
        reasons.extend(str(item) for item in (legacy_quality.get('notes') or []) if str(item).strip())
        data_credibility['reasons'] = list(dict.fromkeys(reasons))
    lianban_review = facts.get('lianban_review') if isinstance(facts.get('lianban_review'), dict) else None
    if lianban_review is None:
        lianban_review = build_lianban_review(ladder_metrics)
    mainline_review = facts.get('mainline_review') if isinstance(facts.get('mainline_review'), dict) else None
    if mainline_review is None:
        mainline_review = build_mainline_review(
            mainline_concentration, limit_up_count=advance_decline.get('zt'),
            attribution_source=(advance_decline.get('sector_source') or 'CLS+Eastmoney concepts'),
        )

    scenarios = _default_scenarios(curr_h, prev_h, focus_df=focus_df)
    by_code = {row['code']: row for row in scenario_probabilities}
    for scenario, code in zip(scenarios, ('A', 'B', 'C', 'D')):
        if code in by_code:
            scenario.update(by_code[code])
    _mark_base_scenario(scenarios)

    return {
        'date_str': date_key,
        'scene': market_state.get('title') or market_state.get('scene') or market_state.get('label') or timing.get('scene'),
        'action': market_state.get('action') or timing.get('action'),
        'level': timing.get('level'),
        'color': market_state.get('color') or timing.get('color'),
        'position': timing.get('position'),
        'win_rate': timing.get('win_rate'),
        'desc': market_state.get('reason') or timing.get('desc'),
        'curr_h': curr_h, 'prev_h': prev_h, 'pressure_5d': pressure,
        'zt': zt, 'dt': dt, 'zt_prev': zt_prev,
        # 兼容旧调用方, 但新展示必须区分两个指标。
        'ad_ratio': ratios.get('breadth_ratio', ad_val),
        'breadth_ratio': ratios.get('breadth_ratio'),
        'advance_decline_ratio': ratios.get('advance_decline_ratio'),
        'up': ratios.get('up', _up), 'down': ratios.get('down', _dn),
        'ladder': ladder, 'h3': h3, 'h4': h4, 'h5': h5, 'h6p': h6p,
        'ladder_metrics': ladder_metrics,
        'mainline_concentration': mainline_concentration,
        'data_credibility': data_credibility,
        'lianban_review': lianban_review,
        'mainline_review': mainline_review,
        'data_quality': quality,
        'market_state': market_state,
        'historical_samples': historical_samples,
        'historical_stats': historical_stats,
        'win_rate_sample_size': timing.get('win_rate_sample_size', 0),
        'win_rate_confidence_interval': timing.get('win_rate_confidence_interval'),
        'scenario_probabilities': scenario_probabilities,
        'scenarios': scenarios,
        'focus_df': focus_df,
        'focus_catalysts': focus_catalysts or {},
        'publication_mode': unified_context.get('publication_mode', market_state.get('publication_mode')),
        'report_context': unified_context,
        'daily_delta': unified_context.get('daily_delta', {}),
        'prediction_review': unified_context.get('prediction_review', {}),
        'lineage': unified_context.get('lineage', {}),
        'progression_chain': (unified_context.get('facts') or {}).get('progression_chain', {}),
    }


def _fmt(v: Any, default: str = '—') -> str:
    if v is None:
        return default
    if isinstance(v, float):
        return f'{v:.2f}'
    return str(v)


def _win_rate_color(wr: float | None) -> str:
    if wr is None:
        return '#8b949e'
    if wr >= 0.65:
        return '#3fb950'
    if wr >= 0.45:
        return '#d29922'
    return '#ff4444'


def _pick_base_scenario(scenarios: list[dict]) -> tuple[dict | None, int]:
    """从情形列表里挑"概率最高"的作为基准情形 (供重点提炼带高亮).
    prob 字段为动态概率文本；无法解析按 0 处理，平票取先出现者。
    """
    best, best_p = None, -1
    for s in scenarios or []:
        m = re.search(r'(\d+)\s*%', str(s.get('prob', '')))
        p = int(m.group(1)) if m else 0
        if p > best_p:
            best, best_p = s, p
    return best, max(best_p, 0)
def _historical_outcomes_only(ctx: dict) -> bool:
    """识别只有历史结果率、没有前瞻概率的研究统计。"""
    stats = ctx.get('scenario_stats')
    if not isinstance(stats, dict):
        return False
    rows = [row for row in stats.values() if isinstance(row, dict)]
    has_outcome_rate = any('win_rate' in row or 'horizon' in row for row in rows)
    has_forward_probability = any(row.get('predicted_probability') is not None or row.get('probability') is not None for row in rows)
    return has_outcome_rate and not has_forward_probability


def _sentiment_temp(ad, zt, dt, curr_h, pressure_5d) -> tuple[int, str, str]:
    """把盘面折算成 0-100 情绪温度 + 档位标签 + 颜色 (A股口径: 热=红, 冷=绿).
    A/D 为主轴; 跌停家数触发亏钱效应减分; 空间板破 5 日压力加分.
    A/D 缺失时用涨跌停家数比兜底.
    """
    base = None
    if isinstance(ad, (int, float)):
        base = ad * 100
    if base is None:
        tot = (zt or 0) + (dt or 0)
        base = (zt / tot * 100) if tot else 50.0
    if dt and dt > 15:
        base -= min((dt - 15) * 0.8, 15)
    if curr_h and pressure_5d and curr_h > pressure_5d:
        base += 5
    pct = int(max(2, min(98, round(base))))
    if pct < 35:
        return pct, '冰点', '#3fb950'
    if pct < 50:
        return pct, '偏弱', '#58a6ff'
    if pct < 65:
        return pct, '中性', '#d29922'
    if pct < 80:
        return pct, '偏强', '#ff8800'
    return pct, '过热', '#ff4444'


def _split_focus_pool(focus_df) -> dict:
    """把 focus_pool DataFrame 按'策略池'字段拆桶, 便于挂到不同情形.
    返回: {'space': [...], 'midcore': [...], 'low_level': [...]}
      space: 高连板追打 (空间博弈池 + 主升接力池, 用于 A/B/C)
      midcore: 中军低吸 (核心中军低吸池, 用于 D 或 C 换车)
      low_level: 首板/二板补涨观察
    每个元素形如 {'name': '爱丽家居', 'plate': '并购重组50%', 'cond': '...', 'stop': '...'}
    """
    buckets = {'space': [], 'midcore': [], 'low_level': []}
    if focus_df is None:
        return buckets
    try:
        if hasattr(focus_df, 'empty') and focus_df.empty:
            return buckets
    except Exception:
        return buckets
    try:
        for _, row in focus_df.iterrows():
            pool = str(row.get('策略池', ''))
            entry = {
                'name': str(row.get('股票', '')).strip(),
                'plate': str(row.get('板块', '')).strip(),
                'cond':  str(row.get('入场条件', '')).strip(),
                'stop':  str(row.get('防守位', '')).strip(),
            }
            if not entry['name']:
                continue
            if '补涨' in pool:
                buckets['low_level'].append(entry)
            elif '空间博弈' in pool or '主升接力' in pool:
                buckets['space'].append(entry)
            elif '中军' in pool or '低吸' in pool:
                buckets['midcore'].append(entry)
    except Exception:
        pass
    return buckets


def _clean_plate(plate: str) -> str:
    """把 focus_pool 的'板块'字段清洗成可读题材名, 无意义时返回空串.
    - '并购重组50%' / 'IDC电力设备50%' -> 去掉尾部的"数字+%"投票占比 -> '并购重组' / 'IDC电力设备'
    - '/' / '' -> 空串 (占位符, 不显示)
    - '10日' / '5日' -> 空串 (中军池这里存的是 N日窗口, 不是真题材)
    """
    p = (plate or '').strip()
    if not p or p == '/':
        return ''
    # 中军低吸池的"板块"字段实为 N日窗口 (如 '10日'), 非真题材
    if re.fullmatch(r'\d+日', p):
        return ''
    # 只取第一个题材 (可能逗号分隔), 去掉尾部的"数字+%"投票占比
    p = p.split(',')[0].strip()
    p = re.sub(r'\d+%$', '', p).strip()
    return p


def _fmt_stock(entry: dict) -> str:
    """一只票的紧凑话术: '爱丽家居 (并购重组)'; 板块无意义时只显示股名."""
    name = entry.get('name', '')
    plate_short = _clean_plate(entry.get('plate', ''))
    if plate_short:
        return f'{name} ({plate_short})'
    return name


def _render_catalyst_cell(name: str, catalysts: dict | None) -> str:
    """催化列 <td> 内容: 一行 tag + 简短 text; 无数据显示 —"""
    if not catalysts:
        return '<td class="fp-cat">—</td>'
    item = catalysts.get(name) or {}
    cat = item.get('catalyst') if isinstance(item, dict) else None
    if not cat:
        return '<td class="fp-cat" style="color:#6e7681;">无近期催化</td>'
    cat = normalize_catalyst(cat)
    tag = cat['tag']
    text = cat['text']
    url = cat['url']
    body = _esc(text) or _esc(tag)
    if url:
        body = f'<a href="{_esc(url)}" target="_blank" rel="noopener" style="color:inherit;text-decoration:underline dotted;">{body}</a>'
    return (f'<td class="fp-cat"><span class="fp-cat-tag">{_esc(tag)}</span>'
            f'<div class="fp-cat-text">{body}</div></td>')


def _render_focus_table(buckets: dict, catalysts: dict | None = None, mode: str = 'decision') -> str:
    """把 focus_pool 拆好的 space/midcore 两桶渲染成两张表 (独立看板用).

    catalysts: {股票名: {catalyst: {tag, text, url}, raw: ...}}, 来自
        catalyst_attribution.attribute_focus_pool(). 无则催化列显示 '—'.
    空表则整个 section 省略, 避免占版面显示空表。
    """
    space = buckets.get('space', [])
    midcore = buckets.get('midcore', [])
    if mode == 'facts_only':
        return '<div class="fp-empty" style="color:#d29922;padding:12px 4px;">股票池未发布（数据阻断）</div>'
    observation_only = mode == 'observation'
    if not space and not midcore:
        return ('<div class="fp-empty" style="color:#6e7681;padding:12px 4px;">'
                '暂无核心股票池 · 无近期催化</div>' if catalysts else '')

    def _row(entry: dict) -> str:
        name = entry.get('name', '')
        return (
            f'<tr>'
            f'<td class="fp-name"><b>{_esc(name)}</b></td>'
            f'<td class="fp-plate">{_esc(_clean_plate(entry.get("plate", "")) or "—")}</td>'
            f'{_render_catalyst_cell(name, catalysts)}'
            f'<td class="fp-entry">{_esc(entry.get("cond", ""))}</td>'
            f'<td class="fp-stop">{_esc(entry.get("stop", ""))}</td>'
            f'</tr>'
        )

    parts = []
    if space:
        space_rows = ''.join(_row(x) for x in space)
        parts.append(f'''
        <div class="fp-block fp-space">
          <div class="fp-block-title">🚀 {"观察名单（非推荐）" if observation_only else "空间博弈 / 主升接力池"} · {len(space)} 只
            <span class="fp-block-sub">{"仅供观察 · 条件触发" if observation_only else "高连板追打 · 对应 A / B / C 场景"}</span></div>
          <table class="fp-table"><thead><tr>
            <th>标的</th><th>主线</th><th>近期催化</th><th>入场条件</th><th>防守位</th>
          </tr></thead><tbody>{space_rows}</tbody></table>
        </div>''')
    if midcore:
        mid_rows = ''.join(_row(x) for x in midcore)
        parts.append(f'''
        <div class="fp-block fp-midcore">
          <div class="fp-block-title">🛡️ {"观察名单（非推荐）" if observation_only else "核心中军低吸池"} · {len(midcore)} 只
            <span class="fp-block-sub">{"仅供观察 · 条件触发" if observation_only else "深蹲抄底 · 对应 D 场景或 C 场景换车备胎"}</span></div>
          <table class="fp-table"><thead><tr>
            <th>标的</th><th>周期</th><th>近期催化</th><th>入场条件</th><th>防守位</th>
          </tr></thead><tbody>{mid_rows}</tbody></table>
        </div>''')

    return f'''<div class="fp-wrap">{''.join(parts)}</div>'''


def _default_scenarios(curr_h: int, prev_h: int, focus_df=None) -> list[dict]:
    """T+1 4 情形树的默认模板. 从 focus_df 挂具体标的:
    - A (双龙一字): 空间池最强 2 只锁仓
    - B (空间一字+接力分歧): 换车 space 池第 2-3 只
    - C (高开分歧+二三进阶): space 池首选 + midcore 池 1 只
    - D (龙头炸板): midcore 池深蹲 + 前排清仓
    focus_df 为空时话术自动降级为主线级别 (不 hardcode 具体股名).
    """
    buckets = _split_focus_pool(focus_df)
    space = buckets['space']
    midcore = buckets['midcore']

    # A: 前 2 只空间强势股锁仓
    if space[:2]:
        a_head = space[0]
        a_items = [
            f'前排持仓不动 · 锁仓核心: {_fmt_stock(a_head)}',
            f'盯 {curr_h - 1}板梯队是否秒板 → 主线延续' if curr_h > 1 else '盯高度是否新高',
            '盘中不追后排 (主升诱多)',
            '目标: 分歧日再兑现',
        ]
    else:
        a_items = ['前排持仓不动 / 场内锁仓', '盘中不追后排 (主升诱多)',
                   f'盯 {curr_h - 1}板梯队是否秒板 → 主线延续',
                   '目标: 分歧日再兑现']

    # B: 换车到空间池第 2-3 只 (次强梯队, 低吸接力)
    if len(space) >= 2:
        switch_targets = ', '.join(_fmt_stock(x) for x in space[1:3])
        b_items = [
            '前排减仓 30-50% · 兑现主升',
            f'低吸换车: {switch_targets}',
            '不追高位孤峰 (胜率 <40%)',
            '警惕孤峰塌陷',
        ]
    else:
        b_items = ['前排减仓 30-50%', '换车次高梯队低吸',
                   '不接高位孤峰回封', '警惕孤峰塌陷']

    # C: 空间池首选 + midcore 池 1 只 (三因子共振买点)
    c_items = ['★ 三因子共振时才启动 (A/D>0.65 + 梯队≥12 + 破压)']
    if space[:1]:
        c_items.append(f'加仓接力核心: {_fmt_stock(space[0])}')
    if midcore[:1]:
        c_items.append(f'低吸预备: {_fmt_stock(midcore[0])}')
    else:
        c_items.append('主线中军低吸做备胎')
    c_items.append('目标周期 3-5 天')

    # D: 前排清仓 + midcore 深蹲 (只留防守低吸)
    d_items = ['F 顶部崩塌预警 (断板 ≥ 3 + 跌停 >15)', '立即清仓前排 · 不抄任何高位股']
    if midcore[:2]:
        deep_targets = ', '.join(_fmt_stock(x) for x in midcore[:2])
        d_items.append(f'仅留深蹲低吸: {deep_targets}')
    else:
        d_items.append('全线离场观望')

    return [
        {'kind': 'attack', 'name': 'A · 双龙一字', 'prob': '概率待质量校验',
         'items': a_items, 'pos': '仓位 · 7-8 成'},
        {'kind': 'moderate', 'name': 'B · 空间一字 + 接力分歧', 'prob': '概率待质量校验',
         'items': b_items, 'pos': '仓位 · 4-5 成'},
        {'kind': 'attack', 'name': 'C · 高开分歧 + 二三进阶', 'prob': '概率待质量校验',
         'items': c_items, 'pos': '仓位 · 8-9 成 ⭐'},
        {'kind': 'defense', 'name': 'D · 龙头炸板', 'prob': '概率待质量校验',
         'items': d_items, 'pos': '仓位 · 1-2 成'},
    ]


def _esc(s: Any) -> str:
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _factor_row(name: str, value: str, ok: str, hint: str) -> str:
    icon_class = {'✅': 'check-ok', '❌': 'check-fail', '❓': 'check-warn', '⚠️': 'check-warn'}.get(
        ok[:1] if ok else '', 'check-warn')
    return (f'<tr><td>{_esc(name)}</td><td><b>{_esc(value)}</b></td>'
            f'<td><span class="{icon_class}">{_esc(ok)}</span></td>'
            f'<td>{_esc(hint)}</td></tr>')


def _scen_card(s: dict) -> str:
    items = ''.join(f'<li>{_esc(x)}</li>' for x in s.get('items', []))
    base_cls = ' scen-base' if s.get('is_base') else ''
    base_badge = '<span class="scen-base-badge">基准</span>' if s.get('is_base') else ''
    stat_text = _scenario_stat_text(s)
    return (f'<div class="scen-card {s.get("kind", "moderate")}{base_cls}">'
            f'<div class="head"><span class="name">{_esc(s.get("name", ""))}{base_badge}</span>'
            f'<span class="prob">{_esc(s.get("prob", ""))}</span></div>'
            f'<div class="scen-stat">{_esc(stat_text)}</div>'
            f'<ul>{items}</ul>'
            f'<div class="pos">{_esc(s.get("pos", ""))}</div>'
            f'</div>')


def _scenario_stat_text(s: dict) -> str:
    """把场景的历史样本与区间压成一行可读摘要。"""
    sample_size = s.get('sample_size')
    try:
        sample_size = int(sample_size or 0)
    except (TypeError, ValueError):
        sample_size = 0
    ci_text = str(s.get('confidence_interval_text') or '').strip()
    if sample_size <= 0:
        return '样本不足 · 暂无置信区间'
    if not ci_text or ci_text == '样本不足':
        return f'样本 {sample_size} · 暂无置信区间'
    estimated = ' · 由历史比例反推' if s.get('confidence_interval_estimated') else ''
    return f'样本 {sample_size} · {ci_text}{estimated}'


def _win_rate_stat_text(ctx: dict, win_rate: Any) -> str:
    """返回独立/内嵌 KPI 共用的历史命中率证据摘要。"""
    sample_size = ctx.get('win_rate_sample_size', 0)
    try:
        sample_size = int(sample_size or 0)
    except (TypeError, ValueError):
        sample_size = 0
    ci = ctx.get('win_rate_confidence_interval')
    ci_text = ci.get('text') if isinstance(ci, dict) else None
    if not isinstance(win_rate, (int, float)):
        return '样本不足 · 暂无置信区间'
    if not ci_text:
        return f'样本 {sample_size} · 暂无置信区间' if sample_size else '暂无置信区间'
    return f'样本 {sample_size} · {ci_text}'


def _history_row(c: dict) -> str:
    result = c.get('result', '')
    cls = 'win' if '⭐' in result or '✓' in result else ('lose' if '❌' in result else 'neutral')
    return (f'<tr class="{cls}"><td>{_esc(c.get("date"))}</td>'
            f'<td>{_esc(c.get("curr_h"))}板</td>'
            f'<td>{_esc(c.get("zt"))}</td>'
            f'<td>{_esc(c.get("ladder"))}</td>'
            f'<td>{_esc(c.get("t1"))}</td>'
            f'<td>{_esc(c.get("t2"))}</td>'
            f'<td>{_esc(c.get("t3"))}</td>'
            f'<td>{_esc(result)}</td></tr>')


def _sentiment_score(ad, curr_h, pressure_5d, zt, dt, zt_prev) -> tuple[int, str, str]:
    """把多因子压成 0-100 情绪温度分, 供顶部温度计一眼读。
    返回 (score, mood_label, mood_color).
      A/D 主导 (0-55), 空间突破加成 (0-20), 涨停环比 (0-15), 跌停惩罚 (0 到 -20)。
    """
    score = 50.0
    if isinstance(ad, (int, float)):
        # A/D 0.30→冰点, 0.50→中性, 0.70+→亢奋; 线性映射到 20-85
        score = 20 + max(0.0, min(1.0, (ad - 0.30) / 0.45)) * 65
    # 空间突破前压力: 情绪外扩
    if pressure_5d and curr_h > pressure_5d:
        score += 8
    elif pressure_5d and curr_h < pressure_5d:
        score -= 6
    # 涨停环比放大/萎缩
    if zt_prev > 0:
        boom = zt / zt_prev
        if boom >= 1.2:
            score += 8
        elif boom <= 0.6:
            score -= 8
    # 跌停亏钱效应惩罚
    if dt > 15:
        score -= min(18, (dt - 15) * 1.2)
    score = max(2, min(98, round(score)))
    if score >= 70:
        return score, '亢奋 · 主升', '#3fb950'
    if score >= 55:
        return score, '偏暖 · 结构活跃', '#58c463'
    if score >= 45:
        return score, '中性 · 结构博弈', '#d29922'
    if score >= 30:
        return score, '偏冷 · 防守', '#ff8800'
    return score, '冰点 · 空仓观望', '#ff4444'


def _mark_base_scenario(scenarios: list[dict]) -> dict | None:
    """从场景列表挑概率最高的作为'基准情形', 打 is_base 标记并返回它本身。
    概率解析失败时退回第一个。"""
    if not scenarios:
        return None
    def _p(s):
        m = re.search(r'(\d+)', str(s.get('prob', '')))
        return int(m.group(1)) if m else -1
    scored = [(s, _p(s)) for s in scenarios]
    if not any(score >= 0 for _, score in scored):
        for s in scenarios:
            s.pop('is_base', None)
        return None
    base = max(scored, key=lambda item: item[1])[0]
    for s in scenarios:
        s['is_base'] = (s is base)
    return base


def _prepare_scenarios(ctx: dict, curr_h: int, prev_h: int, focus_df, *,
                       breadth_ratio, zt, dt, pressure_5d, ladder, h5) -> list[dict]:
    """统一生成渲染层情形树，避免独立看板/内嵌看板回退到固定概率模板。"""
    scenarios = ctx.get('scenarios') or _default_scenarios(curr_h, prev_h, focus_df=focus_df)
    scenarios = [dict(row) for row in scenarios]
    if len(scenarios) < 4:
        defaults = _default_scenarios(curr_h, prev_h, focus_df=focus_df)
        scenarios.extend(defaults[len(scenarios):])
    dynamic = build_scenario_probabilities(
        scene=ctx.get('scene'), ad_ratio=breadth_ratio,
        zt=zt, dt=dt, curr_h=curr_h, pressure_5d=pressure_5d,
        ladder=ladder, h5=h5,
        data_quality=ctx.get('data_quality'),
        historical_samples=ctx.get('historical_samples', 0),
        historical_stats=ctx.get('historical_stats'),
    )
    by_code = {row['code']: row for row in dynamic}
    for scenario, code in zip(scenarios[:4], ('A', 'B', 'C', 'D')):
        # 保留股票池话术，仅覆盖概率/名称/类型等由统一逻辑负责的字段。
        scenario.update({key: by_code[code].get(key) for key in (
            'code', 'name', 'kind', 'probability', 'probability_pct', 'prob',
            'confidence', 'sample_size', 'probability_kind', 'historical_rate',
            'confidence_interval', 'confidence_interval_text',
            'confidence_interval_estimated')})
    scenarios = scenarios[:4]
    _mark_base_scenario(scenarios)
    return scenarios


def _ladder_view(ctx: dict, curr_h: int, h3: Any, h4: Any, h5: Any, h6p: Any) -> dict[str, Any]:
    """把真实梯队指标整理成渲染层统一字段，并兼容旧 ctx。"""
    raw = ctx.get('ladder_metrics')
    if isinstance(raw, dict) and raw:
        metrics = dict(raw)
    else:
        metrics = compute_ladder_metrics([
            {'height': 3, 'count': h3}, {'height': 4, 'count': h4},
            {'height': 5, 'count': h5}, {'height': 6, 'count': h6p},
        ])
        # 旧 ctx 只有各高度数量时，上面的通用解析无法识别 count，手动修正。
        counts = {3: int(h3 or 0), 4: int(h4 or 0), 5: int(h5 or 0), 6: int(h6p or 0)}
        heights = [height for height, count in counts.items() for _ in range(max(0, count))]
        metrics = compute_ladder_metrics([{'height': height} for height in heights])
    counts = metrics.get('counts') or {
        3: int(metrics.get('h3', h3) or 0),
        4: int(metrics.get('h4', h4) or 0),
        5: int(metrics.get('h5', h5) or 0),
        6: int(metrics.get('h6p', h6p) or 0),
    }
    gap_heights = metrics.get('gap_heights') or []
    metrics.setdefault('height', curr_h)
    metrics.setdefault('ladder', ctx.get('ladder'))
    metrics['counts'] = counts
    metrics.setdefault('gap_text', '、'.join(f'缺{x}板' for x in gap_heights) if gap_heights else '无明显断层')
    metrics.setdefault('gap_risk', bool(gap_heights))
    metrics.setdefault('gap_risk_label', '高' if metrics['gap_risk'] else '低')
    metrics.setdefault('progression_label', '突破昨日最高高度占比')
    metrics.setdefault(
        'progression_definition',
        '今日梯队中高度超过昨日最高板的个股数 / 今日有效梯队个股数',
    )
    metrics.setdefault('progression_denominator', '今日有效梯队个股数')
    metrics.setdefault('progressed_count', 0)
    metrics.setdefault('previous_count', 0)
    metrics.setdefault('broken_count', 0)
    metrics.setdefault('isolated_leader', False)
    if not metrics.get('progression_text'):
        metrics['progression_text'] = (
            f"{metrics.get('progressed_count', 0)}/{metrics.get('height_count', 0)}"
            if metrics.get('previous_count') else '样本不足'
        )
    return metrics


def _quality_view(ctx: dict) -> dict[str, Any]:
    quality = ctx.get('data_quality')
    return quality if isinstance(quality, dict) else assess_data_quality()


def _publication_policy(ctx: dict) -> dict[str, bool | str]:
    """把数据质量状态映射成报表可发布范围。"""
    state = ctx.get('market_state') if isinstance(ctx.get('market_state'), dict) else build_market_state(ctx.get('data_quality'))
    mode = str(state.get('publication_mode') or '').lower()
    if mode not in {'facts_only', 'observation', 'decision'}:
        quality = _quality_view(ctx)
        status = str(quality.get('status') or 'unknown').lower()
        mode = 'facts_only' if status in {'blocked', 'non_trading_day'} else ('decision' if status == 'ok' else 'observation')
    return {
        'mode': mode,
        'facts_only': mode == 'facts_only',
        'observation_only': mode == 'observation',
        'decision_ready': mode == 'decision',
        'allow_focus_pool': mode == 'decision',
    }


def _sanitize_scenarios_for_publication(scenarios: list[dict], mode: str) -> list[dict]:
    """根据发布模式过滤场景，避免质量不足时输出伪决策。"""
    if mode == 'decision':
        return scenarios
    if mode == 'facts_only':
        return [{
            'code': 'FACTS',
            'name': '事实层',
            'probability': None,
            'probability_pct': None,
            'prob': None,
            'items': [
                '仅展示当前事实与数据质量问题',
                '待沪深北全A数据通过校验后再评估情形',
                '当前仅展示已校验事实',
            ],
            'pos': '仅事实层',
            'is_base': False,
        }]
    items = [
        '仅观察数据新鲜度、覆盖率和涨跌家数',
        '数据重新通过质量校验后才评估该情形',
        '当前展示条件性观察',
    ]
    pos = '条件性观察'
    return [dict(row, items=list(items), pos=pos, is_base=False) for row in scenarios]


_MODULE_DISPLAY_ORDER = (
    'universe', 'price_raw', 'breadth', 'limit_pool', 'sector',
    'echelon', 'history', 'price_qfq', 'ai',
)
_MODULE_LABELS = {
    'universe': '证券主数据',
    'price_raw': '报告日价格',
    'breadth': '市场宽度',
    'limit_pool': '涨停事实池',
    'sector': '题材归因',
    'echelon': '连板梯队',
    'history': '历史情绪',
    'price_qfq': '前复权价格',
    'ai': 'AI 研判',
}
_MODULE_STATUS_LABELS = {
    'ok': '正常',
    'degraded': '降级',
    'blocked': '阻断',
    'unavailable': '不可用',
    'unknown': '未评估',
}

_SCOPE_LABELS = {
    'market_facts': '市场事实',
    'lianban_review': '连板复盘',
    'mainline_review': '主线归因',
    'return_analysis': '复权收益',
    'ai_review': 'AI 复盘',
}
_SCOPE_MODE_LABELS = {
    'full': '完整可用',
    'limited': '条件性',
    'unavailable': '不可用',
}

def _module_sort_key(key: str, modules: dict) -> tuple[int, int, str]:
    module = modules.get(key) if isinstance(modules.get(key), dict) else {}
    status = str(module.get('status') or 'unknown').lower()
    priority = _MODULE_DISPLAY_ORDER.index(key) if key in _MODULE_DISPLAY_ORDER else len(_MODULE_DISPLAY_ORDER)
    return (0 if status != 'ok' else 1, priority, key)


def _module_coverage_text(module: dict) -> str:
    total = module.get('total')
    covered = module.get('covered')
    coverage = module.get('coverage_pct')
    if total is not None:
        detail = f'{_fmt(covered, "0")}/{_fmt(total, "0")}'
        if coverage is not None:
            detail += f'（{_fmt(coverage)}%）'
        return detail
    if coverage is not None:
        return f'{_fmt(coverage)}%'
    return ''


def _find_limit_pool_reconciliation(modules: dict, lineage: dict) -> dict:
    candidates = (
        ((modules.get('limit_pool') or {}).get('lineage') if isinstance(modules.get('limit_pool'), dict) else {}),
        ((lineage.get('limit_pool') or {}).get('lineage') if isinstance(lineage.get('limit_pool'), dict) else {}),
        ((modules.get('sector') or {}).get('lineage') if isinstance(modules.get('sector'), dict) else {}),
        ((lineage.get('sector') or {}).get('lineage') if isinstance(lineage.get('sector'), dict) else {}),
        modules.get('limit_pool'),
        lineage.get('limit_pool'),
    )
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        reconciliation = candidate.get('reconciliation')
        if isinstance(reconciliation, dict) and reconciliation:
            return reconciliation
    return {}


def _quality_scope_summary(quality: dict, publication_label: str, prefix: str) -> str:
    modules = quality.get('modules') if isinstance(quality.get('modules'), dict) else {}
    passed = []
    limited = []
    for key in _MODULE_DISPLAY_ORDER:
        module = modules.get(key) if isinstance(modules.get(key), dict) else None
        if not module:
            continue
        label = _MODULE_LABELS.get(key, key)
        status = str(module.get('status') or 'unknown').lower()
        if status == 'ok':
            passed.append(label)
            continue
        coverage = module.get('coverage_pct')
        coverage_text = f' {_fmt(coverage)}%' if coverage is not None else f' {_MODULE_STATUS_LABELS.get(status, "未评估")}'
        limited.append(f'{label}{coverage_text}')
    passed_text = '、'.join(passed) if passed else '暂无模块通过完整校验'
    limited_text = '、'.join(limited) if limited else '无'
    scopes = quality.get('publication_scopes') if isinstance(quality.get('publication_scopes'), dict) else {}
    scope_rows = []
    for key, label in _SCOPE_LABELS.items():
        scope = scopes.get(key) if isinstance(scopes.get(key), dict) else {}
        mode = str(scope.get('mode') or 'unavailable').lower()
        scope_rows.append(
            f'{label}={_SCOPE_MODE_LABELS.get(mode, "不可用")}'
        )
    scope_text = '；'.join(scope_rows) if scopes else '未生成分层评估'
    return (
        f'<div class="{prefix}quality-scope"><b>当前可用范围：{_esc(publication_label)}</b>'
        f'<span>事实层已通过：{_esc(passed_text)}</span>'
        f'<span>限制项：{_esc(limited_text)}</span>'
        f'<span>分层可用性：{_esc(scope_text)}</span></div>'
    )


def _quality_module_source_summary(quality: dict, *, field: str = 'source') -> str:
    """返回报表实际参与的模块来源，优先使用 lineage.source_chain。"""
    modules = quality.get('modules') if isinstance(quality.get('modules'), dict) else {}
    rows = []
    for key in ('price_raw', 'breadth'):
        module = modules.get(key) if isinstance(modules.get(key), dict) else {}
        lineage = module.get('lineage') if isinstance(module.get('lineage'), dict) else {}
        chain = lineage.get('source_chain')
        if isinstance(chain, (list, tuple)):
            chain = [str(item).strip() for item in chain if str(item).strip()]
        else:
            chain = []
        if field == 'fallback':
            value = lineage.get('fallback_source') or module.get('fallback_source')
            if not value and len(chain) > 1:
                value = ' → '.join(chain[1:])
        elif chain:
            value = ' → '.join(chain)
        else:
            value = module.get('source')
        if not value:
            value = '未声明' if field != 'fallback' else '未配置'
        rows.append(f'{_MODULE_LABELS.get(key, key)}：{value}')
    return '；'.join(rows)


def _quality_module_fallback_summary(quality: dict) -> str:
    modules = quality.get('modules') if isinstance(quality.get('modules'), dict) else {}
    rows = []
    for key in ('price_raw', 'breadth'):
        module = modules.get(key) if isinstance(modules.get(key), dict) else {}
        lineage = module.get('lineage') if isinstance(module.get('lineage'), dict) else {}
        used = bool(lineage.get('used_fallback', module.get('used_fallback')))
        rows.append(f'{_MODULE_LABELS.get(key, key)}={"是" if used else "否"}')
    return '；'.join(rows)


def _quality_html(ctx: dict, prefix: str = '') -> str:
    """渲染统一的数据质量摘要，避免把内部字段或 Python None 泄漏到 HTML。"""
    quality = _quality_view(ctx)
    status = str(quality.get('status') or 'unknown').lower()
    status_text = {
        'ok': '数据正常', 'degraded': '数据降级', 'blocked': '数据阻断',
        'non_trading_day': '非交易日',
    }.get(status, '未评估')
    total = quality.get('market_total')
    covered = quality.get('market_covered')
    coverage = quality.get('coverage_pct')
    if total:
        try:
            coverage_value = float(coverage or 0)
            coverage_display = (f'{coverage_value:.0f}%' if coverage_value.is_integer()
                                else f'{coverage_value:.1f}%')
            coverage_text = f'{covered or 0} / {total}（{coverage_display}）'
        except (TypeError, ValueError):
            coverage_text = f'{covered or 0} / {total}（未计算）'
    else:
        coverage_text = '未声明/未提供'
    primary = _esc(quality.get('primary_source') or '未声明')
    fallback = _esc(quality.get('fallback_source') or '未配置')
    module_sources = _esc(_quality_module_source_summary(quality))
    module_fallbacks = _esc(_quality_module_fallback_summary(quality))
    scope = _esc(quality.get('market_scope') or '沪深北全A')
    prefixes = _esc(','.join(quality.get('market_prefixes') or ()) or '未获取')
    fallback_used = '是' if quality.get('used_fallback') else '否'
    stale = '是' if quality.get('used_stale') else '否'
    missing = quality.get('missing_fields') or []
    errors = quality.get('errors') or []
    issue_text = ''
    if missing:
        issue_text += f'缺字段：{"、".join(map(str, missing))}'
    if errors:
        issue_text += f'{"；" if issue_text else ""}错误：{"；".join(map(str, errors))}'
    state = ctx.get('market_state') if isinstance(ctx.get('market_state'), dict) else build_market_state(quality)
    reason = _esc(state.get('reason') or '质量检查通过')
    freshness_labels = {
        'fresh': '新鲜', 'delayed': '延迟', 'stale': '陈旧',
        'blocked': '阻断', 'unknown': '未知',
    }
    freshness_level = str(quality.get('freshness_level') or 'unknown').lower()
    freshness_text = freshness_labels.get(freshness_level, '未知')
    freshness_reason = _esc(quality.get('freshness_reason') or '未提供可核验的数据时间戳')
    data_timestamp = _esc(quality.get('data_timestamp') or '未提供')
    source_timestamp = _esc(quality.get('source_timestamp') or '未提供')
    report_generated_at = _esc(quality.get('report_generated_at') or '未提供')
    publication_label = {'facts_only': '仅事实层', 'observation': '观察与条件触发', 'decision': '完整决策'}.get(str(state.get('publication_mode') or ''), '待核验')
    scope_summary = _quality_scope_summary(quality, publication_label, prefix)
    data_layer = state.get('data_layer') or {}
    statistics_layer = state.get('statistics_layer') or {}
    decision_layer = state.get('decision_layer') or {}
    quality_cls = f'{prefix}quality-card'
    return f"""
    <div class="{quality_cls}">
      <div class="{prefix}quality-title">数据质量：<b>{_esc(status_text)}</b></div>
      {scope_summary}
      <div class="{prefix}quality-items">
        <span>数据源：{primary}</span>
        <span>备用源：{fallback}</span>
        <span>模块来源：{module_sources}</span>
        <span>模块备用源启用：{module_fallbacks}</span>
        <span>市场范围：{scope}</span>
        <span>代码前缀：{prefixes}</span>
        <span>覆盖率：{_esc(coverage_text)}</span>
        <span>备用源启用：{fallback_used}</span>
        <span>stale：{stale}</span>
        <span>新鲜度：{_esc(freshness_text)}</span>
        <span>报告可用范围：{_esc(publication_label)}</span>
      </div>
      <div class="{prefix}quality-layers">
        <span>行情时间：{data_timestamp}</span>
        <span>源时间：{source_timestamp}</span>
        <span>生成时间：{report_generated_at}</span>
        <span>数据层：{_esc(data_layer.get('label') or status_text)}</span>
        <span>统计层：{_esc(statistics_layer.get('label') or '统计待核验')}</span>
        <span>决策层：{_esc(decision_layer.get('label') or '仅条件性结论')}</span>
      </div>
      <div class="{prefix}quality-issues">新鲜度原因：{freshness_reason} · {_esc(issue_text) if issue_text else '字段完整性与来源状态已记录'} · {reason}</div>
    </div>"""


def _ratio_text(value: Any, *, inf_text: str = '∞') -> str:
    if isinstance(value, (int, float)):
        if value == float('inf'):
            return inf_text
        return f'{value:.3f}'
    return '未取到'


def _build_playbook(curr_h, zt, breadth, h5, date_str) -> list[dict]:
    """基于当日盘面, 从三条实证规律挑出命中的"今日操作口令"。

    阈值来自可维护的盘面规则配置；历史命中率由预测回顾模块动态注入，
    不在看板文案中硬编码固定胜率。

    返回 [{tone, icon, text}], tone ∈ hot|cold|warn|ok|neutral 决定配色。
    只输出命中的口令; 数据是论据, 口令是动作。
    """
    cmds: list[dict] = []
    breadth_is = isinstance(breadth, (int, float))
    ZT_HOT, ZT_COLD = 126, 46

    # ① 情绪极值逆向 (最高优先级)
    if zt >= ZT_HOT:
        cmds.append({'tone': 'hot', 'icon': '🔥',
            'text': f'涨停 {zt} 家破高潮线 — 明天别追高。过热不一定崩(缓慢消化), '
                    f'但收益已到头, 该止盈的分批走。'})
    elif zt <= ZT_COLD or (breadth_is and breadth < 0.2):
        _r = f'上涨占比 {breadth:.0%} ' if breadth_is else ''
        cmds.append({'tone': 'cold', 'icon': '🥶',
            'text': f'涨停 {zt} 家 {_r}冰点 — 明天优先观察情绪修复, '
                    f'仅在承接确认后逢低加, 不做无条件抄底。'})

    # ② 孤峰预警 (最高板 ≥6 且 5 板断档)
    if curr_h >= 6 and h5 == 0:
        cmds.append({'tone': 'warn', 'icon': '⚠️',
            'text': f'空间板 {curr_h}板孤峰、5板断档 — 龙一独一档没接力, '
                    f'高位股先看承接与补位，未确认前不追高。'})
    elif curr_h >= 6:
        cmds.append({'tone': 'ok', 'icon': '🪜',
            'text': f'空间板 {curr_h}板且阶梯连续 — 主升情绪健康, '
                    f'可放胆做题材, 中位段(3-6板)拿得住。'})

    # ③ 连板 2 板陷阱 (常驻提醒)
    cmds.append({'tone': 'neutral', 'icon': '📉',
        'text': '首板/2板 → 观察封单、换手和次日承接；'
                '3-6板 → 重点看是否突破昨日最高高度，并结合承接决定去留。'})

    # ④ 日历脾气 (T+1 前瞻)
    try:
        wd = datetime.strptime(date_str, '%Y-%m-%d').weekday()
    except Exception:
        wd = -1
    if wd == 3:  # 今天周四 → 明天周五
        cmds.append({'tone': 'warn', 'icon': '📅',
            'text': '今天周四 — 临近周末先检查高位股承接与兑现压力, '
                    '高位股明天冲高先做减仓预案。'})
    elif wd == 2:  # 今天周三 → 明天周四(全周最危险)
        cmds.append({'tone': 'warn', 'icon': '📅',
            'text': '明天周四 — 今天尾盘不追满仓, 给明天的波动和减仓留空间。'})
    elif wd == 4 and (zt <= ZT_COLD or (breadth_is and breadth < 0.35)):  # 今天周五冰点 → 周一
        cmds.append({'tone': 'cold', 'icon': '📅',
            'text': '周五冰点收盘 — 周一优先观察修复信号, '
                    '不在恐慌盘中机械割肉，也不预设必然反弹。'})

    return cmds


def _render_playbook(cmds: list[dict], p: str = '') -> str:
    """把口令列表渲染成 HTML。p 是 class 前缀 (''=独立看板, 'dbd-'=内嵌 section)。"""
    if not cmds:
        return ''
    rows = ''.join(
        f'<div class="{p}pb-cmd {p}pb-{c["tone"]}">'
        f'<span class="{p}pb-icon">{c["icon"]}</span>'
        f'<span class="{p}pb-txt">{_esc(c["text"])}</span></div>'
        for c in cmds
    )
    return (f'<div class="{p}playbook">'
            f'<div class="{p}pb-title">今日操作口令'
            f'<span class="{p}pb-sub">命中实证规律 · 看到什么做什么</span></div>'
            f'{rows}</div>')


def _metric_rate_text(value: Any, *, default: str = '样本不足') -> str:
    """将比例字段安全渲染；缺少分母或比例时不伪造 0%。"""
    if isinstance(value, dict):
        value = value.get('rate')
    if value is None:
        return default
    try:
        return f'{float(value):.0%}'
    except (TypeError, ValueError):
        return default


def _ladder_quality_html(ctx: dict, prefix: str = '') -> str:
    metrics = ctx.get('ladder_metrics')
    cls = f'{prefix}quality-metrics'
    card = f'{prefix}metric-card'
    grid = f'{prefix}metric-grid'
    title = f'{prefix}metric-title'
    note = f'{prefix}metric-note'
    if not isinstance(metrics, dict) or not metrics:
        return (f'<div class="{cls}"><div class="{card}"><b>连板质量</b>'
                f'<span>数据未就位</span></div></div>')
    rates = metrics.get('advancement_rates') or metrics.get('progression_rates') or {}

    def _rate(key):
        row = rates.get(key)
        if row is None:
            try:
                row = rates.get(int(key.split('_')[0]))
            except (TypeError, ValueError):
                row = None
        return _metric_rate_text(row)

    broken = _metric_rate_text(metrics.get('broken_rate'))
    bomb = _metric_rate_text(metrics.get('bomb_rate') or metrics.get('explosion_rate'))
    reclose = _metric_rate_text(metrics.get('reclose_rate') or metrics.get('re封_rate'))
    board = metrics.get('board_structure') or {}
    one_word = board.get('one_word_count', metrics.get('one_word_count'))
    turnover = board.get('turnover_count', metrics.get('turnover_count'))
    score = metrics.get('quality_score')
    score_text = f'{float(score):.1f}/100' if isinstance(score, (int, float)) else str(metrics.get('quality_text') or '数据未就位')
    sample = metrics.get('quality_sample_size') or metrics.get('height_count') or metrics.get('sample_size')

    def _rate_detail(key):
        row = metrics.get(key)
        text = _metric_rate_text(row)
        if isinstance(row, dict):
            successes = row.get('successes', row.get('numerator'))
            trials = row.get('trials', row.get('denominator'))
            if successes is not None and trials is not None:
                return f'{text}（{successes}/{trials}）'
        return text

    first_board = _rate_detail('first_board_to_second')
    streak_promotion = _rate_detail('streak_pool_promotion')
    all_limit_up = _rate_detail('all_limit_up_reclose')
    return f'''
    <div class="{cls}">
      <div class="{title}">连板质量</div>
      <div class="{grid}">
        <span>首板→二板晋级率：{first_board}</span>
        <span>昨日连板池晋级率（高度≥2）：{streak_promotion}</span>
        <span>昨日涨停池次日再板率：{all_limit_up}</span>
        <span>二板→三板晋级率：{_rate('2_to_3')}</span>
        <span>三板→四板晋级率：{_rate('3_to_4')}</span>
        <span>四板→五板晋级率：{_rate('4_to_5')}</span>
        <span>五板→六板晋级率：{_rate('5_to_6')}</span>
        <span>昨日连板池断板率：{broken}</span>
        <span>炸板率：{bomb}</span>
        <span>炸板后回封率：{reclose}</span>
        <span>一字板：{_esc(one_word if one_word is not None else '样本不足')}</span>
        <span>换手板：{_esc(turnover if turnover is not None else '样本不足')}</span>
        <span>连板质量分：{_esc(score_text)}</span>
        <span>样本数：{_esc(sample if sample is not None else '样本不足')}</span>
      </div>
      <div class="{note}">统计口径：首板、昨日连板池、昨日涨停池分别使用独立分母；真实前后交易日证券代码匹配，缺少样本时不推断晋级率。</div>
    </div>'''


def _mainline_concentration_html(ctx: dict, prefix: str = '') -> str:
    metrics = ctx.get('mainline_concentration')
    cls = f'{prefix}quality-metrics'
    card = f'{prefix}metric-card'
    grid = f'{prefix}metric-grid'
    title = f'{prefix}metric-title'
    note = f'{prefix}metric-note'
    if not isinstance(metrics, dict) or not metrics:
        return (f'<div class="{cls}"><div class="{card}"><b>主线集中度</b>'
                f'<span>数据未就位</span></div></div>')
    top = _esc(metrics.get('top_mainline') or '数据未就位')
    share = _metric_rate_text(metrics.get('top_share'), default='数据未就位')
    attributed_share = _metric_rate_text(metrics.get('top_share_attributed_sample'), default='数据未就位')
    conservative_share = _metric_rate_text(metrics.get('top_share_authoritative_pool'), default='数据未就位')
    coverage_pct = metrics.get('attribution_coverage_pct')
    coverage = _metric_rate_text(
        float(coverage_pct) / 100 if isinstance(coverage_pct, (int, float)) else None,
        default='数据未就位',
    )
    conclusion_label = {
        'strong': '强结论',
        'conditional': '条件性结论',
        'insufficient': '覆盖不足',
    }.get(str(metrics.get('conclusion_level') or 'insufficient'), '覆盖不足')
    hhi = metrics.get('hhi')
    hhi_text = f'{float(hhi):.3f}' if isinstance(hhi, (int, float)) else '数据未就位'
    sample = metrics.get('sample_size')
    sample_text = str(sample) if isinstance(sample, int) and sample > 0 else '数据未就位'
    distribution = metrics.get('distribution') or {}
    if isinstance(distribution, list):
        parts = []
        for item in distribution[:5]:
            if isinstance(item, dict):
                parts.append(f"{_esc(item.get('name') or item.get('mainline') or '')} {_metric_rate_text(item.get('share'))}")
        dist_text = '、'.join(parts) or '数据未就位'
    elif isinstance(distribution, dict):
        dist_text = '、'.join(f'{_esc(name)} {value}' for name, value in list(distribution.items())[:5]) or '数据未就位'
    else:
        dist_text = '数据未就位'
    return f'''
    <div class="{cls}">
      <div class="{title}">主线集中度</div>
      <div class="{grid}">
        <span>领先方向：{top}</span>
        <span>已归因样本内占比：{attributed_share if attributed_share != '数据未就位' else share}</span>
        <span>归因覆盖率：{coverage}</span>
        <span>全事实池保守占比：{conservative_share}</span>
        <span>结论等级：{_esc(conclusion_label)}</span>
        <span>HHI：{_esc(hhi_text)}</span>
        <span>有效样本数：{_esc(sample_text)}</span>
        <span>主线分布：{dist_text}</span>
      </div>
      <div class="{note}">统计口径：按有效连板样本归属主线统计；覆盖不足时只表述为已归因样本中的领先方向，不外推为全事实池结论。</div>
    </div>'''


def _data_credibility_html(ctx: dict, prefix: str = '') -> str:
    summary = ctx.get('data_credibility') if isinstance(ctx.get('data_credibility'), dict) else {}
    cls = f'{prefix}quality-metrics'
    card = f'{prefix}metric-card'
    grid = f'{prefix}metric-grid'
    title = f'{prefix}metric-title'
    note = f'{prefix}metric-note'
    if not summary:
        return f'<div class="{cls}"><div class="{card}"><b>数据可信度</b><span>数据未就位</span></div></div>'
    status = str(summary.get('status') or 'unknown').lower()
    status_label = {
        'ok': '正常', 'degraded': '降级', 'blocked': '阻断',
        'unavailable': '不可用', 'non_trading_day': '非交易日',
    }.get(status, '未评估')
    total = summary.get('market_total')
    covered = summary.get('market_covered')
    coverage = (f'{float(covered) / float(total) * 100:.2f}%'
                if isinstance(total, (int, float)) and total > 0 and isinstance(covered, (int, float))
                else '数据未就位')
    labels = _MODULE_LABELS
    modules = summary.get('modules') if isinstance(summary.get('modules'), dict) else {}
    module_items = []
    for name, item in modules.items():
        if not isinstance(item, dict):
            continue
        state = str(item.get('status') or 'unknown').lower()
        state_label = _MODULE_STATUS_LABELS.get(state, '未评估')
        effective = item.get('effective_coverage_pct', item.get('coverage_pct'))
        coverage_text = f'{float(effective):.2f}%' if isinstance(effective, (int, float)) else '—'
        module_items.append(f'{_esc(labels.get(name, name))}：{_esc(state_label)} {coverage_text}')
    reasons = [_esc(str(x)) for x in (summary.get('reasons') or [])[:3] if str(x).strip()]
    reason_text = '；'.join(reasons) if reasons else '未发现额外质量告警'
    legacy_items = []
    if summary.get('name_conflicts') is not None:
        legacy_items.append(f'名称冲突 {summary.get("name_conflicts")}')
    if summary.get('limit_pool_status'):
        legacy_items.append(f'涨停池状态：{summary.get("limit_pool_status")}')
    if summary.get('limit_pool_source'):
        legacy_items.append(f'涨停池来源：{summary.get("limit_pool_source")}')
    legacy_html = ''.join(f'<span>{_esc(item)}</span>' for item in legacy_items)
    return f'''
    <div class="{cls}">
      <div class="{title}">数据可信度</div>
      <div class="{grid}">
        <span>状态：{_esc(status_label)}</span>
        <span>市场范围：{_esc(summary.get('market_scope') or '沪深北全A')}</span>
        <span>市场覆盖：{_esc(str(covered) if covered is not None else '—')} / {_esc(str(total) if total is not None else '—')}（{_esc(coverage)}）</span>
        <span>可发布范围：{_esc(summary.get('publication_mode') or '—')}</span>
        <span>源失败模块：{_esc(str(summary.get('source_failure', 0)))}</span>
        <span>陈旧模块：{_esc(str(summary.get('stale', 0)))}</span>
        <span>缺失字段：{_esc(str(summary.get('missing', 0)))}</span>
        {legacy_html}
      </div>
      <div class="{note}">{'；'.join(module_items) if module_items else '模块状态未提供'}<br/>原因：{reason_text}</div>
    </div>'''


def _lianban_review_html(ctx: dict, prefix: str = '') -> str:
    review = ctx.get('lianban_review') if isinstance(ctx.get('lianban_review'), dict) else {}
    cls = f'{prefix}quality-metrics'
    card = f'{prefix}metric-card'
    grid = f'{prefix}metric-grid'
    title = f'{prefix}metric-title'
    note = f'{prefix}metric-note'
    if not review:
        return f'<div class="{cls}"><div class="{card}"><b>连板复盘</b><span>数据未就位</span></div></div>'
    counts = review.get('board_counts') if isinstance(review.get('board_counts'), dict) else {}
    first = review.get('first_board_to_second') if isinstance(review.get('first_board_to_second'), dict) else {}
    streak = review.get('streak_pool_promotion') if isinstance(review.get('streak_pool_promotion'), dict) else {}
    negative = review.get('negative_feedback') if isinstance(review.get('negative_feedback'), dict) else {}
    status = {
        'ok': '样本充分',
        'partial': '部分可用',
        'insufficient': '样本不足',
    }.get(str(review.get('status') or 'insufficient'), '样本不足')
    available_metrics = review.get('available_metrics') or []
    missing_metrics = review.get('missing_metrics') or []
    available_text = '、'.join(str(item) for item in available_metrics) or '—'
    missing_text = '、'.join(str(item) for item in missing_metrics) or '—'
    return f'''
    <div class="{cls}">
      <div class="{title}">连板复盘</div>
      <div class="{grid}">
        <span>首板：{_esc(str(review.get('first_board_count', counts.get(1, 0))))}</span>
        <span>二板：{_esc(str(review.get('second_board_count', counts.get(2, 0))))}</span>
        <span>三板：{_esc(str(counts.get(3, 0)))}</span>
        <span>四板：{_esc(str(counts.get(4, 0)))}</span>
        <span>首板→二板：{_esc(first.get('text') or '样本不足')}</span>
        <span>昨日连板池样本/分母：{_esc(str(review.get('streak_pool_trials', review.get('streak_pool_sample_size', 0))))}</span>
        <span>今日仍连板：{_esc(str(review.get('streak_pool_current_count', 0)))}</span>
        <span>昨日连板池晋级率：{_esc(streak.get('text') or '样本不足')}</span>
        <span>负反馈：{_esc(negative.get('text') or '样本不足')}</span>
        <span>状态：{_esc(status)}</span>
        <span>可用指标：{_esc(available_text)}</span>
        <span>缺失指标：{_esc(missing_text)}</span>
      </div>
      <div class="{note}">{_esc(review.get('conclusion') or '连板复盘结论未就位')}；所有晋级率均展示真实前后交易日匹配分母。{_esc(review.get('sample_note') or '')}</div>
    </div>'''


def _mainline_review_from_ctx(ctx: dict) -> dict:
    """Return the unified mainline review, with a legacy concentration fallback."""
    review = ctx.get('mainline_review') if isinstance(ctx.get('mainline_review'), dict) else None
    if review:
        return review
    metrics = ctx.get('mainline_concentration') if isinstance(ctx.get('mainline_concentration'), dict) else None
    if metrics:
        return build_mainline_review(
            metrics,
            limit_up_count=ctx.get('zt'),
            attribution_source=ctx.get('sector_source') or '未声明',
        )
    return {}


def _mainline_review_html(ctx: dict, prefix: str = '') -> str:
    review = _mainline_review_from_ctx(ctx)
    cls = f'{prefix}quality-metrics'
    card = f'{prefix}metric-card'
    grid = f'{prefix}metric-grid'
    title = f'{prefix}metric-title'
    note = f'{prefix}metric-note'
    if not review:
        return f'<div class="{cls}"><div class="{card}"><b>主线复盘</b><span>数据未就位</span></div></div>'
    top3 = []
    for row in review.get('top3') or []:
        if isinstance(row, dict) and row.get('name'):
            share = _metric_rate_text(row.get('share'), default='—')
            top3.append(f'{_esc(row.get("name"))} {share}')
    hhi = review.get('hhi')
    hhi_text = f'{float(hhi):.3f}' if isinstance(hhi, (int, float)) else '—'
    coverage = review.get('attribution_coverage_pct')
    coverage_text = f'{float(coverage):.2f}%' if isinstance(coverage, (int, float)) else '—'
    level = {'strong': '强结论', 'conditional': '条件性结论', 'insufficient': '覆盖不足'}.get(
        str(review.get('conclusion_level') or 'insufficient'), '覆盖不足')
    return f'''
    <div class="{cls}">
      <div class="{title}">主线复盘 · 主线集中度</div>
      <div class="{grid}">
        <span>领先方向：{_esc(review.get('top1') or '数据未就位')}</span>
        <span>Top 3：{'、'.join(top3) if top3 else '数据未就位'}</span>
        <span>HHI：{_esc(hhi_text)}</span>
        <span>权威涨停事实池：{_esc(str(review.get('authoritative_count') if review.get('authoritative_count') is not None else review.get('limit_up_count') if review.get('limit_up_count') is not None else '—'))}</span>
        <span>已归因样本：{_esc(str(review.get('attributed_count') if review.get('attributed_count') is not None else review.get('lianban_count') if review.get('lianban_count') is not None else '—'))}</span>
        <span>未归因样本：{_esc(str(review.get('unattributed_count') if review.get('unattributed_count') is not None else '—'))}</span>
        <span>归因覆盖率：{_esc(coverage_text)}</span>
        <span>结论等级：{_esc(level)}</span>
        <span>归因来源：{_esc(review.get('attribution_source') or '未声明')}</span>
      </div>
      <div class="{note}">{_esc(review.get('conclusion') or '主线复盘结论未就位')}；覆盖不足时不可外推全市场。</div>
    </div>'''


def _review_closure_html(ctx: dict, prefix: str = '') -> str:
    """Render the daily review loop from the shared ReportContext."""
    daily_delta = ctx.get('daily_delta') if isinstance(ctx.get('daily_delta'), dict) else {}
    progression = ctx.get('progression_chain') if isinstance(ctx.get('progression_chain'), dict) else {}
    prediction = ctx.get('prediction_review') if isinstance(ctx.get('prediction_review'), dict) else {}
    lineage = ctx.get('lineage') if isinstance(ctx.get('lineage'), dict) else {}
    quality = ctx.get('data_quality') if isinstance(ctx.get('data_quality'), dict) else {}
    modules = quality.get('modules') if isinstance(quality.get('modules'), dict) else {}

    panel_style = ('background:rgba(22,27,34,.72);border:1px solid rgba(48,54,61,.85);'
                   'border-radius:12px;padding:14px 16px;min-width:0')
    title_style = 'font-size:14px;font-weight:800;color:#f0f6fc;margin-bottom:10px'
    muted_style = 'color:#8b949e;font-size:12px;line-height:1.6'
    item_style = 'color:#c9d1d9;font-size:12.5px;line-height:1.65'
    warning_style = 'color:#d29922;font-size:12px;line-height:1.65;margin-top:7px'

    delta_items = []
    for row in list(daily_delta.get('highlights') or [])[:5]:
        if not isinstance(row, dict):
            continue
        label = _esc(row.get('label') or '指标')
        current = _esc(_fmt(row.get('current')))
        previous = _esc(_fmt(row.get('previous')))
        delta = row.get('delta')
        delta_text = f'（{delta:+g}）' if isinstance(delta, (int, float)) else ''
        delta_items.append(
            f'<div style="{item_style}"><b>{label}</b>：{previous} → {current}{_esc(delta_text)}</div>'
        )
    if not delta_items:
        delta_items.append(
            f'<div style="{muted_style}">缺少上一交易日结构化快照；历史 HTML 不作为计算源。</div>'
        )

    limit_pool = daily_delta.get('limit_pool') if isinstance(daily_delta.get('limit_pool'), dict) else {}
    limit_pool_items = []
    if limit_pool.get('available'):
        counts = limit_pool.get('counts') if isinstance(limit_pool.get('counts'), dict) else {}
        limit_pool_items.extend([
            f'<div style="{item_style}"><b>涨停池变化</b>：新增 {_esc(_fmt(counts.get("new"), "0"))}（新增首板 {_esc(_fmt(counts.get("new_first_board"), "0"))}） · '
            f'晋级 {_esc(_fmt(counts.get("promoted"), "0"))} · 断板 {_esc(_fmt(counts.get("broken"), "0"))} · '
            f'消失 {_esc(_fmt(counts.get("missing"), "0"))} · 未变 {_esc(_fmt(counts.get("unchanged"), "0"))}</div>'
        ])
        for key, label in (('new_first_board', '新增首板'), ('promoted', '晋级'), ('broken', '断板')):
            rows = [row for row in list(limit_pool.get(key) or [])[:3] if isinstance(row, dict)]
            if rows:
                names = '、'.join(_esc(row.get('name') or row.get('code') or '未知标的') for row in rows)
                limit_pool_items.append(f'<div style="{muted_style}">{label}：{names}</div>')
    else:
        reason = limit_pool.get('reason') or '缺少结构化逐股快照'
        limit_pool_items.append(f'<div style="{warning_style}">涨停池逐股变化不可用：{_esc(reason)}</div>')

    status_labels = {
        'promoted': '晋级',
        'broken_positive': '断板收红',
        'broken_negative': '断板收跌',
        'limit_down': '跌停反馈',
        'suspended': '停牌',
        'missing': '当日状态缺失',
    }
    progression_items = []
    for row in list(progression.get('rows') or [])[:12]:
        if not isinstance(row, dict):
            continue
        name = _esc(row.get('name') or row.get('code') or '未知标的')
        previous_height = row.get('previous_height')
        current_height = row.get('current_height')
        status = status_labels.get(str(row.get('status') or ''), str(row.get('status') or '待核验'))
        height_text = f'{_fmt(previous_height)}板 → {_fmt(current_height)}板' if current_height else f'{_fmt(previous_height)}板'
        progression_items.append(
            f'<div style="{item_style}"><b>{name}</b> · {_esc(status)} · {_esc(height_text)}</div>'
        )
    if not progression_items:
        progression_items.append(f'<div style="{muted_style}">昨日梯队暂无跟踪样本。</div>')

    lineage_items = []
    lineage_keys = sorted(set(lineage) | set(modules), key=lambda key: _module_sort_key(key, modules))
    for key in lineage_keys[:12]:
        meta = lineage.get(key) if isinstance(lineage.get(key), dict) else {}
        module = modules.get(key) if isinstance(modules.get(key), dict) else {}
        display = dict(meta)
        display.update(module)
        source = meta.get('source') or module.get('source') or '未声明'
        status = str(module.get('status') or meta.get('status') or 'unknown').lower()
        status_text = _MODULE_STATUS_LABELS.get(status, '未评估')
        coverage_text = _module_coverage_text(display)
        coverage_suffix = f' · {coverage_text}' if coverage_text else ''
        label = _MODULE_LABELS.get(key, key)
        lineage_items.append(
            f'<div style="{item_style}"><b>{_esc(label)}</b>：{_esc(source)} · {_esc(status_text)}{_esc(coverage_suffix)}</div>'
        )
    if not lineage_items:
        lineage_items.append(f'<div style="{muted_style}">本次产物未附带模块血缘。</div>')

    prediction_count = prediction.get('prediction_count', prediction.get('total', 0))
    matured_count = prediction.get('matured_count', prediction.get('matured', 0))
    pending_count = prediction.get('pending_count', prediction.get('pending', 0))
    incomplete_count = prediction.get('incomplete_count', prediction.get('incomplete', 0))
    scored_count = prediction.get('scored_count')
    hit_rate = prediction.get('hit_rate')
    confidence = prediction.get('confidence_interval') if isinstance(prediction.get('confidence_interval'), dict) else {}
    brier = prediction.get('brier_score')
    brier_text = f' · Brier {_fmt(brier)}' if brier is not None else ''
    scored_text = f' · 可评分 {_fmt(scored_count, "0")} 条' if scored_count is not None else ''
    rate_text = ''
    if hit_rate is not None:
        rate_text = f' · T+3结果 {_fmt(float(hit_rate) * 100, "—")}% '
        if confidence.get('lower') is not None and confidence.get('upper') is not None:
            rate_text += f'（Wilson {_fmt(float(confidence["lower"]) * 100, "—")}–{_fmt(float(confidence["upper"]) * 100, "—")}%）'
    prediction_html = (
        f'<div style="{item_style}">历史记录 {_esc(_fmt(prediction_count, "0"))} 条 · '
        f'已到期 {_esc(_fmt(matured_count, "0"))} 条 · 部分回填 {_esc(_fmt(incomplete_count, "0"))} 条 · '
        f'待验证 {_esc(_fmt(pending_count, "0"))} 条'
        f'{_esc(scored_text)}{_esc(rate_text)}{_esc(brier_text)}</div>'
    )

    extra_panels = []
    reconciliation = _find_limit_pool_reconciliation(modules, lineage)
    authoritative = reconciliation.get('authoritative_count')
    matched = reconciliation.get('matched_count')
    if authoritative is not None and matched is not None:
        coverage = reconciliation.get('classification_coverage_pct')
        if coverage is None and authoritative:
            coverage = round(float(matched) * 100 / float(authoritative), 2)
        reconciliation_html = (
            f'<div style="{item_style}">涨停事实池：{_esc(_fmt(authoritative, "0"))} 只</div>'
            f'<div style="{item_style}">题材归因命中：{_esc(_fmt(matched, "0"))} / {_esc(_fmt(authoritative, "0"))}'
            f'（{_esc(_fmt(coverage, "0"))}%）</div>'
            f'<div style="{item_style}">尚未归因：{_esc(_fmt(reconciliation.get("fupan_only_count"), "0"))} 只</div>'
            f'<div style="{item_style}">分类池独有：{_esc(_fmt(reconciliation.get("cls_only_count"), "0"))} 只</div>'
            f'<div style="{warning_style}">事实池完整不等于题材归因完整；题材结论按已命中样本降级解释。</div>'
        )
        extra_panels.append(
            f'<div style="{panel_style}"><div style="{title_style}">涨停事实与题材归因</div>{reconciliation_html}</div>'
        )

    ai_module = modules.get('ai') if isinstance(modules.get('ai'), dict) else {}
    ai_meta = lineage.get('ai') if isinstance(lineage.get('ai'), dict) else {}
    ai_status = str(ai_module.get('status') or ai_meta.get('status') or '').lower()
    if ai_status and ai_status != 'ok':
        ai_errors = ai_module.get('errors') or ai_meta.get('errors') or []
        ai_reason = ai_errors[0] if ai_errors else (ai_module.get('reason') or ai_meta.get('reason') or '未返回可核验的结构化结果')
        ai_html = (
            f'<div style="{item_style}"><b>AI 研判未生成</b>：{_esc(ai_reason)}。</div>'
            f'<div style="{warning_style}">本页保留规则计算与事实复盘，不以规则文本冒充 AI 输出。</div>'
        )
        extra_panels.append(
            f'<div style="{panel_style}"><div style="{title_style}">AI 研判状态</div>{ai_html}</div>'
        )

    block = f'{prefix}review-closure'
    return f'''
    <div class="{block}" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px;margin:16px 0 20px">
      <div style="{panel_style}"><div style="{title_style}">今日相对昨日</div>{''.join(delta_items)}{''.join(limit_pool_items)}</div>
      <div style="{panel_style}"><div style="{title_style}">昨日连板反馈</div>{''.join(progression_items)}</div>
      <div style="{panel_style}"><div style="{title_style}">数据来源与质量</div>{''.join(lineage_items)}</div>
      <div style="{panel_style}"><div style="{title_style}">历史预测复盘</div>{prediction_html}</div>
      {''.join(extra_panels)}
    </div>'''
def generate_dashboard_html(ctx: dict) -> str:
    """把 ctx 渲染成一份完整的看板 HTML (single-file, 无外部依赖)."""
    # 数据取值 + 兜底
    date_str = ctx.get('date_str') or datetime.now().strftime('%Y-%m-%d')
    state = ctx.get('market_state') if isinstance(ctx.get('market_state'), dict) else build_market_state(ctx.get('data_quality'))
    policy = _publication_policy(ctx)
    mode = str(policy['mode'])
    blocked = bool(policy['facts_only'])
    observation_only = bool(policy['observation_only'])
    scene = ctx.get('scene', '中性震荡')
    action = ctx.get('action', '结构博弈 / 主线为纲')
    level = ctx.get('level', '中性')
    color = ctx.get('color', '#d29922')
    position = ctx.get('position', '5成仓位')
    win_rate = ctx.get('win_rate')
    desc = ctx.get('desc', '')
    if blocked:
        scene = state.get('label', '数据阻断')
        action = '数据待核验，仅展示事实'
        level = f"低置信度 · {state.get('label', '数据待核验')}"
        color = '#8b949e'
        position = '仅事实层'
        win_rate = None
        desc = f"{state.get('reason', '核心数据未通过质量校验')}。当前仅展示已校验事实。"
    elif observation_only:
        scene = state.get('title') or state.get('label', '数据降级')
        action = state.get('action') or '仅观察与条件触发'
        level = f"条件模式 · {scene}"
        color = state.get('color') or '#d29922'
        position = '条件性观察'
        win_rate = None
        desc = f"{state.get('reason', '数据处于降级状态')}。当前展示条件性观察。"

    curr_h = int(ctx.get('curr_h', 0) or 0)
    prev_h = int(ctx.get('prev_h', 0) or 0)
    pressure_5d = int(ctx.get('pressure_5d', 0) or 0)
    zt = int(ctx.get('zt', 0) or 0)
    dt = int(ctx.get('dt', 0) or 0)
    breadth = ctx.get('breadth_ratio', ctx.get('ad_ratio'))
    ad_ratio = ctx.get('advance_decline_ratio')
    breadth_str = _ratio_text(breadth)
    ad_str = _ratio_text(ad_ratio)
    ladder = ctx.get('ladder')
    h3 = ctx.get('h3', 0)
    h4 = ctx.get('h4', 0)
    h5 = ctx.get('h5', 0)
    h6p = ctx.get('h6p', 0)
    ladder_metrics = _ladder_view(ctx, curr_h, h3, h4, h5, h6p)
    h3 = ladder_metrics.get('h3', h3)
    h4 = ladder_metrics.get('h4', h4)
    h5 = ladder_metrics.get('h5', h5)
    h6p = ladder_metrics.get('h6p', h6p)
    ladder = ladder_metrics.get('ladder', ladder)

    zt_prev = int(ctx.get('zt_prev', 0) or 0)
    zt_boom = (zt / zt_prev) if zt_prev > 0 else None
    focus_df = ctx.get('focus_df') if policy['allow_focus_pool'] else None

    scenarios = _prepare_scenarios(
        ctx, curr_h, prev_h, focus_df,
        breadth_ratio=breadth, zt=zt, dt=dt, pressure_5d=pressure_5d,
        ladder=ladder, h5=h5,
    )
    scenarios = _sanitize_scenarios_for_publication(scenarios, mode)
    history_cases = ctx.get('history_cases') or []

    # 三因子交叉表
    ap_ad_ok = '✅ 达标' if isinstance(breadth, (int, float)) and breadth > 0.65 else (
        '⚠️ 中档' if isinstance(breadth, (int, float)) and breadth >= 0.5 else '❌ 未达')
    ap_ladder_ok = '✅ 达标' if isinstance(ladder, (int, float)) and ladder >= 12 else '❌ 未达'
    breakout_ok = '✅ 达标' if curr_h > pressure_5d else '❌ 未达'
    factor_rows = ''.join([
        _factor_row('① 空间板突破 5 日压力',
                    f'{curr_h}板 vs 前压力 {pressure_5d}板', breakout_ok,
                    f'昨断 {curr_h - prev_h:+d} 板'),
        _factor_row('② 上涨占比 > 0.65 (A+ 门槛)',
                    breadth_str, ap_ad_ok,
                    f'涨停 {zt}, 跌停 {dt} · 涨跌比 {ad_str}'),
        _factor_row('③ 梯队分 ≥ 12 (A+ 门槛)',
                    f'{ladder}分' if ladder is not None else '—', ap_ladder_ok,
                    f'3板 {h3} / 4板 {h4} / 5板 {h5} / 6+板 {h6p}'),
    ])

    base_scen = _mark_base_scenario(scenarios)
    scen_cards = ''.join(_scen_card(s) for s in scenarios)
    hist_rows = ''.join(_history_row(c) for c in history_cases) or (
        '<tr><td colspan="8" style="color:#6e7681;padding:20px;">暂无历史同型样本 (需累计更多回测)</td></tr>')

    # 明日核心股票池表 (从 focus_df 拆桶后逐只列出, 附真实催化)
    focus_buckets = _split_focus_pool(focus_df) if policy['allow_focus_pool'] else {'space': [], 'midcore': []}
    # observation/facts_only 不接收主流程新拉取的催化；
    # 若调用方传入已有催化，仍显示其可追溯状态。
    focus_catalysts = ctx.get('focus_catalysts') or {}
    focus_rows_html = _render_focus_table(focus_buckets, catalysts=focus_catalysts, mode=mode)
    quality_html = _quality_html(ctx)
    ladder_quality_html = _ladder_quality_html(ctx)
    data_credibility_html = _data_credibility_html(ctx)
    lianban_review_html = _lianban_review_html(ctx)
    mainline_review_html = _mainline_review_html(ctx)
    review_closure_html = _review_closure_html(ctx)
    historical_outcomes_only = _historical_outcomes_only(ctx)
    if historical_outcomes_only:
        scenario_heading = '当前策略 · 历史结果对照'
        scenario_sub = '历史结果率仅作研究参考'
    elif policy['observation_only']:
        scenario_heading = '观察与条件触发'
        scenario_sub = '满足条件后再评估，不构成无条件动作'
    else:
        scenario_heading, scenario_sub = '明日 T+1 · 4 情形决策树', '概率最高者为基准情形'
    scenario_block = '' if policy['facts_only'] else (
        f'<div class="section-title">{scenario_heading}<span class="st-sub">{scenario_sub}</span></div>'
        f'<div class="scenario-tree">{scen_cards}</div>'
    )
    footer_note = (
        '数据质量未通过校验，仅展示事实与来源状态。'
        if policy['facts_only']
        else '数据基于 173 天历史回测, 胜率为经验统计, 仅供研究参考, 不构成投资建议'
    )

    # 情绪温度计 (0-100) — 顶部一眼读盘面冷热
    senti_score, senti_label, senti_color = _sentiment_score(
        breadth, curr_h, pressure_5d, zt, dt, zt_prev)

    # 重点提炼带 — 把"明天最可能发生 + 该干什么 + 首选标的"压成一句,
    # 左箱=基准决策, 右箱=情绪温度计, 一眼看结论。
    top_pick = ''
    _sp = focus_buckets.get('space', [])
    if _sp:
        top_pick = _fmt_stock(_sp[0])
    base_name = _esc(base_scen.get('name', '')) if base_scen else '—'
    base_prob = _esc(base_scen.get('prob', '')) if base_scen else ''
    base_pos = _esc(base_scen.get('pos', '')) if base_scen else _esc(position)
    base_first = _esc(base_scen['items'][0]) if (base_scen and base_scen.get('items')) else _esc(action)
    pick_html = f' — <span class="pk">首选 {_esc(top_pick)}</span>' if top_pick and policy['decision_ready'] else ''
    if policy['facts_only']:
        headline_label, headline_value, headline_sub = '可用范围', '仅事实层', '当前仅展示已校验事实'
    elif historical_outcomes_only:
        headline_label, headline_value, headline_sub = '重点 · 当前策略', base_name, '历史结果率仅作研究参考'
    elif policy['observation_only']:
        headline_label, headline_value, headline_sub = '可用范围', '观察与条件触发', '当前展示条件性观察'
    else:
        headline_label, headline_value, headline_sub = f'重点 · 明日基准情形 {base_prob}', base_name, f'{base_first} · 建议仓位 {base_pos}'
    headline_html = f'''
    <div class="headline">
      <div class="box primary">
        <div class="lbl">{headline_label}</div>
        <div class="big">{headline_value}{pick_html}</div>
        <div class="sub2">{headline_sub}</div>
      </div>
      <div class="box gauge-wrap">
        <div class="lbl">盘面情绪温度</div>
        <div class="gauge-score" style="color:{senti_color};">{senti_score}</div>
        <div class="gauge-track"><div class="gauge-pin" style="left:{senti_score}%;"></div></div>
        <div class="gauge-legend"><span>冰点</span><span>中性</span><span>亢奋</span></div>
        <div class="gauge-mood" style="color:{senti_color};">{senti_label}</div>
      </div>
    </div>'''

    # 今日操作口令 — 从三条实证规律里挑当日命中的动作项
    if policy['facts_only']:
        playbook_html = '<div class="quality-blocked-note">数据待核验：当前仅展示已校验事实。</div>'
    elif policy['observation_only']:
        playbook_html = '<div class="quality-blocked-note">数据降级：当前展示条件性观察。</div>'
    else:
        playbook_html = _render_playbook(_build_playbook(curr_h, zt, breadth, h5, date_str))

    wr_color = _win_rate_color(win_rate)
    wr_str = f'{win_rate * 100:.0f}%' if isinstance(win_rate, (int, float)) else '—'
    wr_evidence = _win_rate_stat_text(ctx, win_rate)

    # KPI 板块
    boom_str = f'×{zt_boom:.2f}' if zt_boom else '—'
    kpi_html = f'''
    <div class="grid">
      <div class="card">
        <h3>空间板</h3>
        <div class="kpi red">{curr_h}板</div>
        <div class="hint">前 5 日压力位 {pressure_5d}板 · 昨 {prev_h}板 ({curr_h - prev_h:+d})</div>
      </div>
      <div class="card">
        <h3>涨停家数</h3>
        <div class="kpi red">{zt}</div>
        <div class="hint">昨日 {zt_prev} · 环比 {boom_str}</div>
      </div>
      <div class="card">
        <h3>跌停家数</h3>
        <div class="kpi green">{dt}</div>
        <div class="hint">亏钱效应阈值: > 15 转防守</div>
      </div>
      <div class="card">
        <h3>上涨占比</h3>
        <div class="kpi yellow">{breadth_str}</div>
        <div class="hint">上涨 {ctx.get('up', '—')} / 下跌 {ctx.get('down', '—')} · A+ 门槛 &gt; 0.65</div>
      </div>
      <div class="card">
        <h3>涨跌比</h3>
        <div class="kpi yellow">{ad_str}</div>
        <div class="hint">上涨家数 ÷ 下跌家数</div>
      </div>
      <div class="card">
        <h3>梯队分</h3>
        <div class="kpi blue">{_fmt(ladder)}</div>
        <div class="hint">h3×1 + h4×2 + h5×3 + h6+×4</div>
      </div>
      <div class="card">
        <h3>{_esc(ladder_metrics.get('progression_label', '突破昨日最高高度占比'))}</h3>
        <div class="kpi blue">{_esc(ladder_metrics.get('progression_text', '样本不足'))}</div>
        <div class="hint">分子：突破昨日最高板的个股数 · 分母：今日有效梯队个股数</div>
      </div>
      <div class="card">
        <h3>高度断层</h3>
        <div class="kpi" style="color:{'#ff4444' if ladder_metrics.get('gap_risk') else '#3fb950'};">{_esc(ladder_metrics.get('gap_text', '无明显断层'))}</div>
        <div class="hint">风险等级：{_esc(ladder_metrics.get('gap_risk_label', '低'))} · 最高 {ladder_metrics.get('height', curr_h)}板</div>
      </div>
      <div class="card">
        <h3>同型历史命中率 (T+3)</h3>
        <div class="kpi" style="color:{wr_color};">{wr_str if wr_str != '—' else '样本不足'}</div>
        <div class="hint">{_esc(wr_evidence)} · {_esc(level)}</div>
      </div>
    </div>'''

    css = '''
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      background: #0d1117; color: #e6edf3;
      font-family: 'Microsoft YaHei', 'PingFang SC', -apple-system, sans-serif;
      padding: 24px; line-height: 1.6; min-height: 100vh;
    }
    .wrap { max-width: 1200px; margin: 0 auto; }
    .back {
      display: inline-block; padding: 6px 14px; margin-bottom: 12px;
      background: #161b22; color: #58a6ff; text-decoration: none;
      border: 1px solid #30363d; border-radius: 6px; font-size: 13px;
    }
    .back:hover { border-color: #58a6ff; }
    .hero {
      background: linear-gradient(135deg, color-mix(in srgb, var(--sc) 20%, transparent),
                                       color-mix(in srgb, var(--sc) 8%, transparent));
      border: 2px solid var(--sc); border-radius: 16px;
      padding: 24px 28px; margin-bottom: 20px;
      display: flex; align-items: center; justify-content: space-between;
      gap: 20px; flex-wrap: wrap;
      box-shadow: 0 0 30px color-mix(in srgb, var(--sc) 22%, transparent);
    }
    .hero .left h1 { font-size: 28px; font-weight: 800; color: var(--sc); margin-bottom: 6px; }
    .hero .left .sub { color: #c9d1d9; font-size: 14px; }
    .hero .left .date { color: #8b949e; font-size: 13px; margin-top: 4px; }
    .hero .right {
      text-align: right;
      background: color-mix(in srgb, var(--sc) 22%, transparent);
      padding: 14px 22px; border-radius: 10px;
      border: 1px solid var(--sc);
    }
    .hero .right .pos-label { color: color-mix(in srgb, var(--sc) 70%, #ffffff);
                              font-size: 13px; margin-bottom: 4px; }
    .hero .right .pos-value { font-size: 24px; font-weight: 800; color: #fff; }
    .hero .right .win { color: #ffcc00; font-size: 13px; margin-top: 4px; }
    .hero-desc {
      grid-column: 1 / -1; color: #c9d1d9; font-size: 14px;
      padding-top: 12px; margin-top: 12px;
      border-top: 1px solid color-mix(in srgb, var(--sc) 35%, transparent);
      width: 100%;
    }
    .grid {
      display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 14px; margin-bottom: 24px;
    }
    .card {
      background: rgba(22, 27, 34, 0.7);
      border: 1px solid rgba(48, 54, 61, 0.8);
      border-radius: 12px; padding: 16px 18px;
    }
    .card h3 {
      font-size: 13px; color: #d29922; margin-bottom: 8px;
      border-left: 3px solid #d29922; padding-left: 8px;
    }
    .kpi { font-size: 24px; font-weight: 800; color: #fff; }
    .kpi.green { color: #3fb950; }
    .kpi.red { color: #ff4444; }
    .kpi.yellow { color: #ffcc00; }
    .kpi.blue { color: #58a6ff; }
    .hint { color: #8b949e; font-size: 12px; margin-top: 4px; }
    .quality-card { margin: 10px 0 24px; padding: 12px 16px; border: 1px solid rgba(48,54,61,0.8);
      border-radius: 10px; background: rgba(22,27,34,0.65); }
    .quality-title { color: #c9d1d9; font-size: 13px; margin-bottom: 8px; }
    .quality-title b { color: #3fb950; }
    .quality-items { display: flex; flex-wrap: wrap; gap: 8px 18px; color: #8b949e; font-size: 12px; }
    .quality-layers { display: flex; flex-wrap: wrap; gap: 6px 18px; margin-top: 7px; color: #c9d1d9; font-size: 12px; }
    .quality-issues { margin-top: 7px; color: #d29922; font-size: 12px; line-height: 1.5; }

    /* 重点提炼带: 一眼看结论 */
    .headline {
      display: grid; grid-template-columns: 1.4fr 1fr; gap: 16px;
      margin-bottom: 22px;
    }
    .headline .box {
      background: rgba(22, 27, 34, 0.7);
      border: 1px solid rgba(48, 54, 61, 0.8);
      border-radius: 14px; padding: 18px 20px;
    }
    .headline .box.primary { border-color: var(--sc);
      background: linear-gradient(135deg, color-mix(in srgb, var(--sc) 14%, transparent), transparent); }
    .headline .lbl { font-size: 12px; color: #8b949e; text-transform: uppercase;
      letter-spacing: 0.5px; margin-bottom: 8px; }
    .headline .big { font-size: 20px; font-weight: 800; color: #fff; line-height: 1.35; }
    .headline .big .pk { color: var(--sc); }
    .headline .sub2 { color: #c9d1d9; font-size: 13px; margin-top: 8px; line-height: 1.5; }
    /* 情绪温度计 */
    .gauge-wrap { text-align: center; }
    .gauge-track { position: relative; height: 12px; border-radius: 6px; margin: 14px 0 6px;
      background: linear-gradient(90deg, #ff4444 0%, #d29922 45%, #3fb950 100%); }
    .gauge-pin { position: absolute; top: -5px; width: 4px; height: 22px;
      background: #fff; border-radius: 2px; box-shadow: 0 0 6px rgba(255,255,255,0.7);
      transform: translateX(-2px); }
    .gauge-score { font-size: 30px; font-weight: 800; }
    .gauge-legend { display: flex; justify-content: space-between; color: #6e7681;
      font-size: 11px; margin-top: 2px; }
    .gauge-mood { font-size: 13px; margin-top: 6px; font-weight: 700; }

    /* 今日操作口令带: 命中实证规律 → 动作 */
    .playbook { margin-bottom: 22px; }
    .pb-title { font-size: 14px; font-weight: 700; color: #ffcc00;
      margin-bottom: 10px; padding-left: 10px; border-left: 4px solid #ffcc00; }
    .pb-title .pb-sub { font-size: 11px; font-weight: 400; color: #8b949e; margin-left: 8px; }
    .pb-cmd { display: flex; align-items: flex-start; gap: 10px;
      background: rgba(22,27,34,0.7); border: 1px solid rgba(48,54,61,0.8);
      border-left-width: 3px; border-radius: 8px; padding: 10px 14px; margin-bottom: 8px;
      font-size: 13.5px; line-height: 1.5; color: #e6edf3; }
    .pb-icon { font-size: 16px; flex-shrink: 0; line-height: 1.4; }
    .pb-cmd.pb-hot { border-left-color: #ff4444; }
    .pb-cmd.pb-cold { border-left-color: #58a6ff; }
    .pb-cmd.pb-warn { border-left-color: #ff8800; }
    .pb-cmd.pb-ok { border-left-color: #3fb950; }
    .pb-cmd.pb-neutral { border-left-color: #6e7681; }

    .section-title {
      font-size: 18px; font-weight: 700; color: #ffcc00;
      margin: 28px 0 14px; padding-left: 10px;
      border-left: 4px solid #ffcc00;
    }
    .section-title .st-sub { font-size: 12px; font-weight: 400; color: #8b949e; margin-left: 8px; }
    /* 证据折叠区 */
    details.evidence { margin: 24px 0 8px; border: 1px solid rgba(48,54,61,0.6);
      border-radius: 12px; background: rgba(22,27,34,0.4); overflow: hidden; }
    details.evidence > summary { cursor: pointer; padding: 14px 18px; list-style: none;
      font-size: 14px; font-weight: 700; color: #8b949e; user-select: none; }
    details.evidence > summary::-webkit-details-marker { display: none; }
    details.evidence > summary::before { content: '▸ '; color: #58a6ff; }
    details.evidence[open] > summary::before { content: '▾ '; }
    details.evidence > summary:hover { color: #c9d1d9; }
    details.evidence .evidence-body { padding: 0 18px 16px; }
    /* 基准场景高亮 */
    .scen-card.scen-base { box-shadow: 0 0 0 1px currentColor, 0 0 24px color-mix(in srgb, var(--sc) 28%, transparent); }
    .scen-base-badge { display: inline-block; font-size: 10px; font-weight: 700;
      background: var(--sc); color: #0d1117; padding: 1px 7px; border-radius: 4px;
      margin-left: 8px; vertical-align: middle; }
    table.factor, table.history {
      width: 100%; background: rgba(22, 27, 34, 0.7);
      border-radius: 12px; border-collapse: separate;
      border-spacing: 0; overflow: hidden; margin-bottom: 8px;
    }
    table.factor th, table.factor td,
    table.history th, table.history td {
      padding: 11px 14px; border-bottom: 1px solid rgba(48, 54, 61, 0.6);
      text-align: left; font-size: 13.5px;
    }
    table.factor th, table.history th {
      background: rgba(30, 35, 42, 0.9); color: #8b949e;
      font-size: 12px; font-weight: 600; text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    table.history td { text-align: center; }
    table.history tr.win td { color: #3fb950; }
    table.history tr.lose td { color: #ff6666; }
    table.history tr.neutral td { color: #d29922; }
    .check-ok { color: #3fb950; font-weight: 700; }
    .check-fail { color: #ff4444; font-weight: 700; }
    .check-warn { color: #ffcc00; font-weight: 700; }

    .scenario-tree {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 14px;
    }
    .scen-card {
      background: rgba(22, 27, 34, 0.75);
      border: 2px solid; border-radius: 12px;
      padding: 16px 18px;
    }
    .scen-card .head {
      display: flex; align-items: center; justify-content: space-between;
      margin-bottom: 10px;
    }
    .scen-card .name { font-size: 15px; font-weight: 700; }
    .scen-card .prob { font-size: 12px; color: #8b949e; }
    .scen-card .scen-stat { margin: -4px 0 7px; color: #8b949e; font-size: 11px; line-height: 1.45; }
    .scen-card ul { margin-left: 18px; font-size: 13px; color: #e6edf3; line-height: 1.7; }
    .scen-card .pos {
      margin-top: 10px; padding: 6px 10px; border-radius: 6px;
      font-size: 13px; font-weight: 700; display: inline-block;
    }
    .attack { border-color: #ff4444; }
    .attack .name, .attack .pos { color: #ff4444; }
    .attack .pos { background: rgba(255, 68, 68, 0.15); }
    .moderate { border-color: #d29922; }
    .moderate .name, .moderate .pos { color: #d29922; }
    .moderate .pos { background: rgba(210, 153, 34, 0.15); }
    .defense { border-color: #58a6ff; }
    .defense .name, .defense .pos { color: #58a6ff; }
    .defense .pos { background: rgba(88, 166, 255, 0.15); }
    .critical { border-color: #ff8800; }
    .critical .name, .critical .pos { color: #ff8800; }
    .critical .pos { background: rgba(255, 136, 0, 0.15); }

    /* focus pool styles (fp-*) */
    .fp-block { margin-bottom: 20px; }
    .fp-block-title {
      font-size: 15px; font-weight: 700; color: #e6edf3;
      padding: 10px 14px; border-radius: 8px 8px 0 0;
      background: rgba(30, 35, 42, 0.9);
      border-left: 4px solid;
    }
    .fp-space .fp-block-title { border-left-color: #ff4444; color: #ff8888; }
    .fp-midcore .fp-block-title { border-left-color: #58a6ff; color: #79b8ff; }
    .fp-block-sub { color: #8b949e; font-size: 12px; font-weight: 400; margin-left: 10px; }
    table.fp-table {
      width: 100%; background: rgba(22, 27, 34, 0.7);
      border-radius: 0 0 8px 8px; border-collapse: separate;
      border-spacing: 0; overflow: hidden;
    }
    table.fp-table th, table.fp-table td {
      padding: 10px 14px; border-bottom: 1px solid rgba(48, 54, 61, 0.6);
      text-align: left; font-size: 13px; vertical-align: top;
    }
    table.fp-table th {
      background: rgba(30, 35, 42, 0.5); color: #8b949e;
      font-size: 11px; font-weight: 600; text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    table.fp-table tbody tr:hover { background: rgba(88, 166, 255, 0.05); }
    table.fp-table tbody tr:last-child td { border-bottom: none; }
    .fp-name { width: 100px; color: #ffcc00; }
    .fp-plate { width: 130px; color: #79b8ff; font-size: 12px; }
    .fp-entry { color: #c9d1d9; line-height: 1.55; }
    .fp-stop { width: 200px; color: #ff8888; font-size: 12px; }
    .fp-cat { width: 210px; }
    .fp-cat-tag { display: inline-block; font-size: 10.5px; font-weight: 700;
                  color: #0d1117; background: #d29922; padding: 1px 7px;
                  border-radius: 4px; margin-bottom: 3px; }
    .fp-cat-text { color: #c9d1d9; font-size: 11.5px; line-height: 1.45; }

    footer {
      margin-top: 40px; padding-top: 20px; text-align: center;
      color: #6e7681; font-size: 12px;
      border-top: 1px solid rgba(48, 54, 61, 0.6);
    }
    @media (max-width: 640px) {
      .hero { flex-direction: column; align-items: stretch; }
      .hero .right { text-align: left; }
      .fp-plate, .fp-stop { width: auto; }
    }
    '''

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{_esc(date_str)} 复盘决策看板 · 6 场景模型</title>
<style>{css}</style>
</head>
<body>
<div class="wrap" style="--sc:{_esc(color)};">
  <a class="back" href="../index.html">← 返回首页</a>
  <div class="hero">
    <div class="left">
      <h1>{_esc(scene)} · {_esc(action)}</h1>
      <div class="sub">{_esc(level)}</div>
      <div class="date">报告日期 {_esc(date_str)}</div>
    </div>
    <div class="right">
      <div class="pos-label">{"建议仓位" if policy['decision_ready'] else "报告可用范围"}</div>
      <div class="pos-value">{_esc(position if policy['decision_ready'] else ("仅事实层" if policy['facts_only'] else "观察与条件触发"))}</div>
      <div class="win">{("历史 T+3 破新高 " + wr_str) if policy['decision_ready'] else "历史验证暂不展示"}</div>
    </div>
    <div class="hero-desc">{_esc(desc)}</div>
  </div>

  {headline_html}

  {review_closure_html}

  {quality_html}
  {ladder_quality_html}
  {data_credibility_html}
  {lianban_review_html}
  {mainline_review_html}

  {playbook_html}

  {scenario_block}

  <div class="section-title">{"股票池未发布" if policy['facts_only'] else "观察名单（非推荐）" if policy['observation_only'] else "明日核心股票池"}<span class="st-sub">{"数据阻断" if policy['facts_only'] else "仅供观察 · 条件触发" if policy['observation_only'] else "具体标的 · 入场条件 · 防守位"}</span></div>
  {focus_rows_html}

  <details class="evidence">
    <summary>数据佐证 · 盘面因子明细 与 历史同型样本</summary>
    <div class="evidence-body">
      {kpi_html}
      <div class="section-title">场景判定 · 三因子交叉</div>
      <table class="factor">
        <thead><tr><th>因子</th><th>当前读数</th><th>是否达标</th><th>辅助说明</th></tr></thead>
        <tbody>{factor_rows}</tbody>
      </table>
      <div class="section-title">历史同型样本 (T+1/T+2/T+3 走势对照)</div>
      <table class="history">
        <thead><tr>
          <th>日期</th><th>空间板</th><th>涨停</th><th>梯队分</th>
          <th>T+1</th><th>T+2</th><th>T+3</th><th>结局</th>
        </tr></thead>
        <tbody>{hist_rows}</tbody>
      </table>
    </div>
  </details>

  <footer>
    连板情绪分析 · 6 场景数据驱动模型 · 每日自动跑批生成
    <br>{footer_note}
  </footer>
</div>
</body>
</html>'''
    return sanitize_html_for_policy(html, mode)


def save_dashboard(ctx: dict, output_path: str) -> str:
    """把 ctx 渲染成 HTML 并写盘, 返回文件路径."""
    html = generate_dashboard_html(ctx)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return output_path


def generate_dashboard_section(ctx: dict) -> str:
    """把看板渲染为可内嵌进主报告顶部的 HTML section (含独立作用域 CSS).

    所有 class 加 `dbd-` 前缀 (dashboard-embedded 缩写), 避免与主报告的
    .card / .hero / .grid 等类名冲突. 返回一段可以直接插入主报告 body 的
    片段 (含 <style>...</style> + <section class="dbd-wrap">...</section>).
    """
    date_str = ctx.get('date_str') or datetime.now().strftime('%Y-%m-%d')
    state = ctx.get('market_state') if isinstance(ctx.get('market_state'), dict) else build_market_state(ctx.get('data_quality'))
    policy = _publication_policy(ctx)
    mode = str(policy['mode'])
    blocked = bool(policy['facts_only'])
    observation_only = bool(policy['observation_only'])
    scene = ctx.get('scene', '中性震荡')
    action = ctx.get('action', '结构博弈 / 主线为纲')
    level = ctx.get('level', '中性')
    color = ctx.get('color', '#d29922')
    position = ctx.get('position', '5成仓位')
    win_rate = ctx.get('win_rate')
    desc = ctx.get('desc', '')
    if blocked:
        scene = state.get('label', '数据阻断')
        action = '数据待核验，仅展示事实'
        level = f"低置信度 · {state.get('label', '数据待核验')}"
        color = '#8b949e'
        position = '仅事实层'
        win_rate = None
        desc = f"{state.get('reason', '核心数据未通过质量校验')}。当前仅展示已校验事实。"
    elif observation_only:
        scene = state.get('title') or state.get('label', '数据降级')
        action = state.get('action') or '仅观察与条件触发'
        level = f"条件模式 · {scene}"
        color = state.get('color') or '#d29922'
        position = '条件性观察'
        win_rate = None
        desc = f"{state.get('reason', '数据处于降级状态')}。当前展示条件性观察。"

    curr_h = int(ctx.get('curr_h', 0) or 0)
    prev_h = int(ctx.get('prev_h', 0) or 0)
    pressure_5d = int(ctx.get('pressure_5d', 0) or 0)
    zt = int(ctx.get('zt', 0) or 0)
    dt = int(ctx.get('dt', 0) or 0)
    breadth = ctx.get('breadth_ratio', ctx.get('ad_ratio'))
    ad_ratio = ctx.get('advance_decline_ratio')
    breadth_str = _ratio_text(breadth)
    ad_str = _ratio_text(ad_ratio)
    ladder = ctx.get('ladder')
    h3 = ctx.get('h3', 0)
    h4 = ctx.get('h4', 0)
    h5 = ctx.get('h5', 0)
    h6p = ctx.get('h6p', 0)
    zt_prev = int(ctx.get('zt_prev', 0) or 0)
    zt_boom = (zt / zt_prev) if zt_prev > 0 else None
    focus_df = ctx.get('focus_df') if policy['allow_focus_pool'] else None
    # observation/facts_only 不接收主流程新拉取的催化；
    # 若调用方传入已有催化，仍显示其可追溯状态。
    focus_catalysts = ctx.get('focus_catalysts') or {}

    ladder_metrics = _ladder_view(ctx, curr_h, h3, h4, h5, h6p)
    h3 = ladder_metrics.get('h3', h3)
    h4 = ladder_metrics.get('h4', h4)
    h5 = ladder_metrics.get('h5', h5)
    h6p = ladder_metrics.get('h6p', h6p)
    ladder = ladder_metrics.get('ladder', ladder)
    scenarios = _prepare_scenarios(
        ctx, curr_h, prev_h, focus_df,
        breadth_ratio=breadth, zt=zt, dt=dt, pressure_5d=pressure_5d,
        ladder=ladder, h5=h5,
    )
    scenarios = _sanitize_scenarios_for_publication(scenarios, mode)

    # 因子交叉表
    ap_ad_ok = '✅ 达标' if isinstance(breadth, (int, float)) and breadth > 0.65 else (
        '⚠️ 中档' if isinstance(breadth, (int, float)) and breadth >= 0.5 else '❌ 未达')
    ap_ladder_ok = '✅ 达标' if isinstance(ladder, (int, float)) and ladder >= 12 else '❌ 未达'
    breakout_ok = '✅ 达标' if curr_h > pressure_5d else '❌ 未达'

    def _fr(name, value, ok, hint):
        icon_class = {'✅': 'dbd-check-ok', '❌': 'dbd-check-fail',
                      '❓': 'dbd-check-warn', '⚠️': 'dbd-check-warn'}.get(
            ok[:1] if ok else '', 'dbd-check-warn')
        return (f'<tr><td>{_esc(name)}</td><td><b>{_esc(value)}</b></td>'
                f'<td><span class="{icon_class}">{_esc(ok)}</span></td>'
                f'<td>{_esc(hint)}</td></tr>')

    factor_rows = ''.join([
        _fr('① 空间板突破 5 日压力',
            f'{curr_h}板 vs 前压力 {pressure_5d}板', breakout_ok,
            f'昨断 {curr_h - prev_h:+d} 板'),
        _fr('② 上涨占比 > 0.65 (A+ 门槛)',
            breadth_str, ap_ad_ok,
            f'涨停 {zt}, 跌停 {dt} · 涨跌比 {ad_str}'),
        _fr('③ 梯队分 ≥ 12 (A+ 门槛)',
            f'{ladder}分' if ladder is not None else '—', ap_ladder_ok,
            f'3板 {h3} / 4板 {h4} / 5板 {h5} / 6+板 {h6p}'),
    ])

    base_scen = _mark_base_scenario(scenarios)

    def _sc(s):
        items = ''.join(f'<li>{_esc(x)}</li>' for x in s.get('items', []))
        kind = s.get('kind', 'moderate')
        base_cls = ' dbd-scen-base' if s.get('is_base') else ''
        base_badge = '<span class="dbd-scen-base-badge">基准</span>' if s.get('is_base') else ''
        stat_text = _scenario_stat_text(s)
        return (f'<div class="dbd-scen dbd-{kind}{base_cls}">'
                f'<div class="dbd-scen-head"><span class="dbd-scen-name">{_esc(s.get("name", ""))}{base_badge}</span>'
                f'<span class="dbd-scen-prob">{_esc(s.get("prob", ""))}</span></div>'
                f'<div class="dbd-scen-stat">{_esc(stat_text)}</div>'
                f'<ul>{items}</ul>'
                f'<div class="dbd-scen-pos">{_esc(s.get("pos", ""))}</div>'
                f'</div>')

    scen_cards = ''.join(_sc(s) for s in scenarios)

    # 内嵌简版 focus 表 (与独立看板同源, class 加 dbd- 前缀避免冲突)
    def _cat_cell_inline(name):
        """催化列 <td> (dbd- 前缀版): tag + 简短 text; 无数据显示 —"""
        if not focus_catalysts:
            return '<td class="dbd-fp-cat">—</td>'
        item = focus_catalysts.get(name) or {}
        cat = item.get('catalyst') if isinstance(item, dict) else None
        if not cat:
            return '<td class="dbd-fp-cat" style="color:#6e7681;">无近期催化</td>'
        cat = normalize_catalyst(cat)
        body = _esc(cat['text']) or _esc(cat['tag'])
        url = cat['url']
        if url:
            body = (f'<a href="{_esc(url)}" target="_blank" rel="noopener" '
                    f'style="color:inherit;text-decoration:underline dotted;">{body}</a>')
        return (f'<td class="dbd-fp-cat"><span class="dbd-fp-cat-tag">{_esc(cat["tag"])}</span>'
                f'<div class="dbd-fp-cat-text">{body}</div></td>')

    focus_rows_inline = ''
    if policy['facts_only']:
        focus_rows_inline = '<div class="dbd-fp-empty" style="color:#d29922;padding:10px 2px;">股票池未发布（数据阻断）</div>'
    try:
        if policy['allow_focus_pool'] and focus_df is not None and hasattr(focus_df, 'empty') and not focus_df.empty:
            _rows = []
            for _, r in focus_df.iterrows():
                _name = str(r.get("股票", ""))
                _rows.append(
                    f'<tr>'
                    f'<td class="dbd-fp-name">{_esc(_name)}</td>'
                    f'<td class="dbd-fp-plate">{_esc(_clean_plate(str(r.get("板块", ""))) or "—")}</td>'
                    f'<td class="dbd-fp-pool">{_esc(r.get("策略池", ""))}</td>'
                    f'{_cat_cell_inline(_name)}'
                    f'<td class="dbd-fp-entry">{_esc(r.get("入场条件", ""))}</td>'
                    f'<td class="dbd-fp-stop">{_esc(r.get("防守位", ""))}</td>'
                    f'</tr>'
                )
            focus_rows_inline = (
                '<div class="dbd-fp-list-label">' + ('观察名单（非推荐） · 条件触发' if observation_only else '股票池') + '</div>'
                '<table class="dbd-fp-table"><thead><tr>'
                '<th>标的</th><th>板块</th><th>策略池</th><th>近期催化</th><th>入场条件</th><th>防守位</th>'
                '</tr></thead><tbody>' + ''.join(_rows) + '</tbody></table>'
            )
    except Exception:
        pass
    if not focus_rows_inline and focus_catalysts:
        focus_rows_inline = '<div class="dbd-fp-empty" style="color:#6e7681;padding:10px 2px;">暂无核心股票池 · 无近期催化</div>'

    quality_html = _quality_html(ctx, prefix='dbd-')
    ladder_quality_html = _ladder_quality_html(ctx, prefix='dbd-')
    data_credibility_html = _data_credibility_html(ctx, prefix='dbd-')
    lianban_review_html = _lianban_review_html(ctx, prefix='dbd-')
    mainline_review_html = _mainline_review_html(ctx, prefix='dbd-')
    review_closure_html = _review_closure_html(ctx, prefix='dbd-')
    historical_outcomes_only = _historical_outcomes_only(ctx)
    if historical_outcomes_only:
        scenario_heading = '当前策略 · 历史结果对照'
        scenario_sub = '历史结果率仅作研究参考'
    elif policy['observation_only']:
        scenario_heading = '观察与条件触发'
        scenario_sub = '满足条件后再评估，不构成无条件动作'
    else:
        scenario_heading, scenario_sub = '明日 T+1 · 4 情形决策树', '基准情形已高亮'
    scenario_block = '' if policy['facts_only'] else (
        f'<div class="dbd-section-title">{scenario_heading} <span class="dbd-st-sub">{scenario_sub}</span></div>'
        f'<div class="dbd-tree">{scen_cards}</div>'
    )
    wr_color = _win_rate_color(win_rate)
    wr_str = f'{win_rate * 100:.0f}%' if isinstance(win_rate, (int, float)) else '—'
    wr_evidence = _win_rate_stat_text(ctx, win_rate)
    boom_str = f'×{zt_boom:.2f}' if zt_boom else '—'

    # 重点提炼带 + 情绪温度计 (与独立看板同源, dbd- 前缀)
    senti_score, senti_label, senti_color = _sentiment_score(
        breadth, curr_h, pressure_5d, zt, dt, zt_prev)
    if blocked:
        senti_score, senti_label, senti_color = 50, '数据未就位 · 不下结论', '#8b949e'
    _sp = _split_focus_pool(focus_df).get('space', [])
    top_pick = _fmt_stock(_sp[0]) if _sp else ''
    base_name = _esc(base_scen.get('name', '')) if base_scen else '—'
    base_prob = _esc(base_scen.get('prob', '')) if base_scen else ''
    base_pos = _esc(base_scen.get('pos', '')) if base_scen else _esc(position)
    base_first = _esc(base_scen['items'][0]) if (base_scen and base_scen.get('items')) else _esc(action)
    pick_html = f' — <span class="dbd-pk">首选 {_esc(top_pick)}</span>' if top_pick and policy['decision_ready'] else ''
    if policy['facts_only']:
        headline_label, headline_value, headline_sub = '可用范围', '仅事实层', '当前仅展示已校验事实'
    elif historical_outcomes_only:
        headline_label, headline_value, headline_sub = '重点 · 当前策略', base_name, '历史结果率仅作研究参考'
    elif policy['observation_only']:
        headline_label, headline_value, headline_sub = '可用范围', '观察与条件触发', '当前展示条件性观察'
    else:
        headline_label, headline_value, headline_sub = f'重点 · 明日基准情形 {base_prob}', base_name, f'{base_first} · 建议仓位 {base_pos}'
    headline_html = f'''
    <div class="dbd-headline">
      <div class="dbd-hbox dbd-hbox-primary">
        <div class="dbd-hlbl">{headline_label}</div>
        <div class="dbd-hbig">{headline_value}{pick_html}</div>
        <div class="dbd-hsub">{headline_sub}</div>
      </div>
      <div class="dbd-hbox dbd-gauge-wrap">
        <div class="dbd-hlbl">盘面情绪温度</div>
        <div class="dbd-gauge-score" style="color:{senti_color};">{senti_score}</div>
        <div class="dbd-gauge-track"><div class="dbd-gauge-pin" style="left:{senti_score}%;"></div></div>
        <div class="dbd-gauge-legend"><span>冰点</span><span>中性</span><span>亢奋</span></div>
        <div class="dbd-gauge-mood" style="color:{senti_color};">{senti_label}</div>
      </div>
    </div>'''

    # 今日操作口令 (命中实证规律; dbd- 前缀)
    if policy['facts_only']:
        playbook_html = '<div class="dbd-quality-blocked-note">数据待核验：当前仅展示已校验事实。</div>'
    elif policy['observation_only']:
        playbook_html = '<div class="dbd-quality-blocked-note">数据降级：当前展示条件性观察。</div>'
    else:
        playbook_html = _render_playbook(_build_playbook(curr_h, zt, breadth, h5, date_str), p='dbd-')

    kpi_html = f'''
    <div class="dbd-grid">
      <div class="dbd-card"><h4>空间板</h4><div class="dbd-kpi dbd-red">{curr_h}板</div>
        <div class="dbd-hint">5日压力 {pressure_5d}板 · 昨 {prev_h}板 ({curr_h - prev_h:+d})</div></div>
      <div class="dbd-card"><h4>涨停家数</h4><div class="dbd-kpi dbd-red">{zt}</div>
        <div class="dbd-hint">昨日 {zt_prev} · {boom_str}</div></div>
      <div class="dbd-card"><h4>跌停家数</h4><div class="dbd-kpi dbd-green">{dt}</div>
        <div class="dbd-hint">阈值 &gt; 15 转防守</div></div>
      <div class="dbd-card"><h4>上涨占比</h4><div class="dbd-kpi dbd-yellow">{breadth_str}</div>
        <div class="dbd-hint">上涨 {ctx.get('up', '—')} / 下跌 {ctx.get('down', '—')} · A+ 门槛 &gt; 0.65</div></div>
      <div class="dbd-card"><h4>涨跌比</h4><div class="dbd-kpi dbd-yellow">{ad_str}</div>
        <div class="dbd-hint">上涨家数 ÷ 下跌家数</div></div>
      <div class="dbd-card"><h4>梯队分</h4><div class="dbd-kpi dbd-blue">{_fmt(ladder)}</div>
        <div class="dbd-hint">h3×1 + h4×2 + h5×3 + h6+×4</div></div>
      <div class="dbd-card"><h4>{_esc(ladder_metrics.get('progression_label', '突破昨日最高高度占比'))}</h4>
        <div class="dbd-kpi dbd-blue">{_esc(ladder_metrics.get('progression_text', '样本不足'))}</div>
        <div class="dbd-hint">分子：突破昨日最高板的个股数 · 分母：今日有效梯队个股数</div></div>
      <div class="dbd-card"><h4>高度断层</h4><div class="dbd-kpi" style="color:{'#ff4444' if ladder_metrics.get('gap_risk') else '#3fb950'};">{_esc(ladder_metrics.get('gap_text', '无明显断层'))}</div>
        <div class="dbd-hint">风险等级：{_esc(ladder_metrics.get('gap_risk_label', '低'))} · 最高 {ladder_metrics.get('height', curr_h)}板</div></div>
      <div class="dbd-card"><h4>同型历史命中率 (T+3)</h4><div class="dbd-kpi" style="color:{wr_color};">{wr_str if wr_str != '—' else '样本不足'}</div>
        <div class="dbd-hint">{_esc(wr_evidence)} · {_esc(level)}</div></div>
    </div>'''

    css = f'''
    <style>
    .dbd-wrap {{ --dbd-sc: {color}; font-family: inherit; margin: 24px 0 32px; }}
    .dbd-headline {{ display: grid; grid-template-columns: 1.4fr 1fr; gap: 14px; margin-bottom: 18px; }}
    .dbd-hbox {{ background: rgba(22,27,34,0.7); border: 1px solid rgba(48,54,61,0.8);
      border-radius: 12px; padding: 16px 18px; }}
    .dbd-hbox-primary {{ border-color: var(--dbd-sc);
      background: linear-gradient(135deg, color-mix(in srgb, var(--dbd-sc) 14%, transparent), transparent); }}
    .dbd-hlbl {{ font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }}
    .dbd-hbig {{ font-size: 18px; font-weight: 800; color: #fff; line-height: 1.35; }}
    .dbd-hbig .dbd-pk {{ color: var(--dbd-sc); }}
    .dbd-hsub {{ color: #c9d1d9; font-size: 12.5px; margin-top: 7px; line-height: 1.5; }}
    .dbd-gauge-wrap {{ text-align: center; }}
    .dbd-gauge-score {{ font-size: 26px; font-weight: 800; }}
    .dbd-gauge-track {{ position: relative; height: 11px; border-radius: 6px; margin: 12px 0 5px;
      background: linear-gradient(90deg, #ff4444 0%, #d29922 45%, #3fb950 100%); }}
    .dbd-gauge-pin {{ position: absolute; top: -5px; width: 4px; height: 21px; background: #fff;
      border-radius: 2px; box-shadow: 0 0 6px rgba(255,255,255,0.7); transform: translateX(-2px); }}
    .dbd-gauge-legend {{ display: flex; justify-content: space-between; color: #6e7681; font-size: 10px; margin-top: 2px; }}
    .dbd-gauge-mood {{ font-size: 12px; margin-top: 5px; font-weight: 700; }}
    .dbd-scen.dbd-base {{ box-shadow: 0 0 0 1px currentColor, 0 0 22px color-mix(in srgb, var(--dbd-sc) 26%, transparent); }}
    .dbd-base-badge {{ display: inline-block; font-size: 9.5px; font-weight: 700; background: var(--dbd-sc);
      color: #0d1117; padding: 1px 6px; border-radius: 4px; margin-left: 6px; vertical-align: middle; }}
    details.dbd-evidence {{ margin: 18px 0 8px; border: 1px solid rgba(48,54,61,0.6);
      border-radius: 12px; background: rgba(22,27,34,0.4); overflow: hidden; }}
    details.dbd-evidence > summary {{ cursor: pointer; padding: 12px 16px; list-style: none;
      font-size: 13px; font-weight: 700; color: #8b949e; user-select: none; }}
    details.dbd-evidence > summary::-webkit-details-marker {{ display: none; }}
    details.dbd-evidence > summary::before {{ content: '▸ '; color: #58a6ff; }}
    details.dbd-evidence[open] > summary::before {{ content: '▾ '; }}
    details.dbd-evidence .dbd-evidence-body {{ padding: 0 16px 14px; }}
    .dbd-playbook {{ margin-bottom: 18px; border: 1px solid rgba(48,54,61,0.8);
      border-radius: 12px; background: rgba(22,27,34,0.55); padding: 14px 16px; }}
    .dbd-pb-title {{ font-size: 13px; font-weight: 700; color: #ffcc00; margin-bottom: 10px;
      border-left: 3px solid #ffcc00; padding-left: 8px; }}
    .dbd-pb-sub {{ font-size: 11px; font-weight: 400; color: #8b949e; margin-left: 8px; }}
    .dbd-pb-cmd {{ display: flex; align-items: flex-start; gap: 9px; padding: 8px 11px;
      margin-bottom: 6px; border-radius: 8px; border-left: 3px solid #6e7681;
      background: rgba(13,17,23,0.5); font-size: 12.5px; line-height: 1.5; color: #c9d1d9; }}
    .dbd-pb-cmd:last-child {{ margin-bottom: 0; }}
    .dbd-pb-icon {{ flex-shrink: 0; font-size: 14px; }}
    .dbd-pb-hot {{ border-left-color: #ff4444; background: rgba(255,68,68,0.09); }}
    .dbd-pb-cold {{ border-left-color: #58a6ff; background: rgba(88,166,255,0.09); }}
    .dbd-pb-warn {{ border-left-color: #ff8800; background: rgba(255,136,0,0.09); }}
    .dbd-pb-ok {{ border-left-color: #3fb950; background: rgba(63,185,80,0.09); }}
    .dbd-pb-neutral {{ border-left-color: #6e7681; }}
    @media (max-width: 640px) {{ .dbd-headline {{ grid-template-columns: 1fr; }} }}
    .dbd-hero {{
      background: linear-gradient(135deg, color-mix(in srgb, var(--dbd-sc) 20%, transparent),
                                       color-mix(in srgb, var(--dbd-sc) 8%, transparent));
      border: 2px solid var(--dbd-sc); border-radius: 14px;
      padding: 20px 24px; margin-bottom: 16px;
      display: flex; align-items: center; justify-content: space-between;
      gap: 20px; flex-wrap: wrap;
      box-shadow: 0 0 30px color-mix(in srgb, var(--dbd-sc) 22%, transparent);
    }}
    .dbd-hero .dbd-left h2 {{ font-size: 22px; font-weight: 800; color: var(--dbd-sc); margin-bottom: 4px; }}
    .dbd-hero .dbd-left .dbd-sub {{ color: #c9d1d9; font-size: 13px; }}
    .dbd-hero .dbd-right {{
      text-align: right;
      background: color-mix(in srgb, var(--dbd-sc) 22%, transparent);
      padding: 12px 20px; border-radius: 10px; border: 1px solid var(--dbd-sc);
    }}
    .dbd-hero .dbd-pos-label {{ color: color-mix(in srgb, var(--dbd-sc) 70%, #ffffff);
                                 font-size: 12px; margin-bottom: 4px; }}
    .dbd-hero .dbd-pos-value {{ font-size: 22px; font-weight: 800; color: #fff; }}
    .dbd-hero .dbd-win {{ color: #ffcc00; font-size: 12px; margin-top: 4px; }}
    .dbd-desc {{
      grid-column: 1 / -1; color: #c9d1d9; font-size: 13px;
      padding-top: 10px; margin-top: 10px;
      border-top: 1px solid color-mix(in srgb, var(--dbd-sc) 35%, transparent);
      width: 100%;
    }}
    .dbd-grid {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 12px; margin-bottom: 18px;
    }}
    .dbd-card {{
      background: rgba(22, 27, 34, 0.7);
      border: 1px solid rgba(48, 54, 61, 0.8);
      border-radius: 10px; padding: 14px 16px;
    }}
    .dbd-card h4 {{
      font-size: 12px; color: #d29922; margin-bottom: 6px; font-weight: 600;
      border-left: 3px solid #d29922; padding-left: 8px;
    }}
    .dbd-kpi {{ font-size: 22px; font-weight: 800; color: #fff; }}
    .dbd-kpi.dbd-green {{ color: #3fb950; }}
    .dbd-kpi.dbd-red {{ color: #ff4444; }}
    .dbd-kpi.dbd-yellow {{ color: #ffcc00; }}
    .dbd-kpi.dbd-blue {{ color: #58a6ff; }}
    .dbd-hint {{ color: #8b949e; font-size: 11px; margin-top: 3px; }}
    .dbd-quality-card {{ margin: 10px 0 18px; padding: 11px 14px; border: 1px solid rgba(48,54,61,0.8);
      border-radius: 9px; background: rgba(22,27,34,0.6); }}
    .dbd-quality-title {{ color: #c9d1d9; font-size: 12px; margin-bottom: 7px; }}
    .dbd-quality-title b {{ color: #3fb950; }}
    .dbd-quality-items {{ display: flex; flex-wrap: wrap; gap: 6px 14px; color: #8b949e; font-size: 11px; }}
    .dbd-quality-layers {{ display: flex; flex-wrap: wrap; gap: 5px 14px; margin-top: 6px; color: #c9d1d9; font-size: 11px; }}
    .dbd-quality-issues {{ margin-top: 6px; color: #d29922; font-size: 11px; line-height: 1.45; }}
    .dbd-section-title {{
      font-size: 15px; font-weight: 700; color: #ffcc00;
      margin: 20px 0 10px; padding-left: 9px;
      border-left: 3px solid #ffcc00;
    }}
    .dbd-st-sub {{ font-size: 11px; font-weight: 400; color: #8b949e; margin-left: 8px; }}
    .dbd-factor {{
      width: 100%; background: rgba(22, 27, 34, 0.7);
      border-radius: 10px; border-collapse: separate; border-spacing: 0;
      overflow: hidden; margin-bottom: 8px;
    }}
    .dbd-factor th, .dbd-factor td {{
      padding: 10px 12px; border-bottom: 1px solid rgba(48, 54, 61, 0.6);
      text-align: left; font-size: 13px;
    }}
    .dbd-factor th {{
      background: rgba(30, 35, 42, 0.9); color: #8b949e;
      font-size: 11px; font-weight: 600; text-transform: uppercase;
    }}
    .dbd-check-ok {{ color: #3fb950; font-weight: 700; }}
    .dbd-check-fail {{ color: #ff4444; font-weight: 700; }}
    .dbd-check-warn {{ color: #ffcc00; font-weight: 700; }}
    .dbd-tree {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 12px;
    }}
    .dbd-scen {{
      background: rgba(22, 27, 34, 0.75);
      border: 2px solid; border-radius: 10px; padding: 14px 16px;
    }}
    .dbd-scen-head {{
      display: flex; align-items: center; justify-content: space-between;
      margin-bottom: 8px;
    }}
    .dbd-scen-name {{ font-size: 14px; font-weight: 700; }}
    .dbd-scen-prob {{ font-size: 11px; color: #8b949e; }}
    .dbd-scen-stat {{ margin: -3px 0 6px; color: #8b949e; font-size: 10px; line-height: 1.4; }}
    .dbd-scen ul {{ margin-left: 16px; font-size: 12px; color: #e6edf3; line-height: 1.6; }}
    .dbd-scen-pos {{
      margin-top: 8px; padding: 5px 9px; border-radius: 5px;
      font-size: 12px; font-weight: 700; display: inline-block;
    }}
    .dbd-attack {{ border-color: #ff4444; }}
    .dbd-attack .dbd-scen-name, .dbd-attack .dbd-scen-pos {{ color: #ff4444; }}
    .dbd-attack .dbd-scen-pos {{ background: rgba(255, 68, 68, 0.15); }}
    .dbd-moderate {{ border-color: #d29922; }}
    .dbd-moderate .dbd-scen-name, .dbd-moderate .dbd-scen-pos {{ color: #d29922; }}
    .dbd-moderate .dbd-scen-pos {{ background: rgba(210, 153, 34, 0.15); }}
    .dbd-defense {{ border-color: #58a6ff; }}
    .dbd-defense .dbd-scen-name, .dbd-defense .dbd-scen-pos {{ color: #58a6ff; }}
    .dbd-defense .dbd-scen-pos {{ background: rgba(88, 166, 255, 0.15); }}
    .dbd-critical {{ border-color: #ff8800; }}
    .dbd-critical .dbd-scen-name, .dbd-critical .dbd-scen-pos {{ color: #ff8800; }}
    .dbd-critical .dbd-scen-pos {{ background: rgba(255, 136, 0, 0.15); }}
    .dbd-full-link {{
      display: inline-block; margin-top: 10px; padding: 6px 14px;
      background: #161b22; color: #58a6ff; text-decoration: none;
      border: 1px solid #30363d; border-radius: 6px; font-size: 12px;
    }}
    .dbd-full-link:hover {{ border-color: #58a6ff; }}
    .dbd-fp-table {{
      width: 100%; background: rgba(22, 27, 34, 0.7);
      border-radius: 10px; border-collapse: separate; border-spacing: 0;
      overflow: hidden; margin-bottom: 8px;
    }}
    .dbd-fp-table th, .dbd-fp-table td {{
      padding: 10px 12px; border-bottom: 1px solid rgba(48, 54, 61, 0.6);
      text-align: left; font-size: 12.5px; vertical-align: top;
    }}
    .dbd-fp-table th {{
      background: rgba(30, 35, 42, 0.9); color: #8b949e;
      font-size: 11px; font-weight: 600; text-transform: uppercase;
    }}
    .dbd-fp-table tbody tr:last-child td {{ border-bottom: none; }}
    .dbd-fp-name {{ width: 100px; color: #ffcc00; font-weight: 700; }}
    .dbd-fp-plate {{ width: 130px; color: #79b8ff; font-size: 11.5px; }}
    .dbd-fp-pool {{ width: 130px; color: #d29922; font-size: 11.5px; }}
    .dbd-fp-entry {{ color: #c9d1d9; line-height: 1.55; }}
    .dbd-fp-stop {{ width: 180px; color: #ff8888; font-size: 11.5px; }}
    .dbd-fp-cat {{ width: 200px; font-size: 11.5px; }}
    .dbd-fp-cat-tag {{ display: inline-block; color: #3fb950; font-weight: 700;
      background: rgba(63,185,80,0.12); padding: 1px 6px; border-radius: 4px; }}
    .dbd-fp-cat-text {{ color: #c9d1d9; margin-top: 3px; line-height: 1.5; }}
    @media (max-width: 640px) {{
      .dbd-hero {{ flex-direction: column; align-items: stretch; }}
      .dbd-hero .dbd-right {{ text-align: left; }}
    }}
    </style>'''

    html = f'''{css}
<section class="dbd-wrap">
  <div class="dbd-hero">
    <div class="dbd-left">
      <h2>今日决策看板 · {_esc(scene)} · {_esc(action)}</h2>
      <div class="dbd-sub">{_esc(level)} · 报告日期 {_esc(date_str)}</div>
    </div>
    <div class="dbd-right">
      <div class="dbd-pos-label">{"建议仓位" if policy['decision_ready'] else "报告可用范围"}</div>
      <div class="dbd-pos-value">{_esc(position if policy['decision_ready'] else ("仅事实层" if policy['facts_only'] else "观察与条件触发"))}</div>
      <div class="dbd-win">{("历史 T+3 破新高 " + wr_str) if policy['decision_ready'] else "历史验证暂不展示"}</div>
    </div>
    <div class="dbd-desc">{_esc(desc)}</div>
  </div>

  {headline_html}

  {review_closure_html}

  {quality_html}
  {ladder_quality_html}
  {data_credibility_html}
  {lianban_review_html}
  {mainline_review_html}

  {playbook_html}

  {scenario_block}

  <div class="dbd-section-title">{"股票池未发布" if policy['facts_only'] else "观察名单（非推荐）" if policy['observation_only'] else "明日核心股票池"} · {"数据阻断" if policy['facts_only'] else "仅供观察 · 条件触发" if policy['observation_only'] else "具体标的与入场条件"}</div>
  {focus_rows_inline}

  <details class="dbd-evidence">
    <summary>数据佐证 · 盘面因子明细</summary>
    <div class="dbd-evidence-body">
      {kpi_html}
      <div class="dbd-section-title">场景判定 · 三因子交叉</div>
      <table class="dbd-factor">
        <thead><tr><th>因子</th><th>当前读数</th><th>是否达标</th><th>辅助说明</th></tr></thead>
        <tbody>{factor_rows}</tbody>
      </table>
    </div>
  </details>
</section>'''
    return sanitize_html_for_policy(html, mode)
