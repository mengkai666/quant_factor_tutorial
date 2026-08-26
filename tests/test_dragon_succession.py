# -*- coding: utf-8 -*-
"""龙头接替·监管周期模块 (src/dragon_succession.py) 单测。

全部用合成数据 + monkeypatch, 零联网:
  · 分类/周期/预筛/脊柱/停牌/孵化/崩溃 都是可注入的纯函数;
  · 公告正/负缓存重定向到 tmp_path, 不碰真实 data/;
  · 唯一联网点 fetch_announcements 一律 monkeypatch (含"预筛不触网"的反证)。

母版: test_trend_regime.py (渲染) / test_price_gap_memo.py (文件缓存) / test_publish_site.py (集成)。
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

import dragon_succession as D  # noqa: E402


# ─────────────────────────── 合成数据小工具 ───────────────────────────
def _price_df(rows):
    """rows: [(c6, d, close)] → 归一化价格表 (与 _load_price_df 输出同构)。"""
    return pd.DataFrame(rows, columns=['c6', 'd', 'close'])


def _ctx(by_day, real_dates, as_of, name_by_code=None, price_df=None,
         zt_count=None, dt_count=None, dt_pool=None):
    return {
        'as_of': as_of, 'policy': 'observation',
        'by_day': by_day, 'real_dates': real_dates, 'stale': set(),
        'name_by_code': name_by_code or {},
        'zt_count': zt_count or {d: len(m) for d, m in by_day.items()},
        'dt_count': dt_count or {}, 'dt_pool': dt_pool or {},
        'price_df': price_df if price_df is not None else _price_df([]),
    }


def _full_result():
    """一份完整、真实感的 result (爱丽→百花→神奇 + 停牌孵化 + 8/19 崩溃), 喂渲染。"""
    backbone = [
        {'code6': '603221', 'name': '爱丽家居', 'first_rise_date': '20260715',
         'peak_height': 10, 'cum_gain_pct': 214.0, 'max_severity': 4,
         'top_marker_date': '20260805',
         'announcements': [{'ann_date': '20260805', 'title': '严重异常波动公告',
                            'type': '', 'severity': 3, 'kind': '严重异动/问询', 'url': 'http://x'}],
         'halts': [{'start': '20260812', 'end': '20260818', 'resumed': True}],
         'is_suspended_now': False, 'role': 'anchor'},
        {'code6': '600721', 'name': '百花医药', 'first_rise_date': '20260805',
         'peak_height': 6, 'cum_gain_pct': 95.0, 'max_severity': 2,
         'top_marker_date': None, 'announcements': [], 'halts': [],
         'is_suspended_now': False, 'role': 'successor'},
        {'code6': '600613', 'name': '神奇制药', 'first_rise_date': '20260813',
         'peak_height': 5, 'cum_gain_pct': 60.0, 'max_severity': 2,
         'top_marker_date': None, 'announcements': [], 'halts': [],
         'is_suspended_now': False, 'role': 'successor'},
    ]
    return {
        'as_of': '20260819', 'policy': 'observation', 'has_cycle': True,
        'cycle': {'start': '20260715', 'end': '20260819', 'peak_date': '20260806',
                  'peak_height': 10, 'is_current': True, 'n_days': 20},
        'backbone': backbone, 'board': list(backbone),
        'succession': [{'from': '603221', 'to': '600613', 'from_name': '爱丽家居',
                        'to_name': '神奇制药', 'incubation': True,
                        'window': ['20260812', '20260818'], 'to_first_rise': '20260813',
                        'gap_days': 1, 'resumed': True}],
        'ladder': [
            {'date': '20260805', 'code6': '603221', 'name': '爱丽家居', 'severity': 3,
             'kind': '严重异动/问询', 'title': '严重异常波动公告', 'url': 'http://x',
             'is_top_marker': True},
            {'date': '20260812', 'code6': '603221', 'name': '爱丽家居', 'severity': 4,
             'kind': '停牌核查', 'title': '停牌核查公告', 'url': '', 'is_top_marker': True},
        ],
        'collapse': {'date': '20260819', 'zt_prev': 79, 'zt': 36, 'dt_prev': 5, 'dt': 118,
                     'zt_drop_pct': 54.4, 'dt_surge_x': 23.6, 'is_collapse': True,
                     'high_leaders_limit_down': [{'code6': '603221', 'name': '爱丽家居'}],
                     'note': '情绪崩溃'},
        'headline': '以 爱丽家居(10板) 为锚的监管周期，3 只核心高标接替，2026-08-19 情绪崩溃。',
        'teaser': {'leader_name': '爱丽家居', 'leader_height': 10,
                   'backbone_names': ['爱丽家居', '百花医药', '神奇制药'],
                   'stage': '崩溃/退潮', 'n_backbone': 3, 'n_board': 3, 'top_markers': 2},
        'degraded': False, 'degrade_reason': None,
    }


@pytest.fixture
def ds(tmp_path, monkeypatch):
    """把公告正/负缓存重定向到 tmp_path, 并把 _today 钉死, 保证确定性 + 不污染 data/。"""
    monkeypatch.setattr(D, 'CNINFO_ANN_CACHE', str(tmp_path / 'ann.csv'))
    monkeypatch.setattr(D, 'CNINFO_ANN_NEG_CACHE', str(tmp_path / 'ann_neg.csv'))
    monkeypatch.setattr(D, '_today', lambda: '20260825')
    return D


# ─────────────────────────── ① 公告分类 ───────────────────────────
@pytest.mark.parametrize('title,sev', [
    ('爱丽家居关于股票交易复牌的公告', 5),
    ('股票交易严重异常波动暨停牌核查公告', 4),   # 停牌优先于严重异动
    ('股票交易严重异常波动公告', 3),
    ('关于收到上海证券交易所监管工作函的公告', 3),
    ('股票交易异常波动公告', 2),
    ('股票交易风险提示公告', 1),
    ('2025年年度报告', 0),
])
def test_classify_announcement_priority(title, sev):
    assert D.classify_announcement(title) == sev


# ─────────────────────────── ② 周期识别 ───────────────────────────
def test_identify_cycle_detects_window():
    by_day = {
        '20260715': {'A': 4}, '20260716': {'A': 6}, '20260717': {'A': 10},
        '20260718': {'A': 5}, '20260721': {'A': 2}, '20260722': {'A': 1},
    }
    dates = sorted(by_day)
    cyc = D.identify_cycle(by_day, dates, dates[-1])
    assert cyc is not None
    assert cyc['start'] == '20260715' and cyc['end'] == '20260718'
    assert cyc['peak_date'] == '20260717' and cyc['peak_height'] == 10
    assert cyc['is_current'] is False and cyc['n_days'] == 4


def test_identify_cycle_current_when_not_closed():
    by_day = {'20260715': {'A': 4}, '20260716': {'A': 6}, '20260717': {'A': 8}}
    dates = sorted(by_day)
    cyc = D.identify_cycle(by_day, dates, dates[-1])
    assert cyc is not None and cyc['is_current'] is True


def test_identify_cycle_none_when_no_high_board():
    by_day = {'20260715': {'A': 2}, '20260716': {'A': 3}, '20260717': {'A': 1}}
    dates = sorted(by_day)
    assert D.identify_cycle(by_day, dates, dates[-1]) is None


# ─────────────────────────── ③ 预筛 (纯本地, 不触网) ───────────────────────────
def test_candidate_prefilter_cache_only(monkeypatch):
    # 预筛若不慎触网, fetch 会抛错; 断言无异常 = 证明预筛零联网。
    import catalyst_attribution
    def _boom(*a, **k):
        raise AssertionError('预筛不应联网抓公告')
    monkeypatch.setattr(catalyst_attribution, 'fetch_announcements', _boom)

    by_day = {'20260716': {'A': 5, 'D': 3}, '20260718': {'A': 6, 'B': 4}}
    dates = sorted(by_day)
    # D 只 3 板 (不进连板通路), 但价格 10 日翻倍 → 走累计涨幅通路
    price = _price_df([('D', '20260716', 100.0), ('D', '20260717', 200.0),
                       ('D', '20260718', 250.0)])
    ctx = _ctx(by_day, dates, dates[-1], price_df=price)
    cycle = {'start': '20260716', 'end': '20260718', 'peak_date': '20260718',
             'peak_height': 6, 'is_current': False, 'n_days': 2}
    cand = D._candidate_prefilter(ctx, cycle)
    assert 'A' in cand and 'B' in cand   # 连板通路
    assert 'D' in cand                    # 累计涨幅通路


# ─────────────────────────── ④ 全量盖章板 + 脊柱 ───────────────────────────
def test_build_regulated_board_orders_and_filters(ds, monkeypatch):
    by_day = {
        '20260715': {'A': 5}, '20260718': {'A': 5, 'B': 10},
        '20260720': {'A': 4, 'B': 6, 'C': 4},
    }
    dates = sorted(by_day)
    ctx = _ctx(by_day, dates, '20260720',
               name_by_code={'A': '爱丽', 'B': '一鸣', 'C': '过客'})
    cycle = {'start': '20260715', 'end': '20260720', 'peak_date': '20260718',
             'peak_height': 10, 'is_current': False, 'n_days': 3}
    anns_map = {
        'A': [{'ann_date': '20260716', 'title': '异常波动', 'type': '', 'severity': 2,
               'kind': '异动', 'url': ''}],
        'B': [{'ann_date': '20260719', 'title': '停牌核查', 'type': '', 'severity': 4,
               'kind': '停牌核查', 'url': ''}],
        'C': [],  # 无 severity≥2 → 应被剔除
    }
    monkeypatch.setattr(ds, 'get_announcements_cached',
                        lambda c6, as_of, _neg=None, _stats=None: anns_map.get(c6, []))
    board = ds.build_regulated_board(ctx, cycle)
    codes = [b['code6'] for b in board]
    assert codes == ['A', 'B']            # 按首涨日升序, C 被剔
    assert board[0]['max_severity'] == 2 and board[1]['max_severity'] == 4


def test_select_spine_anchor_is_highest_score():
    # 锚定龙头 = 脊柱分最高者, 而非最早起涨者 (修正"百花当锚"偏差)
    board = [
        {'code6': 'A', 'name': '早起涨', 'first_rise_date': '20260715', 'peak_height': 7,
         'max_severity': 3, 'halts': [], 'is_suspended_now': False, 'role': 'successor'},
        {'code6': 'B', 'name': '真龙头', 'first_rise_date': '20260718', 'peak_height': 10,
         'max_severity': 4, 'halts': [{'start': '20260812', 'end': '20260818', 'resumed': True}],
         'is_suspended_now': False, 'role': 'successor'},
    ]
    spine = D.select_spine(board)
    assert [b['code6'] for b in spine] == ['A', 'B']           # 展示按首涨日
    anchors = [b for b in spine if b['role'] == 'anchor']
    assert len(anchors) == 1 and anchors[0]['code6'] == 'B'    # 锚是高分的 B, 非最早的 A


# ─────────────────────────── ⑤ 停牌区间 ───────────────────────────
def test_infer_halt_from_sandwiched_gap():
    mdays = ['20260810', '20260811', '20260812', '20260813', '20260814', '20260815', '20260818']
    price = _price_df([('A', '20260810', 10.0), ('A', '20260811', 11.0), ('A', '20260818', 12.0)])
    halts = D.infer_halt_intervals(price, 'A', mdays, anns=[])
    assert halts == [{'start': '20260812', 'end': '20260818', 'resumed': True}]


def test_infer_halt_trailing_open_when_sev4():
    mdays = ['20260810', '20260811', '20260812', '20260813', '20260814']
    price = _price_df([('A', '20260810', 10.0), ('A', '20260811', 11.0)])
    anns = [{'severity': 4, 'ann_date': '20260811'}]
    halts = D.infer_halt_intervals(price, 'A', mdays, anns)
    assert halts and halts[-1]['end'] is None and halts[-1]['resumed'] is False


def test_infer_halt_isolated_gap_dropped_without_sev4():
    # 孤立单日缺口且无停牌公告 → 当数据洞丢弃
    mdays = ['20260810', '20260811', '20260812', '20260813']
    price = _price_df([('A', '20260810', 10.0), ('A', '20260812', 11.0), ('A', '20260813', 12.0)])
    assert D.infer_halt_intervals(price, 'A', mdays, anns=[]) == []


def test_infer_halt_leading_gap_not_counted():
    # 前导缺失 (未上市/数据洞) 不算停牌
    mdays = ['20260810', '20260811', '20260812', '20260813']
    price = _price_df([('A', '20260812', 10.0), ('A', '20260813', 11.0)])
    assert D.infer_halt_intervals(price, 'A', mdays, anns=[]) == []


# ─────────────────────────── ⑥ 孵化重叠 ───────────────────────────
def _spine_pair(new_first_rise):
    return [
        {'code6': 'A', 'name': '爱丽', 'first_rise_date': '20260715', 'peak_height': 10,
         'max_severity': 4, 'halts': [{'start': '20260812', 'end': '20260818', 'resumed': True}]},
        {'code6': 'B', 'name': '神奇', 'first_rise_date': new_first_rise, 'peak_height': 5,
         'max_severity': 2, 'halts': []},
    ]


def test_succession_edge_incubation_inside_window():
    spine = _spine_pair('20260813')   # 落在爱丽停牌窗 [0812,0818] 内
    edges = D.build_succession_edges(spine, board=spine)
    assert len(edges) == 1
    e = edges[0]
    assert e['from'] == 'A' and e['to'] == 'B' and e['incubation'] is True


def test_succession_edge_none_outside_window():
    spine = _spine_pair('20260820')   # 复牌之后起涨, 不算孵化
    assert D.build_succession_edges(spine, board=spine) == []


# ─────────────────────────── ⑦ 情绪崩溃 (8/19 标定) ───────────────────────────
def test_collapse_metric_flags_819():
    by_day = {'20260818': {'x': 1}, '20260819': {'y': 1}}
    ctx = _ctx(by_day, ['20260818', '20260819'], '20260819',
               zt_count={'20260818': 79, '20260819': 36},
               dt_count={'20260818': 5, '20260819': 118},
               dt_pool={'20260819': {'603221': '爱丽家居', '600703': '一鸣'}})
    cycle = {'start': '20260715', 'peak_date': '20260818'}
    backbone = [{'code6': '603221', 'name': '爱丽家居', 'max_severity': 4},
                {'code6': '600703', 'name': '一鸣', 'max_severity': 3}]
    col = D.build_collapse_metric(ctx, cycle, backbone)
    assert col['is_collapse'] is True and col['date'] == '20260819'
    assert abs(col['zt_drop_pct'] - 54.4) < 0.5
    assert abs(col['dt_surge_x'] - 23.6) < 0.1
    assert len(col['high_leaders_limit_down']) == 2


def test_collapse_metric_none_when_calm():
    by_day = {'20260818': {'x': 1}, '20260819': {'y': 1}}
    ctx = _ctx(by_day, ['20260818', '20260819'], '20260819',
               zt_count={'20260818': 80, '20260819': 78},
               dt_count={'20260818': 3, '20260819': 4})
    cycle = {'start': '20260715', 'peak_date': '20260818'}
    col = D.build_collapse_metric(ctx, cycle, [])
    assert col['is_collapse'] is False and col['date'] is None


# ─────────────────────────── ⑧ 降级路径 (返回 None/空, 不抛) ───────────────────────────
def test_build_returns_has_cycle_false(monkeypatch):
    # ctx 加载成功但无 ≥4 板 → 周期识别 None → has_cycle=False, 且渲染两口都返 ''
    ctx = _ctx({'20260101': {'A': 2}}, ['20260101'], '20260101')
    monkeypatch.setattr(D, '_build_context', lambda *a, **k: ctx)
    res = D.build_dragon_succession(report_date='20260101')
    assert res['has_cycle'] is False and res['degraded'] is True
    assert D.generate_dragon_html(res) == ''
    assert D.render_dragon_teaser_html(res) == ''


@pytest.mark.parametrize('bad', [None, {}, {'has_cycle': False}])
def test_render_empty_on_bad_input(bad):
    assert D.generate_dragon_html(bad) == ''
    assert D.render_dragon_teaser_html(bad) == ''


# ─────────────────────────── ⑨ 渲染房规 ───────────────────────────
def test_generate_dragon_html_sections():
    html = D.generate_dragon_html(_full_result())
    assert html
    for token in ('龙头接替', '停牌', '接替', '情绪崩溃'):
        assert token in html
    assert '../index.html' in html
    assert 'None' not in html and 'nan' not in html.lower()


def test_teaser_uses_absolute_site_url(monkeypatch):
    monkeypatch.setattr('paths.SITE_URL', 'https://example.test/base/')
    html = D.render_dragon_teaser_html(_full_result())
    assert 'https://example.test/base/dragon/latest.html' in html


def test_render_escapes_dynamic_text():
    res = _full_result()
    res['backbone'][0]['name'] = '<script>x</script>'
    res['headline'] = '锚定龙头正常标题'
    html = D.generate_dragon_html(res)
    assert '<script>' not in html
    assert '&lt;script&gt;' in html


# ─────────────────────────── ⑩ 文件型公告缓存 ───────────────────────────
def test_ann_cache_upsert_and_dedup(ds, tmp_path):
    row = {'code': '000001', 'query_date': '20260806', 'ann_date': '20260805',
           'title': '异常波动', 'type': '', 'severity': 2, 'url': 'u1',
           'fetched_at': '2026-08-06 10:00:00'}
    assert ds._save_ann_cache([row]) is True
    ds._save_ann_cache([dict(row)])                 # 同 (code,query_date,url) 再存
    assert len(ds._load_ann_cache()) == 1           # 去重, 不翻倍
    ds._save_ann_cache([dict(row, url='u2')])       # 新 url → 增行
    assert len(ds._load_ann_cache()) == 2
    assert not any(f.endswith('.tmp') for f in os.listdir(tmp_path))   # 无残留


def test_get_announcements_cache_first_no_refetch(ds, monkeypatch):
    import catalyst_attribution
    calls = {'n': 0}
    def _counter(code, page_size=20):
        calls['n'] += 1
        return [{'title': '异常波动', 'type': '', 'date': '2026-08-05', 'url': 'z'}]
    monkeypatch.setattr(catalyst_attribution, 'fetch_announcements', _counter)
    # 预置历史日正缓存 → 历史周期公告不可变, 应永久新鲜
    ds._save_ann_cache([{'code': '000002', 'query_date': '20260806', 'ann_date': '20260805',
                         'title': '异常波动', 'type': '', 'severity': 2, 'url': 'z',
                         'fetched_at': '2026-08-06 10:00:00'}])
    out = ds.get_announcements_cached('000002', '20260806')
    assert calls['n'] == 0                          # 命中新鲜缓存, 零联网
    assert out and out[0]['severity'] == 2


def test_ann_negative_cache_skips_failed_code(ds, monkeypatch):
    import catalyst_attribution
    calls = {'n': 0}
    def _fail(code, page_size=20):
        calls['n'] += 1
        return []                                   # 抓失败/空
    monkeypatch.setattr(catalyst_attribution, 'fetch_announcements', _fail)
    # 历史日: 一轮失败即记负缓存, 下一轮直接跳过 (NEG_FAIL_PAST=1)
    assert ds.get_announcements_cached('000003', '20260806') == []
    assert ds.get_announcements_cached('000003', '20260806') == []
    assert calls['n'] == 1                          # 第二轮被负缓存拦下, 未再抓
    # 成功即清除负缓存 (force 绕过跳过闸)
    monkeypatch.setattr(catalyst_attribution, 'fetch_announcements',
                        lambda code, page_size=20: [{'title': '异常波动', 'type': '',
                                                     'date': '2026-08-05', 'url': 'z'}])
    ds.get_announcements_cached('000003', '20260806', force=True)
    assert ('000003', '20260806') not in ds._read_neg()


def test_neg_systemic_failure_guardrail(ds):
    # 整轮大面积失败 (>max(20, N×0.5)) 判为接口/代理故障, 不记账
    failed = [f'{600000 + i}' for i in range(200)]
    ds._neg_record(failed, [], '20260806', attempted=len(failed))
    assert ds._read_neg() == {}


# ─────────────────────────── ⑪ 集成: publish 归档子页 + 入口卡 ───────────────────────────
def _make_report(tmp_path, date='2026-08-20'):
    # publish() 会做发布前完整性门禁, 夹具必须带 report-integrity 元数据, 否则被拦。
    from report_integrity import build_report_integrity, render_report_integrity_metadata
    payload = build_report_integrity(
        report_date=date, market_date=date,
        phase_result={
            'quadrants': {'独立主线': pd.DataFrame([{'板块': '教育'}])},
            'representatives': {'groups': {'独立主线': [{'code': 'sz003032', 'name': '传智教育'}]}},
        },
        quality={
            'status': 'ok', 'critical_blocked': [],
            'modules': {'price_raw': {'status': 'ok', 'coverage_pct': 99.0, 'source': 'price_cache'}},
        },
    )
    p = tmp_path / '主线强度追踪.html'
    p.write_text(f'<html><head><meta name="report-date" content="{date}">'
                 + render_report_integrity_metadata(payload)
                 + '</head><body>x</body></html>', encoding='utf-8')
    return str(p)


def test_publish_archives_dragon_and_entry(tmp_path):
    import publish_site
    site = str(tmp_path / 'site')
    publish_site.publish(_make_report(tmp_path), site, dragon_html='<html>DRAGON</html>')
    assert os.path.isfile(os.path.join(site, 'dragon', '2026-08-20.html'))
    assert os.path.isfile(os.path.join(site, 'dragon', 'latest.html'))
    idx = open(os.path.join(site, 'index.html'), encoding='utf-8').read()
    assert 'href="dragon/latest.html"' in idx


def test_publish_without_dragon_html_unchanged(tmp_path):
    import publish_site
    site = str(tmp_path / 'site')
    publish_site.publish(_make_report(tmp_path), site)
    assert not os.path.isdir(os.path.join(site, 'dragon'))
    idx = open(os.path.join(site, 'index.html'), encoding='utf-8').read()
    assert 'href="dragon/latest.html"' not in idx     # 入口卡锚点不出现 (CSS 规则仍在)
