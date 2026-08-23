# -*- coding: utf-8 -*-
"""价格缺口负缓存单元测试 (src/price_gap_memo.py)。

覆盖五件事:
  ① 历史日一次失败即可跳过 / 当天需两次且受 TTL 约束;
  ② 成功会清除记录, 偶发网络故障不会被永久钉死;
  ③ 系统性故障护栏: 整轮大面积失败不记账 (代理挂掉不会把全市场误钉成抓不到);
  ④ PRICE_GAP_RETRY_ALL=1 强制重试, 且读坏文件时静默降级不抛异常;
  ⑤ 长期缺价 chronic_codes: 3 个历史日为凭据、7 天探针自愈、当天记录不算凭据。
"""
import importlib
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))


@pytest.fixture
def memo(tmp_path, monkeypatch):
    """把负缓存文件重定向到 tmp_path, 避免污染真实 data/。"""
    import price_gap_memo as module
    module = importlib.reload(module)
    monkeypatch.setattr(module, 'PRICE_GAP_MEMO', str(tmp_path / 'gap.csv'))
    monkeypatch.delenv('PRICE_GAP_RETRY_ALL', raising=False)
    return module


def test_past_date_skipped_after_single_failure(memo):
    memo.record_outcome([('sz300176', '2020-01-02')])
    assert memo.unobtainable_codes(['sz300176'], ['2020-01-02']) == {'sz300176'}
    # 未记账的代码不受影响
    assert memo.unobtainable_codes(['sh600000'], ['2020-01-02']) == set()


def test_today_needs_two_failures_and_expires(memo):
    today = time.strftime('%Y-%m-%d')
    now = time.time()
    memo.record_outcome([('sz300176', today)], now=now)
    assert memo.unobtainable_codes(['sz300176'], [today], now=now) == set()
    memo.record_outcome([('sz300176', today)], now=now)
    assert memo.unobtainable_codes(['sz300176'], [today], now=now) == {'sz300176'}
    # 超过 TTL 后重新放行, 盘中数据晚出也能补上
    later = now + memo.TTL_TODAY + 1
    assert memo.unobtainable_codes(['sz300176'], [today], now=later) == set()


def test_success_clears_record(memo):
    memo.record_outcome([('sz300176', '2020-01-02')])
    assert memo.unobtainable_codes(['sz300176'], ['2020-01-02']) == {'sz300176'}
    memo.record_outcome([], [('sz300176', '2020-01-02')])
    assert memo.unobtainable_codes(['sz300176'], ['2020-01-02']) == set()


def test_all_dates_must_be_unobtainable(memo):
    memo.record_outcome([('sz300176', '2020-01-02')])
    # 只有一天判定抓不到时, 不能跳过整只票
    assert memo.unobtainable_codes(
        ['sz300176'], ['2020-01-02', '2020-01-03']) == set()
    memo.record_outcome([('sz300176', '2020-01-03')])
    assert memo.unobtainable_codes(
        ['sz300176'], ['2020-01-02', '2020-01-03']) == {'sz300176'}


def test_systemic_failure_is_not_recorded(memo):
    """代理挂掉导致整轮全失败时不记账, 否则全市场会被误判成抓不到。"""
    pairs = [(f'sz{300000 + i:06d}', '2020-01-02') for i in range(200)]
    assert memo.record_outcome(pairs, attempted=len(pairs)) is None
    codes = [code for code, _ in pairs]
    assert memo.unobtainable_codes(codes, ['2020-01-02']) == set()
    # 小批量失败 (真的就是这几只没有价) 仍照常记账
    few = pairs[:5]
    assert memo.record_outcome(few, attempted=len(few)) == len(few)
    assert memo.unobtainable_codes([c for c, _ in few], ['2020-01-02']) == {
        c for c, _ in few}


def test_force_retry_env_disables_skipping(memo, monkeypatch):
    memo.record_outcome([('sz300176', '2020-01-02')])
    monkeypatch.setenv('PRICE_GAP_RETRY_ALL', '1')
    assert memo.load_memo() == {}
    assert memo.unobtainable_codes(['sz300176'], ['2020-01-02']) == set()


def test_corrupt_file_degrades_silently(memo):
    with open(memo.PRICE_GAP_MEMO, 'w', encoding='utf-8') as handle:
        handle.write('not,a,valid\x00\x00memo file\n"unclosed')
    assert memo.load_memo() == {}
    assert memo.unobtainable_codes(['sz300176'], ['2020-01-02']) == set()


def test_empty_inputs_are_noops(memo):
    assert memo.unobtainable_codes([], ['2020-01-02']) == set()
    assert memo.unobtainable_codes(['sz300176'], []) == set()
    assert memo.record_outcome([], []) is None


# ─────────────────────────── 长期缺价 (新日期首轮) ───────────────────────────
def _arm_chronic(memo, code, dates, now):
    """在给定历史日上各记一次失败, 让 code 达到 chronic 凭据。"""
    for date in dates:
        memo.record_outcome([(code, date)], now=now)


def test_chronic_needs_enough_distinct_dates(memo):
    now = time.time()
    _arm_chronic(memo, 'sz300176', ['2020-01-02', '2020-01-03'], now)
    assert memo.chronic_codes(['sz300176'], now=now) == set()
    _arm_chronic(memo, 'sz300176', ['2020-01-06'], now)
    assert memo.chronic_codes(['sz300176'], now=now) == {'sz300176'}
    # 未记账的代码不受影响
    assert memo.chronic_codes(['sh600000'], now=now) == set()


def test_chronic_probe_releases_after_interval(memo):
    now = time.time()
    _arm_chronic(memo, 'sz300176', ['2020-01-02', '2020-01-03', '2020-01-06'], now)
    assert memo.chronic_codes(['sz300176'], now=now) == {'sz300176'}
    later = now + memo.CHRONIC_PROBE_DAYS * 86400 + 1
    # 超过探针间隔后放行一次真实尝试, 复牌/接口修复能自愈
    assert memo.chronic_codes(['sz300176'], now=later) == set()


def test_chronic_ignores_today_records(memo):
    """当天的失败不算长期凭据 (盘中数据可能只是晚出)。"""
    today = time.strftime('%Y-%m-%d')
    now = time.time()
    for _ in range(4):
        memo.record_outcome([('sz300176', today)], now=now)
    assert memo.chronic_codes(['sz300176'], now=now) == set()


def test_chronic_respects_force_retry(memo, monkeypatch):
    now = time.time()
    _arm_chronic(memo, 'sz300176', ['2020-01-02', '2020-01-03', '2020-01-06'], now)
    monkeypatch.setenv('PRICE_GAP_RETRY_ALL', '1')
    assert memo.chronic_codes(['sz300176'], now=now) == set()


def test_chronic_empty_inputs(memo):
    assert memo.chronic_codes([]) == set()
    assert memo.chronic_codes(['sz300176']) == set()
