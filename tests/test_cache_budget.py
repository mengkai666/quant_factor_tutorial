# -*- coding: utf-8 -*-
"""进 git 的缓存保留窗口 (src/cache_budget.py) 单测。

全部在 tmp_path 里, 不碰真实 data/。四种载体各验一遍 (CSV / JSONL / 嵌套 JSON /
一天一个文件的目录), 外加两条**结构**判据:
  · CI 提交的每一个路径都必须有人管 (在预算表里, 或明确豁免, 或上限在别处);
  · 不可重算的真值窗口不许被改到分析窗口以下。
"""
import glob
import io
import json
import os
import re
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

import cache_budget as CB  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _dates(n, start=1):
    return [f'202608{d:02d}' for d in range(start, start + n)]


def test_trim_csv_keeps_the_newest_window(tmp_path):
    path = tmp_path / 'plate.csv'
    rows = [{'date': d, 'code': f'sh{600000 + i}'} for d in _dates(10) for i in range(3)]
    pd.DataFrame(rows).to_csv(path, index=False)

    days, removed = CB._trim_csv(str(path), 'date', keep=4, cap_mb=99)
    assert (days, removed) == (6, 18)
    back = pd.read_csv(path, dtype=str)
    assert sorted(back['date'].unique()) == _dates(4, start=7)


def test_trim_csv_preserves_bom(tmp_path):
    """BOM 有无必须原样保留: 顺手改编码 = 整份文件字节全变, 且首列名可能变成
    '﻿日期' 让下游的列引用直接失效。"""
    path = tmp_path / 'zt.csv'
    frame = pd.DataFrame([{'日期': d, '代码': 'sh600000'} for d in _dates(6)])
    frame.to_csv(path, index=False, encoding='utf-8-sig')
    assert path.read_bytes()[:3] == b'\xef\xbb\xbf'

    days, _ = CB._trim_csv(str(path), '日期', keep=2, cap_mb=99)
    assert days == 4
    assert path.read_bytes()[:3] == b'\xef\xbb\xbf', 'BOM 被写掉了'
    assert list(pd.read_csv(path, dtype=str, encoding='utf-8-sig').columns) == ['日期', '代码']


def test_trim_csv_leaves_a_bomless_file_bomless(tmp_path):
    path = tmp_path / 'sentiment.csv'
    pd.DataFrame([{'日期': d, 'up': 4000} for d in _dates(6)]).to_csv(path, index=False)
    CB._trim_csv(str(path), '日期', keep=2, cap_mb=99)
    assert path.read_bytes()[:3] != b'\xef\xbb\xbf', '凭空多了个 BOM'


def test_trim_jsonl_drops_old_days_and_keeps_unreadable_lines(tmp_path):
    path = tmp_path / 'pred.jsonl'
    lines = [json.dumps({'report_date': d, 'payload': 'x'}) for d in _dates(8)]
    lines.insert(3, '{ 这行读不懂')          # 读不懂的行一律保留, 不猜
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    days, removed = CB._trim_jsonl(str(path), 'report_date', keep=3, cap_mb=99)
    assert (days, removed) == (5, 5)
    out = [l for l in path.read_text(encoding='utf-8').splitlines() if l.strip()]
    assert '{ 这行读不懂' in out
    kept = [json.loads(l)['report_date'] for l in out if l.startswith('{"')]
    assert kept == _dates(3, start=6)


def test_trim_nested_json_trims_every_sector(tmp_path):
    path = tmp_path / 'ths.json'
    blob = {name: [{'date': d, 'close': 1.0} for d in
                   [f'2026-08-{x:02d}' for x in range(1, 8)]]
            for name in ('半导体', '光伏')}
    path.write_text(json.dumps(blob, ensure_ascii=False), encoding='utf-8')

    days, removed = CB._trim_nested_json(str(path), 'date', keep=2, cap_mb=99)
    assert days == 5 and removed == 10
    back = json.loads(path.read_text(encoding='utf-8'))
    for series in back.values():
        assert [r['date'] for r in series] == ['2026-08-06', '2026-08-07']


