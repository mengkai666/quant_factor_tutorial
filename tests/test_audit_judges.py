# -*- coding: utf-8 -*-
"""体检的"这天到底开没开市"判据 + 薄天补宽的三条闸门。

为什么单独一份: 2026-09-01 的 CI 红是**判据的前提过期**了 ——
`价格缓存无该日行 ⇒ 该日休市` 在价格缓存等于本地全量史时成立, 而 CI 那份是从 git
切片重建的, 切片按覆盖门槛拒收薄天, 于是它天生缺几个真交易日。后果是 10 个真交易日
(每天 29~109 条真涨停) 被判成休市日污染, 还附了"备份后删除这些行"的建议。

所以这里钉的是两件事:
  · 判休市**必须有正面证据** (别的日期化缓存也证不出这天有数据), 无证据一律"不判";
  · 薄天要能从备份补宽, 但只在同血统 / 纯 legacy 天 / 新证券在段内有历史时才动手。
"""
import glob
import json
import os
import sys

import pandas as pd
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'src'))
sys.path.insert(0, os.path.join(_ROOT, 'tools'))

import audit_data_integrity as ADI  # noqa: E402
import import_legacy_price_backup as ILB  # noqa: E402

# ---------------------------------------------------------------- 判据: 开市/休市

def test_classify_absent_days_splits_three_ways():
    """有证据=真交易日, 无证据但在证据窗口内=休市, 早于证据窗口=不判。"""
    evidence = {'20260311', '20260312', '20260901'}
    holiday, trading, unknown = ADI.classify_absent_days(
        ['20260101', '20260311', '20260501', '20260312'], evidence)
    assert trading == ['20260311', '20260312']
    assert holiday == ['20260501']          # 在 20260311~20260901 内却谁都证不出
    assert unknown == ['20260101']          # 早于最老证据, 证不出也否不掉


def test_classify_absent_days_without_evidence_judges_nothing():
    """一份对证缓存都读不到时, 绝不能反过来把所有天判成休市 (那就是原事故)。"""
    holiday, trading, unknown = ADI.classify_absent_days(['20260311', '20260501'], set())
    assert (holiday, trading) == ([], [])
    assert unknown == ['20260311', '20260501']


def test_corroborating_sources_read_all_three_carriers(tmp_path, monkeypatch):
    """CSV / 嵌套 JSON / 一天一个文件的目录都要认, 且两种日期写法都收成 compact。"""
    import paths
    monkeypatch.setattr(paths, 'DATA_DIR', str(tmp_path))
    pd.DataFrame([{'date': '20260311', 'x': 1},
                  {'date': '20260312', 'x': 2}]).to_csv(
        tmp_path / 'cls_plate_cache.csv', index=False)
    (tmp_path / 'ths_sector_hist.json').write_text(
        json.dumps({'半导体': [{'date': '2026-03-16', 'close': 1.0}]}), encoding='utf-8')
    snaps = tmp_path / 'report_daily_snapshots'
    snaps.mkdir()
    (snaps / '2026-03-17.json').write_text('{}', encoding='utf-8')

    dates, detail = ADI.corroborating_trade_dates()
    assert dates == {'20260311', '20260312', '20260316', '20260317'}
    assert detail['cls_plate_cache.csv'] == 2
    assert 'em_stock_plate_cache.csv' not in detail, '不存在的源不该出现在证人名单里'


def test_corroborating_sources_survive_a_broken_file(tmp_path, monkeypatch):
    """一个源坏了只跳过它 —— 体检不能因为对证缓存坏了就崩。"""
    import paths
    monkeypatch.setattr(paths, 'DATA_DIR', str(tmp_path))
    (tmp_path / 'cls_plate_cache.csv').write_bytes(b'\x00\x01 not a csv')
    pd.DataFrame([{'date': '20260311'}]).to_csv(
        tmp_path / 'em_stock_plate_cache.csv', index=False)
    dates, detail = ADI.corroborating_trade_dates()
    assert dates == {'20260311'} and list(detail) == ['em_stock_plate_cache.csv']

# ---------------------------------------------------------------- 判据: 整天空洞

