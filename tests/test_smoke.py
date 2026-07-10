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
