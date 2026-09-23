# -*- coding: utf-8 -*-
"""词云的内联 SVG 渲染 (2026-09-12)。

病理: 两张 800×400 的 base64 PNG 占报告 268 KB(24.5%), 且占 gzip 后体积的约 70% ——
gzip 295 KB 去掉它们只剩 89.8 KB。位图还带来: 拉伸发糊、词不可选中/搜索、
颜色字号焊死在像素里。

判据刻意不写成"看着差不多": 逐个词比对**字号/颜色/坐标**是否与 `layout_` 一致,
再钉住"没有 base64"和"体积量级"。坐标系是这里唯一的真风险点 ——
`layout_` 的位置是 (y, x), 而 SVG 的 <text> 以基线为锚, 两者都对错了才会"看起来还行但整体偏移"。
"""
import re

import pytest

from wordcloud_svg import advance_em, render_svg


def _layout():
    """模拟 WordCloud.layout_ 的条目: ((word, count), font_size, (y, x), orientation, color)。"""
    from PIL import Image

    return [
        (("绿电", 12.0), 90.0, (160.0, 274.0), None, "#e3b341"),
        (("电网设备", 9.0), 79.0, (22.0, 130.0), None, "#7ee787"),
        (("半导体", 7.0), 70.0, (64.0, 527.0), Image.ROTATE_90, "#ff7b72"),
    ]


def test_every_word_is_emitted_once_with_its_size_and_colour():
    svg = render_svg(_layout(), 800, 400)

    for word in ("绿电", "电网设备", "半导体"):
        assert svg.count(f">{word}<") == 1, f"{word} 出现次数不对"
    assert 'font-size="90.0"' in svg
    assert 'fill="#e3b341"' in svg
    assert 'fill="#7ee787"' in svg


def test_horizontal_words_anchor_on_the_box_centre_not_the_baseline():
    """PIL 以左上角画, SVG 以基线画 —— 不补偿就会整体上移半个字高。"""
    svg = render_svg([(("绿电", 1.0), 90.0, (160.0, 274.0), None, "#fff")], 800, 400)

    # x 直接取 layout 的横向坐标; y 是排版盒子的竖直中点 = 160 + 90/2
    assert 'x="274.0"' in svg
    assert 'y="205.0"' in svg
    assert 'dominant-baseline="central"' in svg


def test_vertical_words_rotate_and_anchor_on_the_bottom_centre_of_their_strip():
    from PIL import Image

    svg = render_svg([(("半导体", 1.0), 70.0, (64.0, 527.0), Image.ROTATE_90, "#fff")], 800, 400)

    # 竖条水平中心 = 527 + 70/2 = 562; 文字自下而上, 起点在 pos_y + 词宽
    assert "rotate(-90)" in svg
    assert 'translate(562.0 ' in svg
    expected_y = 64.0 + advance_em("半导体") * 70.0
    assert f"translate(562.0 {expected_y:.1f})" in svg


def test_vertical_detection_matches_pils_real_constants():
    """踩过的坑: `Image.ROTATE_90` 是 **2** 而不是 90。

    合成用例里写 orientation=90 会让测试全绿, 而真实 `layout_` 里是 2 ——
    于是竖排词被当成横排画成一条水平线, 还不报错。这条断言把真常量钉住。
    """
    from PIL import Image

    from wordcloud_svg import _is_vertical

    assert Image.ROTATE_90 == 2, "Pillow 改了常量, wordcloud_svg 的判据要跟着核对"
    assert Image.ROTATE_270 == 4
    assert _is_vertical(Image.ROTATE_90)
    assert _is_vertical(Image.ROTATE_270)
    assert not _is_vertical(None)
    assert not _is_vertical(Image.ROTATE_180), "180 度仍属横排方向"
    assert not _is_vertical(0)


def test_a_real_wordcloud_layout_is_read_as_vertical():
    """端到端: 真拿 wordcloud 排一次竖排, 确认 SVG 里出的是 rotate(-90)。"""
    pytest.importorskip("wordcloud")
    from collections import Counter

    from PIL import Image
    from wordcloud import WordCloud

    counter = Counter({"绿电": 9, "电网设备": 8, "半导体": 7, "煤炭": 6, "船舶": 5})
    cloud = WordCloud(width=400, height=200, prefer_horizontal=0.0,
                      random_state=7).generate_from_frequencies(counter)

    vertical = [it for it in cloud.layout_ if it[3] == Image.ROTATE_90]
    assert vertical, "这次排版没产出竖排词, 这条断言就失去意义了"

    svg = render_svg(cloud.layout_, 400, 200)

    assert svg.count("rotate(-90)") == len(vertical)