def test_price_gaps_report_days_other_caches_prove_traded():
    evidence = {'20260311', '20260312', '20260313'}
    defects = ADI.audit_price_gaps({'20260311', '20260313'}, evidence, quiet=True)
    assert len(defects) == 1
    assert '1 个交易日' in defects[0]


def test_price_gaps_ignore_evidence_outside_the_cache_range():
    """区间外的证据不算空洞: 缓存起点之前的天本来就不在体检范围内。"""
    evidence = {'20260101', '20260311', '20260901'}
    assert ADI.audit_price_gaps({'20260311'}, evidence, quiet=True) == []


def test_price_gaps_degrade_to_no_defect_without_evidence():
    assert ADI.audit_price_gaps({'20260311'}, set(), quiet=True) == []


# ---------------------------------------------------------------- 判据: raw 覆盖

def _price_frame(rows):
    return pd.DataFrame(rows, columns=['date', 'code', 'close_raw', 'close_qfq',
                                       'close_legacy', 'price_basis', 'source',
                                       'source_timestamp'])


def _day_rows(date, n, raw=True, offset=0):
    return [[date, f'sh{600000 + offset + i}', 10.0 if raw else None, None,
             None if raw else 10.0, 'raw' if raw else 'legacy_mixed', 'test',
             date.replace('-', '')] for i in range(n)]


def test_raw_ratio_uses_the_day_itself_as_denominator():
    """分母必须是**当天的行数**。用窗口中位数做分母时, 口径台阶之下的整段都会被
    误报 (实测 20260805 当天 5204 行 4737 只有 raw = 91%, 被报成 86% 不足)。"""
    frame = _price_frame(_day_rows('2026-08-05', 5204) + _day_rows('2026-08-06', 5538))
    defects = ADI.audit_price(frame, sorted(frame['date'].unique()), quiet=True)
    assert not [d for d in defects if 'raw' in d], defects


def test_zero_raw_day_inside_the_raw_segment_is_a_defect():
    """raw 段中间整天 0 raw: 行数是满的 (legacy 撑着), 比例判据被 >0 过滤掉,
    只有盲区判据看得见。"""
    frame = _price_frame(_day_rows('2026-08-05', 500) +
                         _day_rows('2026-08-06', 500, raw=False) +
                         _day_rows('2026-08-07', 500))
    defects = ADI.audit_price(frame, sorted(frame['date'].unique()), quiet=True)
    assert any('raw' in d for d in defects), defects

# ---------------------------------------------------------------- 补宽的三条闸门

@pytest.fixture()
def widen_sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(ILB, 'PRICE_CACHE', str(tmp_path / 'price_history_cache.csv'))
    return tmp_path


def _legacy_cache(days: dict) -> pd.DataFrame:
    """{日期: 证券数} → 纯 legacy 的缓存表 (代码从 sh600000 起连号)。"""
    rows = []
    for date, n in days.items():
        rows += _day_rows(date, n, raw=False)
    return _price_frame(rows)


def _backup(path, days: dict, close=10.0):
    """老 schema 的备份: date,code,close。"""
    rows = [{'date': d, 'code': f'sh{600000 + i}', 'close': close}
            for d, n in days.items() for i in range(n)]
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


def test_widen_never_touches_a_day_that_has_raw_rows(widen_sandbox, capsys):
    """硬规则 1: 往有 raw 的天掺 legacy 会把整天口径降级, 一律跳过。"""
    cur = _price_frame(_day_rows('2026-03-09', 300, raw=False) +   # 段内有历史的一天
                       _day_rows('2026-03-11', 100) +              # 有 raw 且很薄
                       _day_rows('2026-03-12', 100, raw=False))    # 纯 legacy 且很薄
    bak = _backup(widen_sandbox / 'c.csv.bak.t1', {'2026-03-11': 300, '2026-03-12': 300})
    ILB.widen_thin_days(cur, [bak], threshold=200, apply_=False)
    out = capsys.readouterr().out
    assert '2026-03-11' in out
    assert '2026-03-11: 100 →' not in out, '含 raw 的天被补宽了'
    assert '2026-03-12: 100 → 300' in out, '纯 legacy 的薄天没补'


