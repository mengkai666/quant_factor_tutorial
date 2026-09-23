# -*- coding: utf-8 -*-
"""词云必须在深色底上可读 (2026-09-12)。

病理: `WordCloud(background_color="#161b22", colormap="tab10")` —— tab10 的深端
(`#1f77b4` / `#2ca02c` / `#9467bd` / `#8c564b`) 和 matplotlib 默认 viridis 的低端
(`#440154`) 本来是给**白底**设计的。压在 `#161b22` 上时, 属性词云里的
"半导体/船舶/玻璃基板/游戏/农业种植/储能/煤炭/涂料/光模块"、热门股词云里的
"乐山电力/金牛化工/华电辽能/闽东电力/云煤能源" 全部接近不可读 ——
一张读不出词的词云等于没有词云。

判据用 WCAG 对比度而不是"看着还行": 正文级 4.5:1 是公认下限, 可计算、可回归。
"""
import sys
import types

import pytest


def test_every_palette_color_meets_wcag_aa_against_the_dark_card():
    from wordcloud_style import BRIGHT_PALETTE, DARK_BACKGROUND, contrast_ratio

    assert len(BRIGHT_PALETTE) >= 8, "词云至少要有 8 种颜色才不至于单调"
    bad = [
        (c, round(contrast_ratio(c, DARK_BACKGROUND), 2))
        for c in BRIGHT_PALETTE
        if contrast_ratio(c, DARK_BACKGROUND) < 4.5
    ]
    assert not bad, f"这些颜色在 {DARK_BACKGROUND} 上低于 4.5:1, 读不出来: {bad}"


def test_contrast_ratio_matches_known_reference_values():
    """先把度量本身钉住, 免得后面用一把不准的尺子做验收。"""
    from wordcloud_style import contrast_ratio

    assert contrast_ratio("#ffffff", "#000000") == pytest.approx(21.0, abs=0.05)
    assert contrast_ratio("#000000", "#000000") == pytest.approx(1.0, abs=0.01)
    # 同一颜色对自身必然是 1:1, 且与顺序无关
    assert contrast_ratio("#161b22", "#161b22") == pytest.approx(1.0, abs=0.01)
    assert contrast_ratio("#ff7b72", "#161b22") == pytest.approx(
        contrast_ratio("#161b22", "#ff7b72"), abs=0.001)


def test_color_func_is_deterministic_and_stays_inside_the_palette():
    """同一个词每次上色必须一致 —— 否则每次跑批同一只票换颜色, 没法纵向比。"""
    from wordcloud_style import BRIGHT_PALETTE, color_func

    first = color_func(word="绿电", font_size=40, position=(1, 1), orientation=None)
    second = color_func(word="绿电", font_size=99, position=(300, 200), orientation=None)

    assert first == second
    assert first in BRIGHT_PALETTE
    # 不同词应当能散开, 不能永远同色
    colors = {color_func(word=w, font_size=20, position=(0, 0), orientation=None)
              for w in ["绿电", "煤炭", "半导体", "船舶", "PCB", "AI", "天然气", "玻纤"]}
    assert len(colors) > 1


def test_style_kwargs_forbids_the_dark_end_of_matplotlib_colormaps():
    from wordcloud_style import DARK_BACKGROUND, style_kwargs

    kwargs = style_kwargs()

    assert kwargs["background_color"] == DARK_BACKGROUND
    assert callable(kwargs["color_func"])
    # 留白: 词贴到画布边缘会被裁掉 ("军工"/"云煤能源" 就这么缺了半边)
    assert kwargs["margin"] >= 4
    assert "colormap" not in kwargs, (
        "给了 color_func 就不能再给 colormap; 两者并存时 colormap 的暗色会漏进来"
    )


class _FakeWordCloud:
    """记录构造参数, 不真的画图 (不依赖字体/PNG 编码)。

    `generate_from_frequencies` 会填出与真库同形状的 `layout_`
    ((word, count), font_size, (y, x), orientation, color), 于是能走矢量分支。
    """

    last_kwargs: dict | None = None
    layout_ = None
    width = 800
    height = 400

    def __init__(self, **kwargs):
        _FakeWordCloud.last_kwargs = dict(kwargs)
        self._kwargs = kwargs
        self.layout_ = None

    def generate_from_frequencies(self, frequencies):
        self.layout_ = [
            ((str(word), float(count)), 40.0, (float(i * 7), float(i * 11)), None, "#7ee787")
            for i, (word, count) in enumerate(
                sorted(frequencies.items(), key=lambda item: -item[1]))
        ]
        return self

    def to_image(self):
        from PIL import Image
        return Image.new("RGB", (8, 8), (22, 27, 34))


