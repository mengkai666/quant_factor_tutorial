# -*- coding: utf-8 -*-
"""前端静态依赖必须锁版本、且只有一处真源 (2026-09-12)。

病理: ECharts 的 CDN 地址在三个渲染器里各写一份, 版本还不一致 ——
    主线强度追踪.py / legacy_tracker.py 用浮动的 `echarts@5`,
    lianban_analysis.py 用锁定 `echarts@5.5.1`。
浮动标签的代价是**归档不可复现**: `output/site/reports/` 里 39 期历史报告都指向
`echarts@5`, 上游发一个 5.x 补丁, 那些"历史报告"的渲染就跟着变。

同时全文唯一的**外部**资源就是这个 CDN。它不可达时所有图表空白, 而空白与"当天没有
数据"在页面上无法区分 —— 必须把这句话显式写出来。
"""
import re
import ast
import pathlib

import pytest


def test_echarts_version_is_pinned_to_a_patch_number():
    from web_assets import ECHARTS_CDN, ECHARTS_VERSION, is_pinned

    assert re.fullmatch(r"\d+\.\d+\.\d+", ECHARTS_VERSION), ECHARTS_VERSION
    assert is_pinned(ECHARTS_CDN), f"CDN 用了浮动标签: {ECHARTS_CDN}"
    assert is_pinned("https://x/npm/echarts@5.5.1/dist/e.min.js")
    assert not is_pinned("https://x/npm/echarts@5/dist/e.min.js")
    assert not is_pinned("https://x/npm/echarts/dist/e.min.js")


def test_head_html_carries_the_pinned_url_and_the_notice_script():
    from web_assets import ECHARTS_CDN, echarts_head_html

    html = echarts_head_html()

    assert f'src="{ECHARTS_CDN}"' in html
    # 兜底必须在, 且要说清"是加载失败"而不是让空白冒充无数据。
    assert "window.echarts" in html
    assert "图表库未加载" in html
    assert "不代表当天没有数据" in html
    # 兜底脚本不能依赖任何库 —— 它要能在 ECharts 缺席时执行。
    assert "echarts.init" not in html
    # 覆盖三个渲染器用到的容器类名。
    assert ".chart-container" in html and ".chart-wrap" in html


def test_notice_guard_does_not_duplicate_itself_on_repeat_runs():
    """DOMContentLoaded 之外还会有别的地方调用; 用属性做幂等, 免得叠多张提示。"""
    from web_assets import echarts_head_html

    assert "data-charts-unavailable" in echarts_head_html()


@pytest.mark.parametrize("rel", [
    "src/主线强度追踪.py",
    "src/legacy_tracker.py",
    "src/lianban_analysis.py",
])
def test_no_renderer_hardcodes_an_echarts_cdn_url(rel):
    """三个渲染器都必须走 web_assets, 不许再自己写一份地址。"""
    source = pathlib.Path(rel).read_text(encoding="utf-8")

    assert "cdn.jsdelivr.net/npm/echarts" not in source, (
        f"{rel} 又出现了硬编码的 ECharts 地址; 一律改用 web_assets.echarts_head_html()"
    )
    assert "echarts_head_html" in source, f"{rel} 没有使用 web_assets.echarts_head_html()"


@pytest.mark.parametrize("rel", [
    "src/主线强度追踪.py",
    "src/legacy_tracker.py",
    "src/lianban_analysis.py",
])
def test_placeholder_lives_inside_an_f_string(rel):
    """`{echarts_head_html()}` 必须是 f-string 的**格式化表达式**, 而不是一段普通文本。

    否则它不会被求值, 页面会原样打印这串花括号 —— 一个不报错、只会静默坏掉的失败
    模式 (所有图表都不加载, 且没有任何异常)。

    判定用 `FormattedValue` 节点而不是"某段 f-string 里含这个字符串": 如果占位符被
    粘贴进了 f-string **内部的普通字符串** (或根本不在这段模板里), outer f-string 的
    源码片段照样包含这段文本, 用字符串包含来判会误判为通过。
    """
    source = pathlib.Path(rel).read_text(encoding="utf-8")
    tree = ast.parse(source)

    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FormattedValue)
        and "echarts_head_html" in (ast.get_source_segment(source, node) or "")
    ]

    assert calls, (
        f"{rel}: echarts_head_html() 不是任何 f-string 的格式化表达式 —— "
        "它会以 {echarts_head_html()} 的字面形式出现在页面里"
    )


def test_no_floating_echarts_tag_survives_anywhere_in_src():
    """全局兜底: 除 web_assets(唯一真源) 外, src/ 下不允许出现浮动标签形式的地址。

    只匹配真实 CDN 地址形态 (npm/echarts@...), 免得把文档里提到的版本号也算进来。
    """
    offenders = []
    for path in sorted(pathlib.Path("src").rglob("*.py")):
        if path.name == "web_assets.py":        # 唯一真源, 版本号本来就写在里面
            continue
        text = path.read_text(encoding="utf-8")
        for hit in re.findall(r"npm/echarts@([^/\"'\s]+)", text):
            if not re.fullmatch(r"\d+\.\d+\.\d+", hit):
                offenders.append(f"{path}: echarts@{hit}")

    assert not offenders, f"发现未锁定的 ECharts 版本引用: {offenders}"