def test_widen_refuses_a_backup_of_a_different_lineage(widen_sandbox, capsys):
    """闸门 2: 重叠的 (date,code) 收盘价对不上, 说明这份备份是另一条口径/修复前的
    快照, 整天不碰 —— A/D 逐股隔日差分, 同一天混两条血统就不自洽了。"""
    cur = _legacy_cache({'2026-03-11': 100})
    bak = _backup(widen_sandbox / 'c.csv.bak.t2', {'2026-03-11': 300}, close=11.0)
    ILB.widen_thin_days(cur, [bak], threshold=200, apply_=False)
    out = capsys.readouterr().out
    assert '2026-03-11: 100 →' not in out, '非同血统的备份被采信了'


def test_widen_drops_codes_with_no_history_in_the_segment(widen_sandbox, capsys):
    """闸门 3: 备份里那只在 legacy 段内没有任何历史 → 它在段内孤立一天, 隔日差分
    永远配不上对, 只白占行数。剔掉它, 但整天照补。"""
    cur = _legacy_cache({'2026-03-10': 120, '2026-03-11': 100})
    rows = [{'date': '2026-03-11', 'code': f'sh{600000 + i}', 'close': 10.0}
            for i in range(120)]
    rows.append({'date': '2026-03-11', 'code': 'sh999999', 'close': 10.0})  # 段内无历史
    path = widen_sandbox / 'c.csv.bak.t3'
    pd.DataFrame(rows).to_csv(path, index=False)
    ILB.widen_thin_days(cur, [str(path)], threshold=110, apply_=False)
    out = capsys.readouterr().out
    assert '2026-03-11: 100 → 120' in out, '整天被这一只连坐了'

def test_widen_applies_with_local_winning(widen_sandbox):
    """落盘: 只补本地没有的 (code,date), 本地已有的行一整行都不许被替换。

    注意本地行的**值**不能拿来做这条测试的区分标记 —— 值不一致会先撞上血统闸门,
    整天直接跳过。所以标记打在 source 上: 合并后本地那行必须还是本地那行。"""
    cur = _legacy_cache({'2026-03-10': 120, '2026-03-11': 100})
    cur.loc[(cur['date'] == '2026-03-11') & (cur['code'] == 'sh600000'),
            'source'] = 'local_repair'
    cur.to_csv(ILB.PRICE_CACHE, index=False)
    bak = _backup(widen_sandbox / 'c.csv.bak.t4', {'2026-03-11': 120})
    ILB.widen_thin_days(cur, [bak], threshold=110, apply_=True)

    back = pd.read_csv(ILB.PRICE_CACHE, dtype={'code': str, 'date': str})
    day = back.loc[back['date'] == '2026-03-11']
    assert len(day) == 120
    assert day.loc[day['code'] == 'sh600000', 'source'].iloc[0] == 'local_repair'
    added = day.loc[day['code'] == 'sh600100']
    assert str(added['source'].iloc[0]).startswith('legacy_backup:')
    assert glob.glob(ILB.PRICE_CACHE + '.bak.*'), '落盘前没有自动备份'


def test_widen_floor_is_relative_to_the_segment_not_just_4000(widen_sandbox, capsys):
    """判薄两条线取严。只看绝对线 4000 会漏掉 4036~4056 只的天, 而 A/D 是隔日配对:
    那种天会把前后两个 pair 一起拖到 4000 以下 (实测 20260310 配对后只剩 3369)。"""
    days = {f'2026-03-{d:02d}': 5000 for d in range(1, 26)}
    days['2026-03-20'] = 4100                          # 过了绝对线, 但远低于段内口径
    cur = _legacy_cache(days)
    bak = _backup(widen_sandbox / 'c.csv.bak.t5', {'2026-03-20': 5000})
    ILB.widen_thin_days(cur, [bak], threshold=4000, apply_=False)
    out = capsys.readouterr().out
    assert '2026-03-20: 4100 → 5000' in out, '相对线没生效 (只看了绝对 4000)'
