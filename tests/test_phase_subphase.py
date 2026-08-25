# -*- coding: utf-8 -*-
"""小阶段 (底部至今内部最新一刀) 与 箱体/趋势临界带 的判据测试。

大段是回溯口径 (底一旦锚定就不动), 反弹走久了最新结构会被震荡段整段吞掉;
小阶段就是补这一刀。临界带则是治"振幅正好压在 6.0% 阈值上、标签一天一翻"。
"""
import phase_resonance as pr


def _bar(day, close, high=None, low=None):
    date = f'2026-07-{day:02d}' if day <= 31 else f'2026-08-{day - 31:02d}'
    return {
        'date': date, 'open': close, 'close': close,
        'high': high if high is not None else close,
        'low': low if low is not None else close,
        'volume': 1000,
    }


def _series(tail_closes, tail_high=None, tail_low=None):
    """顶 4200 → 一路跌到底 3760 → 见底脉冲 5 根 → tail_closes 收尾。

    tail_high/tail_low 只改震荡段起点那根的高/低, 用来精确控制振幅。
    """
    closes = [4200, 4180, 4160, 4140, 4120, 4090, 4060, 4030,
              4000, 3960, 3920, 3880, 3840, 3800]        # 下跌段 (顶在第 1 根)
    closes.append(3760)                                   # 底
    closes += [3800, 3850, 3900, 3950, 3990]              # 见底脉冲 (末根 = 阶段高)
    closes += list(tail_closes)
    bars = [_bar(i + 1, c) for i, c in enumerate(closes)]
    seg_start = 19                                        # = vi, 震荡段起点那根
    if tail_high is not None:
        bars[seg_start]['high'] = tail_high
    if tail_low is not None:
        bars[seg_start]['low'] = tail_low
    return bars


STAGE_HIGH_DATE = '2026-07-20'                            # 见底脉冲末根 = 阶段高
RETRACE_TAIL = [3900, 3870, 3850]                         # 回撤 -3.51%, 3 根


def test_sub_phase_names_retrace_leg_after_stage_high():
    det = pr.detect_phases(_series(RETRACE_TAIL))
    sub = det['sub_phase']
    assert sub['name'] == '回撤段'
    assert sub['high_close'] == 3990
    assert sub['start'] == STAGE_HIGH_DATE and sub['end'] == det['latest']['date']
    assert sub['bars'] == 3
    assert sub['retrace'] == round((3850 / 3990 - 1) * 100, 2) <= -pr.SUB_RETRACE_PCT
    assert sub['low_close'] == 3850 and sub['deepest'] == sub['retrace']
    assert set(det['phases']) == {'下跌段', '见底段', '震荡段', '最新日', '底部至今'}


def test_sub_phase_flags_fresh_high_when_last_bar_is_the_high():
    det = pr.detect_phases(_series([4010, 4050, 4090]))
    sub = det['sub_phase']
    assert sub['name'] == '创阶段新高'
    assert sub['bars'] == 0 and sub['retrace'] == 0.0
    assert sub['start'] == sub['end'] == det['latest']['date']
    assert sub['low_date'] is None and sub['deepest'] is None


def test_sub_phase_names_shallow_but_long_stall_as_high_consolidation():
    sub = pr.detect_phases(_series([3985, 3980, 3975, 3972]))['sub_phase']
    assert sub['name'] == '高位盘整'
    assert sub['bars'] >= pr.SUB_MIN_BARS
    assert -pr.SUB_RETRACE_PCT < sub['retrace'] < 0


def test_sub_phase_stays_unnamed_while_still_next_to_the_high():
    sub = pr.detect_phases(_series([4020, 4060, 4055]))['sub_phase']
    assert sub['name'] == '阶段高附近'
    assert sub['bars'] < pr.SUB_MIN_BARS
    assert sub['retrace'] > -pr.SUB_RETRACE_PCT


def test_every_sub_phase_name_carries_an_action_line():
    for tail in (RETRACE_TAIL, [4010, 4050, 4090],
                 [3985, 3980, 3975, 3972], [4020, 4060, 4055]):
        sub = pr.detect_phases(_series(tail))['sub_phase']
        action = sub['action']
        assert action and '——' in action
        assert 'None' not in action and 'nan' not in action
        assert f"{sub['high_close']:.0f}" in action
        assert f"{sub['ref_low']:.0f}" in action


def test_sub_phase_action_survives_missing_reference_low():
    txt = pr.sub_phase_action({'name': '回撤段', 'high_close': None, 'ref_low': None,
                               'bars': 2, 'retrace': None, 'low_close': None})
    assert 'None' not in txt and 'nan' not in txt


def _shape_label(det):
    return str(det['shape']).partition(' (')[0]


def test_amp_below_band_keeps_the_original_box_labels():
    det = pr.detect_phases(_series(RETRACE_TAIL, tail_high=3850 * 1.04))
    assert round(det['amp'], 1) == 4.0
    assert _shape_label(det).startswith('箱体')


