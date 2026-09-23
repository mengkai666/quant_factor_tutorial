# -*- coding: utf-8 -*-
"""把词云的排版结果画成**内联 SVG**, 取代 base64 位图。

为什么值得换:
    报告是**邮件附件**, 体积直接等于收件人的等待。实测两张 800×400 的 PNG 占
    268 KB(全文 24.5%), 而 gzip 后的 295 KB 里约 70% 就是这两张图 ——
    去掉它们 gzip 只剩 89.8 KB(3.3×)。位图还带三个附带毛病: 被拉伸会糊、
    词不可选中也不可搜索、颜色字号焊死在像素里(换主题得重画)。

怎么做到不改变观感:
    排版仍然交给 `wordcloud` 库 —— 它的 `layout_` 里就是每个词的字号/位置/朝向/颜色。
    这里只把**同一份排版**画成矢量而不是像素, 所以位置、大小、配色与原来的 PNG 一致,
    差别只在渲染方式。

坐标系(这块最容易踩):
    · `layout_` 的位置是 **(y, x)**, 不是 (x, y) —— 见 wordcloud `to_image()` 里的
      `pos = (position[1]*scale, position[0]*scale)`。
    · PIL 的 `draw.text` 以**左上角**为锚点; SVG 的 `<text>` 默认以**基线**为锚。
      所以这里用 `dominant-baseline="central"` 把文字对齐到排版盒子的竖直中点,
      而不是把 y 直接当基线用 —— 后者会让每个词整体上移约半个字高。
    · 竖排(orientation=90)时 PIL 的 `TransposedFont` 让文字自下而上; SVG 侧对应
      `rotate(-90)`, 并把锚点放到竖条的**底部中点**。

字体为什么用字体栈而不是生成端的字体文件:
    位图必须把字形烤进像素, 所以生成端需要 `msyh.ttc` 之类的 CJK 字体路径(缺失就整段
    失败)。矢量只存文字, 由**读者**的浏览器选字体 —— 用字体栈覆盖 Win/macOS/Linux
    三家的常见 CJK 字体, 于是生成端不再依赖字体文件, 文字也随读者系统正常渲染。
"""
from __future__ import annotations

import unicodedata
from html import escape

# 读者侧字体栈: Windows / macOS / Linux 常见 CJK 字体, 最后落到无衬线兜底。
# ⚠️ 必须用**单引号**: 这一段会被放进 `style="..."` 属性里, 里面的双引号会把属性提前
#    截断(HTML 会静默解析坏, XML 直接不合法)。踩过一次, 由 XML 合法性用例逮到。
FONT_STACK = ("'Microsoft YaHei', 'PingFang SC', 'Hiragino Sans GB', "
              "'Noto Sans CJK SC', 'Source Han Sans SC', 'WenQuanYi Zen Hei', "
              "sans-serif")

# 非全角字符的宽度近似值 (em)。全角/宽字符按 1 em 计。
_NARROW_EM = 0.55

# ⚠️ 竖排判据必须用 **PIL 的转置常量**, 不是"90 度"这个数字:
#     Image.ROTATE_90 == 2, Image.ROTATE_270 == 4 —— 拿 90/270 去比会**全部漏判**,
#     竖排词会被当成横排画成一条水平线, 而且不报错。
#     (这里踩过一次: 合成用例里写了 orientation=90 于是测试全绿, 真实 layout_ 是 2。)
try:
    from PIL import Image as _PILImage

    _VERTICAL_ORIENTATIONS = frozenset({int(_PILImage.ROTATE_90), int(_PILImage.ROTATE_270)})
except Exception:                                     # pragma: no cover - 兜底: Pillow 缺失时
    _VERTICAL_ORIENTATIONS = frozenset({2, 4})        # ROTATE_90 / ROTATE_270


def _is_vertical(orientation) -> bool:
    """判断该条排版是不是竖排。

    同时接受度数写法(90/270): wordcloud 只产出 PIL 常量, 但这两个数字是这件事最自然的
    说法, 顺手认下来不会误判 —— 90/270 在 PIL 常量里也不是横排语义。
    """
    if orientation is None:
        return False
    try:
        value = int(orientation)
    except (TypeError, ValueError):
        return False
    return value in _VERTICAL_ORIENTATIONS or value in (90, 270)


def advance_em(word: str) -> float:
    """估算词宽(单位 em)。

    只用于给竖排文字定位、以及给测试算包围盒, 不参与横排的绘制 ——
    横排由 SVG 自己排版, 不需要我们猜宽度。
    """
    total = 0.0
    for char in str(word):
        wide = unicodedata.east_asian_width(char) in ("W", "F")
        total += 1.0 if wide else _NARROW_EM
    return total


def render_svg(layout, width, height, *, font_stack: str = FONT_STACK,
               label: str = "词云") -> str:
    """把 `WordCloud.layout_` 渲染成一段自包含的内联 SVG。

    layout 的每条是 `((word, count), font_size, (y, x), orientation, color)`。
    没有排版数据时返回空串 —— 调用方据此回退到位图。
    """
    items = list(layout or [])
    if not items:
        return ""

    width, height = int(width), int(height)
    parts = []
    for entry in items:
        try:
            (word, _count), font_size, position, orientation, color = entry
        except (TypeError, ValueError):
            continue
        text = str(word)
        if not text:
            continue
        size = float(font_size)
        pos_y, pos_x = float(position[0]), float(position[1])
        fill = escape(str(color), quote=True)
        body = escape(text)

        if _is_vertical(orientation):
            # 竖排: 文字自下而上, 占一条以 pos_x 为水平中心、自 pos_y 向上 text_w 的竖带。
            text_w = advance_em(text) * size
            anchor_x = pos_x + size / 2.0
            anchor_y = pos_y + text_w
            parts.append(
                f'<text transform="translate({anchor_x:.1f} {anchor_y:.1f}) rotate(-90)" '
                f'font-size="{size:.1f}" fill="{fill}" dominant-baseline="central">{body}</text>'
            )
        else:
            parts.append(
                f'<text x="{pos_x:.1f}" y="{pos_y + size / 2.0:.1f}" '
                f'font-size="{size:.1f}" fill="{fill}" dominant-baseline="central">{body}</text>'
            )

    if not parts:
        return ""

    return (
        f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet" '
        f'role="img" aria-label="{escape(label, quote=True)}" '
        f'style="width:100%;height:auto;display:block;font-family:{font_stack}">'
        # 不画背景矩形: 卡片本身就是 DARK_BACKGROUND, 留空能让词云跟随主题。
        + "".join(parts)
        + "</svg>"
    )