def test_trim_daily_dir_removes_the_oldest_files(tmp_path):
    directory = tmp_path / 'snapshots'
    directory.mkdir()
    for d in [f'2026-08-{x:02d}' for x in range(1, 7)]:
        (directory / f'{d}.json').write_text('{}', encoding='utf-8')

    days, _ = CB._trim_daily_dir(str(directory), keep=2, cap_mb=99)
    assert days == 4
    assert sorted(os.path.basename(f) for f in glob.glob(str(directory / '*.json'))) == \
        ['2026-08-05.json', '2026-08-06.json']


def test_size_cap_keeps_cutting_after_the_window(tmp_path):
    """窗口装得下但体积超了, 就继续从最老的往前删 —— 记录变胖时唯一的兜底。"""
    path = tmp_path / 'fat.jsonl'
    fat = 'x' * 4000                                     # 每行 ~4KB
    path.write_text('\n'.join(json.dumps({'report_date': d, 'blob': fat})
                              for d in _dates(10)) + '\n', encoding='utf-8')
    # 窗口 10 天一天都不删, 但上限 0.02MB 只装得下 ~5 行
    days, _ = CB._trim_jsonl(str(path), 'report_date', keep=10, cap_mb=0.02)
    assert days > 0
    assert os.path.getsize(path) <= 0.02 * 1024 * 1024 * 1.2


def test_cap_never_empties_a_file(tmp_path):
    """上限再小也至少留最新一天: 宁可超上限, 不可留一个空缓存。"""
    path = tmp_path / 'tiny.jsonl'
    path.write_text('\n'.join(json.dumps({'report_date': d, 'blob': 'y' * 5000})
                              for d in _dates(4)) + '\n', encoding='utf-8')
    CB._trim_jsonl(str(path), 'report_date', keep=4, cap_mb=0.0001)
    out = [l for l in path.read_text(encoding='utf-8').splitlines() if l.strip()]
    assert len(out) == 1 and json.loads(out[0])['report_date'] == _dates(4)[-1]


