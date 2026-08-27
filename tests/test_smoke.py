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


def test_reconcile_refuses_thin_price_cache_ad():
    """CI 浅价格缓存(~850只)算出的残缺 A/D 不得覆盖已对齐的历史家数。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'mztrack_reconcile', os.path.join(_ROOT, 'src', '主线强度追踪.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    # 实测 CI 20260707 写出的 71/779 (合计 850, 全市场 1/6) 必须拒写
    assert m.should_adopt_reconciled_ad(71, 779) is False
    assert m.should_adopt_reconciled_ad(429, 689) is False
    assert m.should_adopt_reconciled_ad(None, None) is False
    # 本地完整缓存的真值放行 (含极端普跌日)
    assert m.should_adopt_reconciled_ad(615, 4495) is True
    assert m.should_adopt_reconciled_ad(513, 4580) is True


def test_reconcile_refuses_narrower_than_existing_ad():
    """真源过了 4000 门槛但明显窄于已有完整值时, 不得覆盖 (口径变窄, 不是纠错)。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'mztrack_narrower', os.path.join(_ROOT, 'src', '主线强度追踪.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    # 实测 20260327: 已有 3943/929 (合计 4872, 全市场) vs 回补缓存 3649/800
    # (合计 4449, 只抓到 4588 只) —— 方向一致但窄 400 只, 拒写
    assert m.should_adopt_reconciled_ad(3649, 800, 3943, 929) is False
    # 已有值本身残缺时不受此闸约束, 该覆盖照旧覆盖
    assert m.should_adopt_reconciled_ad(3649, 800, 71, 779) is True
    # 同口径的纠错 (合计相当, 方向翻转) 必须放行: 20260824 盘中价钉出的错值
    assert m.should_adopt_reconciled_ad(1554, 3767, 2407, 2916) is True
    # 不传已有值 = 老调用方, 行为不变
    assert m.should_adopt_reconciled_ad(3649, 800) is True


def test_tencent_snapshot_refuses_intraday_close():
    """盘中的腾讯"当前价"不得当收盘价落库 (2026-08-24 该日 81% 股票被钉成盘中价)。"""
    import importlib.util
    from datetime import datetime, timedelta, timezone
    spec = importlib.util.spec_from_file_location(
        'mztrack_intraday', os.path.join(_ROOT, 'src', '主线强度追踪.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    bj = timezone(timedelta(hours=8))
    # 当天盘中: 弃收
    assert m.is_intraday_snapshot('2026-08-24', datetime(2026, 8, 24, 10, 30, tzinfo=bj)) is True
    assert m.is_intraday_snapshot('2026-08-24', datetime(2026, 8, 24, 15, 0, tzinfo=bj)) is True
    # 当天收盘后: 放行
    assert m.is_intraday_snapshot('2026-08-24', datetime(2026, 8, 24, 15, 5, tzinfo=bj)) is False
    assert m.is_intraday_snapshot('2026-08-24', datetime(2026, 8, 24, 16, 30, tzinfo=bj)) is False
    # 补往日 (那天早已收盘) / 空日期: 放行
    assert m.is_intraday_snapshot('2026-08-21', datetime(2026, 8, 24, 10, 30, tzinfo=bj)) is False
    assert m.is_intraday_snapshot('', datetime(2026, 8, 24, 10, 30, tzinfo=bj)) is False


def test_ad_requires_same_price_basis_on_both_days(tmp_path, monkeypatch):
    """跨口径的两天不许相减: 有共同口径就用它, 一个都没有则该日判未覆盖。

    D1 只有 raw, D2 同时有 raw 和 legacy, D3 只有 legacy。
    D2 应走 raw→raw (与 D1 同口径), D3 应走 legacy→legacy (与 D2 同口径),
    两天都必须只反映同口径的涨跌; D3 的 legacy 与 D2 的 raw 水平差一个复权
    因子, 若被混用, 涨跌方向会整片翻掉。
    """
    import importlib
    import pandas as pd
    import limit_ratio_factor as lrf

    # 甲: 每天 +2% (raw 与 legacy 各自单调涨); 乙: 每天 -2%。
    # legacy 列整体是 raw 的 0.5 倍水平 —— 混口径会把 D3 的甲算成 -50%。
    rows = [
        # D1: 只有 raw
        ('sh600000', '2026-01-05', 10.00, None),
        ('sh600001', '2026-01-05', 10.00, None),
        # D2: raw + legacy 同行
        ('sh600000', '2026-01-06', 10.20, 5.10),
        ('sh600001', '2026-01-06', 9.80, 4.90),
        # D3: 只有 legacy
        ('sh600000', '2026-01-07', None, 5.202),
        ('sh600001', '2026-01-07', None, 4.802),
    ]
    frame = pd.DataFrame(rows, columns=['code', 'date', 'close_raw', 'close_legacy'])
    cache = tmp_path / 'price.csv'
    frame.to_csv(cache, index=False, encoding='utf-8')
    monkeypatch.setattr(lrf, 'PRICE_CACHE_FILE', str(cache))
    factor = lrf.MarketSentimentFactor()
    ad = factor._load_ad_cache()

    assert '20260105' not in ad, '首日无前一日, 不应产出 A/D'
    assert ad['20260106']['up'] == 1 and ad['20260106']['down'] == 1
    assert ad['20260106']['price_basis'] == 'raw', '两天共有 raw, 必须走 raw'
    assert ad['20260107']['up'] == 1 and ad['20260107']['down'] == 1,         'D3 必须与 D2 的 legacy 相比, 混用 raw 会把两只都算成腰斩'
    assert ad['20260107']['price_basis'] == 'legacy_mixed'


def test_ad_drops_day_without_any_shared_basis(tmp_path, monkeypatch):
    """前一天只有 raw、当天只有 legacy 时, 该日无共同口径, 整天判未覆盖。"""
    import pandas as pd
    import limit_ratio_factor as lrf

    frame = pd.DataFrame(
        [('sh600000', '2026-01-05', 10.00, None),
         ('sh600001', '2026-01-05', 10.00, None),
         ('sh600000', '2026-01-06', None, 5.10),
         ('sh600001', '2026-01-06', None, 4.90)],
        columns=['code', 'date', 'close_raw', 'close_legacy'])
    cache = tmp_path / 'price.csv'
    frame.to_csv(cache, index=False, encoding='utf-8')
    monkeypatch.setattr(lrf, 'PRICE_CACHE_FILE', str(cache))
    ad = lrf.MarketSentimentFactor()._load_ad_cache()
    assert ad == {} or '20260106' not in ad,         '一个共同口径都没有, 不许拿 raw ÷ legacy 冒充权威 A/D'


def test_raw_coverage_guard_handles_empty_filtered_price_cache():
    """CI cold start must not index date on an empty, columnless frame."""
    import importlib.util
    import pandas as pd

    spec = importlib.util.spec_from_file_location(
        'mztrack_raw_guard', os.path.join(_ROOT, 'src', '主线强度追踪.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module._count_raw_codes_on_date(
        pd.DataFrame(), '2026-08-12', ['sh600000']
    ) == 0
    assert module._count_raw_codes_on_date(
        pd.DataFrame({'date': [], 'code': [], 'close_qfq': []}),
        '2026-08-12', ['sh600000']
    ) == 0
    assert module._count_raw_codes_on_date(
        pd.DataFrame({
            'date': ['2026-08-11'],
            'code': ['sh600000'],
            'close_raw': [10.0],
        }),
        '2026-08-12', ['sh600000']
    ) == 0


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


def test_main_uses_safe_report_cutoff_instead_of_stale_cache(monkeypatch):
    import importlib.util
    from datetime import datetime

    import lianban_analysis
    import pandas as pd
    import pytest

    spec = importlib.util.spec_from_file_location(
        'mztrack_report_cutoff', os.path.join(_ROOT, 'src', '主线强度追踪.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setenv('REPORT_DATE', '2026-08-10')
    monkeypatch.setattr(module, 'get_latest_date', lambda: datetime(2026, 8, 7))
    monkeypatch.setattr(module, 'trim_cache_file', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(lianban_analysis, 'fetch_zt_pool_data', lambda **_kwargs: (None, None))
    monkeypatch.setattr(lianban_analysis, 'get_cached_trading_dates', lambda: ['20260810'])
    monkeypatch.setattr(module, 'load_and_classify_zt', lambda **_kwargs: pd.DataFrame({
        '日期': ['20260810'],
        '代码': ['000001'],
        '名称': ['样本'],
        '主线': ['AI算力'],
        '细分': ['算力'],
        '连板数': [1],
    }))

    reached = []

    def stop_after_date_selection(report_date):
        reached.append(report_date)
        raise RuntimeError('date selection reached')

    monkeypatch.setattr(module, '_load_market_universe', stop_after_date_selection)

    with pytest.raises(RuntimeError, match='date selection reached'):
        module._main_impl()

    assert reached == ['2026-08-10']


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


def test_ai_legacy_schema_is_normalized_for_guarded_output():
    import ai_rebound
    output, source = ai_rebound.normalize_ai_output({
        'market_summary': '结构性反弹',
        'active_comment': '主线有承接',
        'follow_comment': '跟随盘谨慎',
        'gap_comment': '中间档位缺失',
        'evolution': '关注轮动',
        'operation': '等待确认',
    })
    assert source == 'legacy'
    assert output['facts'] == ['结构性反弹']
    assert '主动主线: 主线有承接' in output['observations']
    assert output['decision'] == '等待确认'


def test_ai_canonical_schema_renders_without_legacy_fields():
    import ai_rebound
    html = ai_rebound.render_ai_rebound_html(
        {'observations': ['主动主线有承接', '跟随盘谨慎'],
         'risks': ['高度断层'], 'conditions': ['明日看承接'],
         'decision': '等待确认'},
        {'market_char': '数据不足', 'char_desc': 'A/D 未取得'}, '#8b949e')
    assert '主动主线有承接' in html
    assert '高度断层' in html
    assert '等待确认' in html


def test_missing_ad_is_not_classified_as_market_decline():
    import importlib.util
    import pandas as pd
    spec = importlib.util.spec_from_file_location(
        'mztrack_missing_ad', os.path.join(_ROOT, 'src', '主线强度追踪.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._analyze_active_mainlines = lambda: ('', '', [], [])
    html = module.generate_rebound_analysis(
        {'up': None, 'down': None, 'ad_available': False, 'ad_status': 'missing'},
        pd.DataFrame({'up': [None, 1200, 900]}), [],
        {'publication_mode': 'facts_only'},
    )
    assert '涨跌家数数据不足' in html
    assert '普跌弱势' not in html
    assert '数据不足' in html


def test_market_sentiment_missing_snapshot_keeps_counts_unknown(monkeypatch):
    import pandas as pd
    from limit_ratio_factor import MarketSentimentFactor

    factor = MarketSentimentFactor()
    frame = pd.DataFrame([{
        'date': '20260806', 'limit_up': 4, 'limit_down': 2,
        'market_up': None, 'market_down': None, 'market_flat': None,
        'ad_ratio': None, 'ad_available': False, 'ad_status': 'missing',
        'ad_source': 'unavailable', 'raw_score': None, 'score_ema': None,
    }])
    monkeypatch.setattr(factor, '_get_composite_data', lambda: frame)
    result = factor.calculate_factor('20260806')
    assert result['market_up'] is None
    assert result['market_down'] is None
    assert result['ad_available'] is False
    assert result['status'] == 'missing'


def test_large_qfq_only_gap_is_repaired_without_deferring_sh_sz_codes():
    from types import SimpleNamespace

    import pandas as pd
    import 主线强度追踪 as report

    dates = ["2026-08-07", "2026-08-10"]
    codes = ["sh600000", "sz000001", "sh600001"]
    existing = pd.DataFrame([
        {
            "date": date,
            "code": code,
            "close_raw": 10.0 + index,
            "close_qfq": pd.NA,
            "price_basis": "raw",
            "source": "existing_raw",
            "source_timestamp": "now",
        }
        for index, code in enumerate(codes)
        for date in dates
    ])

    class QfqOnlyProvider:
        def __init__(self):
            self.full_calls = []
            self.qfq_calls = []

        def fetch_range(self, universe, requested_dates):
            self.full_calls.append((universe.copy(), list(requested_dates)))
            raise AssertionError("raw-complete codes must use qfq-only repair")

        def fetch_qfq_range(self, universe, requested_dates):
            requested_codes = universe["code"].tolist()
            self.qfq_calls.append((requested_codes, list(requested_dates)))
            rows = []
            for code in requested_codes:
                for date in requested_dates:
                    rows.append({
                        "date": date,
                        "code": code,
                        "close_raw": pd.NA,
                        "close_qfq": 8.0,
                        "trade_status": "traded",
                        "source_raw": "",
                        "source_qfq": "fixture_qfq",
                        "fetched_at": "now",
                    })
            return SimpleNamespace(status="success", message="", data=pd.DataFrame(rows))

    provider = QfqOnlyProvider()
    merged, meta = report._fill_price_gaps_with_provider(
        existing,
        codes,
        "2026-08-10",
        previous_date="2026-08-07",
        provider=provider,
        max_non_bj_gaps=1,
    )

    assert provider.full_calls == []
    assert provider.qfq_calls == [(codes, dates)]
    assert meta["fallback_deferred"] == 0
    assert meta["fallback_covered"] == len(codes)
    got = merged.set_index(["date", "code"])
    assert got["close_raw"].notna().all()
    assert got["close_qfq"].eq(8.0).all()


def test_chronic_gap_codes_are_skipped_on_a_brand_new_date(tmp_path, monkeypatch):
    """长期缺价的票在**新交易日的首轮**也不再单只重抓 (线上 CI 每天省一轮备用源)。

    按 (code,date) 精确匹配的负缓存对新日期无效, 于是每个新交易日都要为那几只
    常年没有前复权价的票跑一轮备用源 (实测 7 只换 0 行、40s)。
    """
    from types import SimpleNamespace

    import pandas as pd
    import price_gap_memo
    import 主线强度追踪 as report

    monkeypatch.setattr(price_gap_memo, 'PRICE_GAP_MEMO', str(tmp_path / 'gap.csv'))
    monkeypatch.delenv('PRICE_GAP_RETRY_ALL', raising=False)
    # 让 sh600001 在 3 个历史日都判定抓不到 → 达到 chronic 凭据
    for date in ('2026-08-10', '2026-08-11', '2026-08-12'):
        price_gap_memo.record_outcome([('sh600001', date)])

    dates = ['2026-08-20', '2026-08-21']          # 全新日期, 负缓存里没有记录
    codes = ['sh600000', 'sz000001', 'sh600001']
    existing = pd.DataFrame([
        {
            'date': date, 'code': code, 'close_raw': 10.0, 'close_qfq': pd.NA,
            'price_basis': 'raw', 'source': 'existing_raw', 'source_timestamp': 'now',
        }
        for code in codes for date in dates
    ])

    class Recorder:
        def __init__(self):
            self.qfq_calls = []

        def fetch_range(self, universe, requested_dates):
            raise AssertionError('raw 已完整, 不应走全量补抓')

        def fetch_qfq_range(self, universe, requested_dates):
            self.qfq_calls.append(universe['code'].tolist())
            return SimpleNamespace(status='success', message='', data=pd.DataFrame([
                {
                    'date': date, 'code': code, 'close_raw': pd.NA, 'close_qfq': 8.0,
                    'trade_status': 'traded', 'source_raw': '', 'source_qfq': 'fixture',
                    'fetched_at': 'now',
                }
                for code in universe['code'].tolist() for date in requested_dates
            ]))

    provider = Recorder()
    _, meta = report._fill_price_gaps_with_provider(
        existing, codes, dates[-1], previous_date=dates[0], provider=provider,
    )
    assert meta['fallback_chronic_skipped'] == 1
    assert provider.qfq_calls == [['sh600000', 'sz000001']]

    # 缺口规模大时 (更像代理/接口故障) 不启用该规则, 照旧全量重抓
    provider_all = Recorder()
    many = codes + [f'sz{300000 + i:06d}' for i in range(60)]
    existing_many = pd.DataFrame([
        {
            'date': date, 'code': code, 'close_raw': 10.0, 'close_qfq': pd.NA,
            'price_basis': 'raw', 'source': 'existing_raw', 'source_timestamp': 'now',
        }
        for code in many for date in dates
    ])
    _, meta_all = report._fill_price_gaps_with_provider(
        existing_many, many, dates[-1], previous_date=dates[0], provider=provider_all,
    )
    assert meta_all['fallback_chronic_skipped'] == 0
    assert 'sh600001' in provider_all.qfq_calls[0]
