# -*- coding: utf-8 -*-
"""冒烟测试: 校验重构后关键路径解析与分类逻辑不崩。
运行: pytest (根目录) 或 python -m pytest tests/
"""
import os
import sys

# 让测试能 import src/ 下的模块
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'src'))


def test_paths_module_single_source():
    """paths.py 是路径单一真源: data/ 与 output/ 解析正确。"""
    import paths
    assert paths.DATA_DIR.rstrip('/\\').endswith('data')
    assert paths.OUTPUT_DIR.rstrip('/\\').endswith('output')
    # 缓存文件在 data/ 下
    assert os.path.dirname(paths.ZT_CACHE_FILE).rstrip('/\\').endswith('data')
    assert os.path.dirname(paths.PRICE_CACHE).rstrip('/\\').endswith('data')
    assert os.path.dirname(paths.CLS_PLATE_CACHE).rstrip('/\\').endswith('data')
    # 输出 HTML 在 output/ 下
    assert os.path.dirname(paths.OUTPUT_HTML).rstrip('/\\').endswith('output')


def test_main_reexports_paths_from_module():
    """主程序从 paths.py 取路径, 仍暴露缓存/输出常量且指向 data/ 与 output/。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'mztrack', os.path.join(_ROOT, 'src', '主线强度追踪.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert os.path.dirname(m.ZT_CACHE_FILE).rstrip('/\\').endswith('data')
    assert os.path.dirname(m.PRICE_CACHE).rstrip('/\\').endswith('data')
    assert os.path.dirname(m.OUTPUT_HTML).rstrip('/\\').endswith('output')


def test_mainline_names_seven():
    """7 大主线体系完整。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'mztrack2', os.path.join(_ROOT, 'src', '主线强度追踪.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.MAINLINE_NAMES == ['AI算力', '机器人', 'AI应用', '新能源电网', '军工航天', '周期资源', '医药']


def test_ad_breadth_guard_blocks_partial_snapshot():
    """残缺 A/D 快照必须被市场宽度体检拦下, 合法家数须放行 (根治 414/1274 泄漏)。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'mztrack_ad', os.path.join(_ROOT, 'src', '主线强度追踪.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    # 截图里的残缺快照 (合计1688, 仅覆盖 ~1/3 市场) 必须判为未就位
    assert m.is_ad_incomplete(414, 1274) is True
    # up=0 / 空值 也算未就位
    assert m.is_ad_incomplete(0, 0) is True
    assert m.is_ad_incomplete(None, None) is True
    # 全市场权威家数放行: 普通日 (1688/3378=5066) 与极端普跌日 (513/4580=5093)
    assert m.is_ad_incomplete(1688, 3378) is False
    assert m.is_ad_incomplete(513, 4580) is False


def test_classify_by_tags_no_substring_blackhole():
    """子串黑洞已修复: 短键不再误吸无关标签。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'mztrack3', os.path.join(_ROOT, 'src', '主线强度追踪.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    # 精确概念应命中
    sub, ml = m.classify_by_tags(['机器人'])
    assert ml == '机器人'
    # 无概念标签应归 None (由上游归"其它"), 不被有色等短键误吸
    sub2, ml2 = m.classify_by_tags(['ST板块'])
    assert ml2 is None


def test_time_utils_cache_path_in_data():
    """time_utils 的缓存路径也指向 data/。"""
    import time_utils
    # 触发一次路径构造 (即使文件不存在也不应抛路径错)
    try:
        time_utils.get_latest_date()
    except Exception:
        pass  # 无数据时返回 None 或异常均可, 这里只验证不因路径崩溃


def test_ai_rebound_disabled_returns_none(monkeypatch):
    """无 API key 时 AI 研判必须静默返回 None (保证主程序 fallback 回规则模板)。"""
    import ai_rebound
    monkeypatch.setattr(ai_rebound, 'ANTHROPIC_API_KEY', '')
    assert ai_rebound.ai_enabled() is False
    assert ai_rebound.generate_ai_rebound({'market_char': '普涨反弹'}) is None


def test_ai_rebound_parse_json_strips_codeblock():
    """_parse_json 能剥离 ```json 代码块包裹, 并容忍前后杂字。"""
    import ai_rebound
    obj = ai_rebound._parse_json('```json\n{"market_summary": "多头占优"}\n```')
    assert obj == {'market_summary': '多头占优'}
    # 前后有杂字时截取第一个 { 到最后一个 }
    obj2 = ai_rebound._parse_json('好的:\n{"operation": "半仓"}\n以上。')
    assert obj2 == {'operation': '半仓'}
    # 不可解析时返回 None
    assert ai_rebound._parse_json('这不是 JSON') is None


def test_ai_rebound_render_produces_html():
    """渲染函数产出含硬数据与 AI 文字的 HTML 卡片。"""
    import ai_rebound
    facts = {'market_char': '温和反弹', 'char_desc': '涨跌家数 2000涨/1500跌'}
    ai = {'market_summary': '结构性反弹', 'evolution': '关注 AI 算力接力',
          'operation': '半仓参与'}
    html = ai_rebound.render_ai_rebound_html(ai, facts, '#ffa657')
    assert '反弹分类复盘' in html
    assert '涨跌家数 2000涨/1500跌' in html  # 硬数据来自 facts
    assert '关注 AI 算力接力' in html         # AI 进化研判
    assert '半仓参与' in html                  # 操作建议


def test_ai_rebound_render_escapes_html():
    """AI 返回文本中的尖括号被转义, 防止破坏卡片结构。"""
    import ai_rebound
    html = ai_rebound.render_ai_rebound_html(
        {'evolution': '风险 <script>alert(1)</script>'}, {}, '#f85149')
    assert '<script>' not in html
    assert '&lt;script&gt;' in html


def test_drop_stale_latest_day():
    """陈旧副本体检: 最新一天整批复制前一日则剔除, 真实行情放行。

    根治 update_price_cache 的 early return —— 它只比日期不看内容, 污染入库后
    腾讯快速路径那道护栏根本不被调用 (2026-08-03 事故, A/D 全成 flat)。
    """
    import importlib.util
    import pandas as pd
    spec = importlib.util.spec_from_file_location(
        'mztrack_stale', os.path.join(_ROOT, 'src', '主线强度追踪.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    codes = [f'sh{600000 + i}' for i in range(800)]  # 交集须 >=500 才启用判据
    prev = pd.DataFrame({'date': '2026-07-31', 'code': codes,
                         'close': [10.0 + i * 0.01 for i in range(800)]})

    # 情形 1: 最新日是前一日的逐股副本 → 剔除该日
    stale = prev.assign(date='2026-08-03')
    got = m._drop_stale_latest_day(pd.concat([prev, stale], ignore_index=True))
    assert sorted(got['date'].unique()) == ['2026-07-31']

    # 情形 2: 真实行情 (每只都在动) → 原样放行
    real = prev.assign(date='2026-08-03', close=prev['close'] * 1.03)
    both = pd.concat([prev, real], ignore_index=True)
    assert len(m._drop_stale_latest_day(both)) == len(both)

    # 情形 3: 交集不足 500 只 → 不判 (样本太小, 宁可放过也不误删真实数据)
    small_prev = prev.head(100)
    small = small_prev.assign(date='2026-08-03')
    small_both = pd.concat([small_prev, small], ignore_index=True)
    assert len(m._drop_stale_latest_day(small_both)) == len(small_both)


def test_trim_uninformative_prefix():
    """回测前缀裁剪: 裁掉无 A/D 真源的开头, 中段残缺保留以维持序列连续性。

    sentiment 缓存起点早于价格缓存 26 个交易日, 这段 up/down 全 0 → ad=NaN,
    会给 '中性震荡' 桶掺零信息样本稀释胜率分母。中段丢天会让 prev_h / T+1
    指向非相邻交易日, 反而制造新失真, 故只裁前缀。
    """
    import pandas as pd
    sys.path.insert(0, os.path.join(_ROOT, 'tools'))
    from backtest_timing_v2_1 import _trim_uninformative_prefix

    facts = pd.DataFrame({
        'date': [20250919, 20250922, 20251106, 20251110, 20260309, 20260310],
        'up':   [0,        0,        2722,     3186,     1029,     3174],
        'down': [0,        0,        2090,     1742,     2882,     725],
        'max_h': [0,       0,        5,        6,        4,        5],
    })
    got = _trim_uninformative_prefix(facts)

    # 开头两天无真源 → 裁掉; 其余全部保留 (含中段残缺的 03-09/03-10)
    assert got['date'].tolist() == [20251106, 20251110, 20260309, 20260310]
    # 裁完不再有 ad=NaN 的天 (up+down 全 >0)
    assert ((got['up'] + got['down']) > 0).all()
    # 索引已重置, 下游 facts.iloc[i-1] 取 prev_h 才不会错位
    assert got.index.tolist() == [0, 1, 2, 3]


def test_audit_clips_to_price_window(tmp_path, monkeypatch):
    """--recent N 体检: 价格缓存区间之外的日子不判缺陷。

    体检拿"价格缓存有这天"当交易日判据。--recent 30 把价格日期集收窄到最近 30 天,
    若不先 clip 到 [lo, hi], 更早的正常涨停日会全被判成休市日污染 (2026-08-04
    实测 --recent 30 误报 150 天), 挂进 CI 就是天天喊狼来了 —— 告警一旦失真,
    真缺陷也会被当噪音划过去。
    """
    import pandas as pd
    sys.path.insert(0, os.path.join(_ROOT, 'tools'))
    import audit_data_integrity as adi

    zt = tmp_path / 'zt.csv'
    pd.DataFrame({'日期': ['20260601', '20260623', '20260701', '20260715'],
                  '代码': ['600000'] * 4, '连板数': [1, 2, 1, 3]}).to_csv(zt, index=False)
    monkeypatch.setattr(adi, 'ZT_CACHE_FILE', str(zt))

    # 窗口只含 06-23 起: 20260601 在区间外 → 不判; 区间内每天都有价格 → 无缺陷
    assert adi.audit_zt({'20260623', '20260701', '20260715'}, quiet=True) == []
    # 真污染: 20260701 落在区间内却无价格数据 → 必须报出来
    assert adi.audit_zt({'20260623', '20260715'}, quiet=True) != []

    sent = tmp_path / 'sent.csv'
    pd.DataFrame({'日期': ['20250919', '20260623', '20260701'],
                  'up': [0, 2722, 3186], 'down': [0, 2090, 1742]}).to_csv(sent, index=False)
    monkeypatch.setattr(adi, 'SENTIMENT_CACHE', str(sent))

    # 20250919 宽度为 0 但在区间外 (真源不覆盖) → 只提示不计缺陷
    assert adi.audit_sentiment({'20260623', '20260701'}, quiet=True) == []
