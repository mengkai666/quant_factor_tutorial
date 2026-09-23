# -*- coding: utf-8 -*-
"""词云配色与留白的唯一真源。

为什么要单独成模块:
    词云原先在 `主线强度追踪.py` 和 `legacy_tracker.py` 里各写一份, 都用
    `background_color="#161b22"` 配 matplotlib 的调色板:
      · 热门股词云  `colormap="tab10"`  —— 深端 #1f77b4 / #2ca02c / #9467bd / #8c564b
      · 属性词云    **没给 colormap**   —— 直接落到 viridis 的低端 #440154
    这些暗色本来是给**白底**设计的。压在 #161b22 上时, "半导体/船舶/玻璃基板/
    游戏/农业种植/储能/煤炭/涂料/光模块" 这类词基本看不见 —— 一张读不出词的词云
    等于没有词云, 而且它不报错, 只是安静地失去信息。

判据为什么是 WCAG 对比度而不是"看着还行":
    4.5:1 是正文级的公认下限, 可计算、可回归、可在 CI 上拦住下一次"顺手换个
    好看点的调色板"。`contrast_ratio()` 就是那把尺子。

颜色怎么定:
    只收亮度足够的亮色, 且**按词取色**(见 `color_func`) —— 同一个词每次跑批必须
    同一个颜色, 否则同一只票在不同期报告里换色, 纵向对比就断了。
"""
from __future__ import annotations

import zlib

# 主报告/看板卡片的深色底。与 report_logic 的 --bg-color 同源。
DARK_BACKGROUND = "#161b22"

# 正文级对比度下限 (WCAG 2.1 AA)。
MIN_CONTRAST = 4.5

# 全部通过 MIN_CONTRAST 的亮色; 由 tests/test_wordcloud_style.py 逐色校验。
BRIGHT_PALETTE = (
    "#ff7b72",   # 珊瑚红
    "#ffa657",   # 橙
    "#e3b341",   # 琥珀
    "#7ee787",   # 亮绿
    "#56d364",   # 绿
    "#79c0ff",   # 天蓝
    "#a5d6ff",   # 浅蓝
    "#d2a8ff",   # 淡紫
    "#ff9bce",   # 粉
    "#f0f6fc",   # 近白
)


def _channels(color: str) -> tuple[float, float, float]:
    """把 #rgb / #rrggbb 解析成 0..1 的三通道。"""
    text = str(color or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        raise ValueError(f"不是合法的十六进制颜色: {color!r}")
    return tuple(int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _linear(channel: float) -> float:
    """sRGB 分量转线性光。"""
    return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4


def relative_luminance(color: str) -> float:
    """WCAG 相对亮度 (0=黑, 1=白)。"""
    red, green, blue = (_linear(channel) for channel in _channels(color))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(foreground: str, background: str) -> float:
    """两色对比度 (1..21), 与前后顺序无关。"""
    first = relative_luminance(foreground)
    second = relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def color_func(word=None, font_size=None, position=None, orientation=None,
               random_state=None, **kwargs) -> str:
    """WordCloud 的 `color_func`。

    按**词本身**取色而不是按随机数: 同一只票在同一期报告里重跑多次颜色不变,
    不同期报告之间也保持同一颜色, 才能纵向比对。font_size/position/random_state
    一律不参与, 所以它们被有意忽略 —— 签名必须留着, 那是 WordCloud 的调用约定。
    """
    key = str(word or "").encode("utf-8")
    return BRIGHT_PALETTE[zlib.crc32(key) % len(BRIGHT_PALETTE)]


def style_kwargs() -> dict:
    """构造 WordCloud 时必须传的样式参数。

    刻意**不返回 colormap**: 一旦 colormap 与 color_func 同时存在, 上游的取舍
    规则会让暗色漏回来 —— 这个模块存在的全部意义就是不让它漏。
    """
    return {
        "background_color": DARK_BACKGROUND,
        "color_func": color_func,
        # 词贴到画布边缘会被裁掉 ("军工"/"云煤能源" 就这么缺了半边)。
        "margin": 6,
        "prefer_horizontal": 0.9,
    }