def test_amp_above_band_keeps_the_original_trend_labels():
    det = pr.detect_phases(_series(RETRACE_TAIL, tail_high=3850 * 1.08))
    assert round(det['amp'], 1) == 8.0
    assert _shape_label(det) in ('反弹趋势段', '二次探底')


def test_amp_inside_band_refuses_to_pick_a_side():
    det = pr.detect_phases(_series(RETRACE_TAIL, tail_high=3850 * 1.06))
    assert round(det['amp'], 1) == 6.0
    assert _shape_label(det) == '箱体/趋势临界'
    assert '暂不定性' in det['shape']
    assert det['shape'].count(' (') == 1 and det['shape'].endswith(')')


def test_band_edges_fall_back_to_the_hard_labels():
    # 带宽只有 ±0.5%, 边界外一丝 (0.01%) 就必须回到硬标签 —— 临界带不许无声扩张。
    # (不测浮点意义上的"恰好等于边界": 4100.25/3850 这类除法本身就带 1e-15 误差)
    lo_h = 3850 * (1 + (pr.BOX_MAX_AMP - pr.BOX_AMP_BUFFER - 0.01) / 100)
    hi_h = 3850 * (1 + (pr.BOX_MAX_AMP + pr.BOX_AMP_BUFFER + 0.01) / 100)
    lo = pr.detect_phases(_series(RETRACE_TAIL, tail_high=lo_h))
    hi = pr.detect_phases(_series(RETRACE_TAIL, tail_high=hi_h))
    assert _shape_label(lo).startswith('箱体')
    assert _shape_label(hi) in ('反弹趋势段', '二次探底')


def test_sub_phase_html_shows_the_reading_and_the_action():
    sub = pr.detect_phases(_series(RETRACE_TAIL))['sub_phase']
    html = pr._sub_phase_html(sub)
    assert '小阶段' in html and '回撤段' in html and '怎么操作' in html
    assert 'overflow-wrap:anywhere' in html
    assert 'None' not in html and 'nan' not in html


def test_sub_phase_html_is_empty_without_a_sub_phase():
    assert pr._sub_phase_html(None) == ''
    assert pr._sub_phase_html({}) == ''


def test_phase_timeline_marks_the_sub_phase_as_overlapping_not_parallel():
    det = pr.detect_phases(_series(RETRACE_TAIL))
    sub = det['sub_phase']
    res = {
        'det': det,
        'phase_names': list(det['phases']),
        'timeline_segments': {**det['phases'], sub['name']: (sub['start'], sub['end'])},
        'timeline_names': list(det['phases']) + [sub['name']],
        'sub_phase': sub,
        'index_ret': {p: 1.0 for p in det['phases']},
        'breadth': {},
    }
    html = pr._phase_timeline(res)
    assert '🔍' in html and '时间上重叠' in html and '别加总' in html
    assert 'None' not in html


def test_phase_timeline_still_renders_payloads_without_a_sub_phase():
    det = pr.detect_phases(_series(RETRACE_TAIL))
    res = {'det': det, 'phase_names': ['最新日'], 'breadth': {},
           'index_ret': {'最新日': 0.5}}
    html = pr._phase_timeline(res)
    assert 'class="phase-timeline-wrap"' in html and '🔍' not in html


def test_extra_segments_never_shadow_the_major_phases():
    det = pr.detect_phases(_series(RETRACE_TAIL))
    sub = det['sub_phase']
    recs = [{'date': b['date'], 'close': b['close'], 'amount': 1e8}
            for b in det['index_series']]
    plain = pr.sector_table({'某板块': recs}, det)
    withsub = pr.sector_table({'某板块': recs}, det,
                              {sub['name']: (sub['start'], sub['end']),
                               '震荡段': ('1999-01-01', '1999-01-02')})
    assert sub['name'] in withsub.columns and sub['name'] not in plain.columns
    assert withsub['震荡段'].tolist() == plain['震荡段'].tolist()


def test_turning_summary_band_shows_the_sub_phase_and_its_action():
    import pandas as pd
    det = pr.detect_phases(_series(RETRACE_TAIL))
    summary = pr.build_turning_summary(det, pd.DataFrame(), {})
    sub = summary['current_phase']['sub']
    assert sub['name'] == '回撤段' and sub['action']
    html = pr._turning_summary_html(summary)
    assert '小阶段' in html and '回撤段' in html and '怎么操作' in html
    assert 'None' not in html


def test_turning_summary_band_stays_intact_without_a_sub_phase():
    import pandas as pd
    det = pr.detect_phases(_series(RETRACE_TAIL))
    det.pop('sub_phase')
    summary = pr.build_turning_summary(det, pd.DataFrame(), {})
    assert summary['current_phase']['sub'] == {}
    html = pr._turning_summary_html(summary)
    assert '小阶段' not in html and '当前阶段' in html