class _NoLayoutWordCloud(_FakeWordCloud):
    """不产出排版数据 —— 用来验证回退到位图的那条分支。"""

    def generate_from_frequencies(self, frequencies):
        self.layout_ = None
        return self


@pytest.fixture
def fake_wordcloud(monkeypatch):
    module = types.ModuleType("wordcloud")
    module.WordCloud = _FakeWordCloud
    monkeypatch.setitem(sys.modules, "wordcloud", module)
    # 走 Windows 分支: 该分支直接给字体路径、不检查文件是否存在,
    # 于是在没有装 CJK 字体的 CI 上也不会因为字体缺失而跳过本测试。
    monkeypatch.setattr("platform.system", lambda: "Windows")
    _FakeWordCloud.last_kwargs = None
    return _FakeWordCloud


def test_hot_stock_cloud_uses_the_bright_style(fake_wordcloud, monkeypatch, tmp_path):
    import 主线强度追踪 as tracker

    monkeypatch.setattr(tracker, "fetch_cls_top20", lambda: ["甲股", "乙股"])
    monkeypatch.setattr(tracker, "fetch_eastmoney_top20", lambda: ["丙股"])
    monkeypatch.setattr(tracker, "fetch_ths_top20", lambda: [])

    result = tracker.generate_wordclouds([], str(tmp_path))

    assert result.get("hot_stock_svg"), "热门股词云没生成"
    kwargs = fake_wordcloud.last_kwargs
    assert kwargs["background_color"] == "#161b22"
    assert callable(kwargs.get("color_func"))
    assert kwargs.get("margin", 0) >= 4
    assert "colormap" not in kwargs


def test_concept_cloud_uses_the_bright_style(fake_wordcloud, monkeypatch, tmp_path):
    """属性词云原先连 colormap 都没给, 直接落到 viridis 的暗紫端 —— 最容易漏。"""
    import 主线强度追踪 as tracker

    monkeypatch.setattr(tracker, "fetch_cls_top20", lambda: [])
    monkeypatch.setattr(tracker, "fetch_eastmoney_top20", lambda: [])
    monkeypatch.setattr(tracker, "fetch_ths_top20", lambda: [])
    plate_data = [{"secu_name": "绿电", "stock_list": [{"up_tags": ["绿电", "电网设备"]}]}]

    result = tracker.generate_wordclouds(plate_data, str(tmp_path))

    assert result.get("plate_svg"), "属性词云没生成"
    kwargs = fake_wordcloud.last_kwargs
    assert callable(kwargs.get("color_func"))
    assert "colormap" not in kwargs


def test_wordcloud_prefers_vector_and_skips_the_bitmap(fake_wordcloud, monkeypatch, tmp_path):
    """矢量可用时不该再去编码一张 PNG —— 那 100 KB 正是要省掉的东西。"""
    import 主线强度追踪 as tracker

    monkeypatch.setattr(tracker, "fetch_cls_top20", lambda: ["甲股"])
    monkeypatch.setattr(tracker, "fetch_eastmoney_top20", lambda: [])
    monkeypatch.setattr(tracker, "fetch_ths_top20", lambda: [])

    result = tracker.generate_wordclouds([], str(tmp_path))

    assert result["hot_stock_svg"].startswith("<svg")
    assert "甲股" in result["hot_stock_svg"]
    assert result["hot_stock_b64"] == "", "矢量已生成, 不该再产出 base64 位图"


def test_wordcloud_falls_back_to_the_bitmap_when_layout_is_unavailable(monkeypatch, tmp_path):
    """拿不到排版数据时必须回退, 不能让词云整块消失。"""
    import 主线强度追踪 as tracker

    module = types.ModuleType("wordcloud")
    module.WordCloud = _NoLayoutWordCloud
    monkeypatch.setitem(sys.modules, "wordcloud", module)
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr(tracker, "fetch_cls_top20", lambda: ["甲股"])
    monkeypatch.setattr(tracker, "fetch_eastmoney_top20", lambda: [])
    monkeypatch.setattr(tracker, "fetch_ths_top20", lambda: [])

    result = tracker.generate_wordclouds([], str(tmp_path))

    assert result["hot_stock_svg"] == ""
    assert result["hot_stock_b64"].startswith("data:image/png;base64,"), "回退位图没生效"
