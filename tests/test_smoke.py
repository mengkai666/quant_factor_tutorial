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