@pytest.mark.parametrize("orientation", ["ROTATE_90", "ROTATE_270"])
def test_both_rotated_orientations_are_treated_as_vertical(orientation):
    from PIL import Image

    svg = render_svg([(("煤炭", 1.0), 40.0, (10.0, 20.0), getattr(Image, orientation), "#fff")],
                     800, 400)

    assert "rotate(-90)" in svg


def test_xml_special_characters_are_escaped():
    svg = render_svg([(("A&B<C>", 1.0), 30.0, (5.0, 6.0), None, "#fff")], 800, 400)

    assert "A&amp;B&lt;C&gt;" in svg
    assert "A&B<C>" not in svg
    # 颜色里的引号也要挡住, 否则可以逃出属性
    quoted = render_svg([(("x", 1.0), 30.0, (5.0, 6.0), None, '"><script>')], 800, 400)
    assert "<script>" not in quoted


def test_viewbox_matches_the_requested_canvas():
    svg = render_svg(_layout(), 800, 400)

    assert 'viewBox="0 0 800 400"' in svg
    assert 'preserveAspectRatio="xMidYMid meet"' in svg


def test_svg_is_accessible_by_construction():
    """原来那两张 <img> 连 alt 都没有; 矢量版顺手把可访问性补上。"""
    svg = render_svg(_layout(), 800, 400, label="当日涨停属性词云")

    assert 'role="img"' in svg
    assert 'aria-label="当日涨停属性词云"' in svg


def test_no_base64_payload_survives():
    svg = render_svg(_layout(), 800, 400)

    assert "base64" not in svg
    assert "data:image" not in svg


def test_empty_or_broken_layout_returns_nothing_so_callers_can_fall_back():
    assert render_svg([], 800, 400) == ""
    assert render_svg(None, 800, 400) == ""
    # 结构不对的条目跳过, 但好的条目照样渲染
    mixed = [("not-a-tuple",), (("绿电", 1.0), 90.0, (10.0, 20.0), None, "#fff")]
    assert "绿电" in render_svg(mixed, 800, 400)


def test_output_is_orders_of_magnitude_smaller_than_the_bitmap_it_replaces():
    """真实词云约 40 个词; 位图实测 84~117 KB, 矢量应当是几 KB 量级。"""
    from PIL import Image

    layout = [
        ((f"概念词{i:02d}", float(40 - i)), 60.0 - i, (float(i * 9), float(i * 19)),
         None if i % 5 else Image.ROTATE_90, "#e3b341")
        for i in range(40)
    ]

    svg = render_svg(layout, 800, 400)

    assert len(svg.encode("utf-8")) < 12_000, f"SVG 过大: {len(svg)} 字符"


def test_advance_em_counts_full_width_characters_as_one_em():
    assert advance_em("半导体") == pytest.approx(3.0)
    assert advance_em("AI") == pytest.approx(1.1)
    assert advance_em("") == 0.0


def test_output_is_well_formed_xml_so_a_browser_will_parse_it():
    """内联进 HTML 的 SVG 若 XML 不合法, 浏览器会整块丢弃 —— 而且不报错。"""
    from xml.etree import ElementTree

    from PIL import Image

    svg = render_svg([
        (("绿电", 3.0), 60.0, (10.0, 20.0), None, "#e3b341"),
        (("A&B<C>", 2.0), 40.0, (30.0, 40.0), None, "#7ee787"),
        (("半导体", 1.0), 50.0, (60.0, 80.0), Image.ROTATE_90, "#79c0ff"),
    ], 800, 400)

    root = ElementTree.fromstring(svg)

    assert root.tag.endswith("svg")
    texts = [node for node in root.iter() if node.tag.endswith("text")]
    assert len(texts) == 3
    assert [node.text for node in texts] == ["绿电", "A&B<C>", "半导体"]