def test_enforce_never_raises_on_a_broken_cache(tmp_path, monkeypatch):
    """瘦身失败只跳过它 —— 绝不能反过来把日报判失败。"""
    (tmp_path / 'broken.csv').write_bytes(b'\x00\x01\x02 not a csv at all')
    monkeypatch.setattr(CB, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(CB, 'CACHE_BUDGET', [('broken.csv', 'date', 5, 1),
                                             ('absent.csv', 'date', 5, 1)])
    assert CB.enforce_cache_budget() == []


def test_every_committed_cache_has_an_owner():
    """CI 提交的每个路径都必须有人管住体积 —— 新加一份缓存就必须在这张表里登记。

    这条测试挡的是"悄悄多进一个从不裁剪的文件": 它进 git 之后每天一份全量 blob,
    等发现时历史已经胖了几百 MB, 删也删不回来。
    """
    workflow = io.open(os.path.join(ROOT, '.github', 'workflows', 'daily_run.yml'),
                       encoding='utf-8').read()
    budgeted = {rel for rel, *_ in CB.CACHE_BUDGET}
    staged = set()
    for line in workflow.splitlines():
        if 'git add' not in line:
            continue
        for token in re.split(r'\s+', line.strip()):
            if token.startswith('data/'):
                staged.add(token[len('data/'):])
    assert staged, 'workflow 里一行 git add data/... 都没解析到, 测试自己坏了'

    unowned = []
    for rel in sorted(staged):
        head = rel.split('/')[0]
        owned = (rel in budgeted or rel in CB.EXEMPT or head in budgeted
                 or head in CB.EXEMPT or head in CB.EXTERNAL_BUDGET)
        if not owned:
            unowned.append(rel)
    assert not unowned, f'CI 提交但没人管体积: {unowned}'


def test_irreplaceable_windows_cover_the_longest_analysis_window():
    """不可重算的真值窗口不许低于回测窗口。

    sentiment 的 up/down 真源是价格缓存 A/D (裁短后算不回来), 涨停池接口对历史日
    返回 0 行 —— 这两份一旦裁掉就是永久空洞, 而 tools/ 里的回测要看 203 天。
    """
    windows = {rel: keep for rel, _, keep, _ in CB.CACHE_BUDGET}
    for rel in ('sentiment_history_cache.csv', '涨停历史缓存.csv'):
        assert windows[rel] >= 250, f'{rel} 窗口 {windows[rel]} 天 < 回测 203 天 + 余量'


@pytest.mark.parametrize('rel,date_col,keep,cap_mb', CB.CACHE_BUDGET)
def test_budget_rows_are_wellformed(rel, date_col, keep, cap_mb):
    assert keep >= 1 and cap_mb > 0
    assert date_col is None or isinstance(date_col, str) and date_col
    # 目录形态 (date_col=None) 必须真是目录名, 不能带扩展名
    if date_col is None:
        assert not os.path.splitext(rel)[1]


def test_real_date_columns_exist_where_the_cache_exists():
    """表里写的日期字段必须真的是那份文件的列名 —— 写错了瘦身会静默不生效。"""
    checked = 0
    for rel, date_col, _keep, _cap in CB.CACHE_BUDGET:
        path = os.path.join(CB.DATA_DIR, rel)
        if date_col is None or not os.path.isfile(path):
            continue
        checked += 1
        if path.endswith('.jsonl'):
            with io.open(path, encoding='utf-8') as handle:
                first = next((l for l in handle if l.strip()), '')
            assert date_col in json.loads(first), f'{rel} 没有字段 {date_col}'
        elif path.endswith('.json'):
            blob = json.load(io.open(path, encoding='utf-8'))
            series = next((v for v in blob.values() if isinstance(v, list) and v), [])
            assert not series or date_col in series[0], f'{rel} 没有字段 {date_col}'
        else:
            head = pd.read_csv(path, dtype=str, encoding='utf-8-sig', nrows=1)
            assert date_col in head.columns, f'{rel} 没有列 {date_col}'
    assert checked, '一份缓存都没查到 (data/ 是空的?)'


def test_no_file_is_both_in_git_and_in_actions_cache():
    """actions/cache 的路径清单里不许出现任何 git 跟踪的文件。

    actions/cache 在 checkout **之后**解包, 会盖掉工作区里的同路径文件。于是
    "既进 git 又进 cache"的文件有一条静默的数据回退通道: git 里是本地+CI 的并集,
    cache 里只有 CI 自己那份, 解包后本地攒的记录当场消失 —— 而 workflow 紧接着
    `git add` 同一个文件, 把这个"消失"提交回 master。留痕类文件 (预测历史 / 阶段
    快照) 不可重算, 丢一行就是永久丢。
    """
    import subprocess

    workflow = io.open(os.path.join(ROOT, '.github', 'workflows', 'daily_run.yml'),
                       encoding='utf-8').read()
    lines = workflow.splitlines()
    # 取 actions/cache 那一步的 path: | 块 (可能有多个 cache 步骤, 全都要查)
    cached_paths = []
    for i, line in enumerate(lines):
        if 'uses: actions/cache' not in line:
            continue
        for j in range(i, len(lines)):
            if re.match(r'\s*-\s+name:', lines[j]) and j > i:
                break                                  # 走到下一步了, 这步没有 path: | 块
            if re.match(r'\s*path:\s*\|\s*$', lines[j]):
                indent = len(lines[j]) - len(lines[j].lstrip())
                for k in range(j + 1, len(lines)):
                    body = lines[k]
                    if not body.strip():
                        continue
                    if len(body) - len(body.lstrip()) <= indent:
                        break
                    if body.strip().startswith('#'):
                        continue
                    cached_paths.append(body.strip())
                break
    assert cached_paths, 'workflow 里一个 actions/cache 的 path 都没解析到'

    tracked = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True,
                             text=True, encoding='utf-8').stdout.splitlines()
    tracked_set = set(tracked)
    tracked_dirs = {os.path.dirname(p) for p in tracked}

    clash = [p for p in cached_paths
             if not p.startswith('~') and (p in tracked_set or p in tracked_dirs)]
    assert not clash, f'既进 git 又进 actions/cache (解包会盖掉 git 版本): {clash}'
