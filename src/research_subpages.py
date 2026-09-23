"""Small, owned navigation blocks for main reports and research subpages."""
from __future__ import annotations

from html import escape, unescape
import os
from pathlib import Path
import re
from urllib.parse import quote, urlsplit


def relative_report_link(target, directory):
    target, directory = Path(target).resolve(), Path(directory).resolve()
    try:
        return quote(Path(os.path.relpath(target, directory)).as_posix(), safe="/")
    except ValueError:
        return target.as_uri()


def _href(value):
    text = str(value or "").strip()
    parsed = urlsplit(text)
    if (not text or parsed.scheme not in {"", "file"} or parsed.netloc
            or text.startswith("//") or "\\" in text or any(ord(c) < 32 for c in text)):
        raise ValueError("报告导航只允许本地文件或相对链接")
    if not parsed.scheme and ":" in parsed.path:
        raise ValueError("报告导航链接格式无效")
    return escape(text, quote=True)


def _strip(document, kind):
    return re.sub(r"<!-- " + kind + r":start -->.*?<!-- " + kind + r":end -->", "", document, flags=re.S)


def remove_research_entry(document):
    return _strip(document, "research-entry")


def _insert(document, kind, block, *, before_heading=False):
    document = _strip(document, kind)
    body = re.search(r"<body\b[^>]*>", document, flags=re.I)
    if body is None:
        raise ValueError("报告缺少body，不能插入导航")
    expression = r"<h1\b[^>]*>" if before_heading else r"</h1\s*>"
    heading = re.search(expression, document[body.end():], flags=re.I)
    position = body.end()
    if heading:
        position += heading.start() if before_heading else heading.end()
    owned = f"<!-- {kind}:start -->{block}<!-- {kind}:end -->"
    return document[:position] + owned + document[position:]


def add_research_entry(document, href, *, report_date=None):
    stamp = f" · {escape(str(report_date))}" if report_date else ""
    block = ('<nav class="research-subpage-entry" aria-label="报告子页面" style="margin:12px 0">'
             f'<a data-research-entry href="{_href(href)}" style="display:inline-block;padding:9px 14px;'
             'border:1px solid #3d596f;border-radius:8px;color:#58a6ff;text-decoration:none;'
             'font-size:14px;font-weight:600;line-height:1.6;font-family:inherit">多板块观察 → '
             f'<span style="font-size:12px;font-weight:400">每板块3股 · 近期多板{stamp}</span></a></nav>')
    return _insert(document, "research-entry", block)


def add_research_parent(document, href):
    block = ('<nav class="research-parent-link" aria-label="返回主报告" style="margin:0 0 14px">'
             f'<a data-research-parent href="{_href(href)}" style="color:#087E8B;font-size:14px;'
             'font-weight:600;text-decoration:none">← 返回主报告</a></nav>')
    return _insert(document, "research-parent", block, before_heading=True)


_SECTION_TAG = re.compile(r"<h2\b[^>]*>")
_EXISTING_ID = re.compile(r'\bid="([^"]+)"')
_DETAILS_OPEN = re.compile(r"<details\b", re.I)
_DETAILS_CLOSE = re.compile(r"</details\s*>", re.I)


def _inside_details(document, position):
    """position 处是否落在某个尚未闭合的 `<details>` 里。

    用"最近的 <details 是否比最近的 </details> 更靠后"来判断, 不维护状态机 ——
    文档里有一个漏闭合的块标签时, 状态机会把后面所有章节都吞掉, 而这个判据不会。
    """
    opened = document.rfind("<details", 0, position)
    if opened < 0:
        return False
    closed = max((m.end() for m in _DETAILS_CLOSE.finditer(document, 0, position)), default=-1)
    return opened > closed


def _section_titles(document):
    """按文档顺序取出章节标题的 (start, end, tag, title)。

    判据是"**任何 <h2>**", 不是 `class="section-title"` —— 真实报告里最靠前的两个章节
    ("今日决策看板"、"反弹分类复盘") 用的是裸 `<h2>` 和内联样式 `<h2 style=...>`,
    按 class 选会把**最重要的两节漏在目录之外**(2026-09-13 在真实 1MB 报告上实测发现)。
    `<details>` 内部的 h2 是面板小标题, 跳过。
    """
    found = []
    for match in _SECTION_TAG.finditer(document):
        if _inside_details(document, match.start()):
            continue
        close = document.find("</h2>", match.end())
        if close < 0:
            continue
        title = _plain_text(document[match.end():close])
        if title:
            found.append((match.start(), match.end(), match.group(0), title))
    return found


def _plain_text(fragment):
    """章节标题的纯文本: 去掉标记, 并丢掉帮助图标留下的那个孤立问号。"""
    text = re.sub(r"<[^>]*>", " ", fragment)
    text = re.sub(r"\s+", " ", unescape(text)).strip()
    return text.rstrip("?").strip()


def _toc_block(links):
    items = "".join(
        f'<a href="#{escape(anchor, quote=True)}" style="color:#58a6ff;text-decoration:none;'
        f'white-space:nowrap">{escape(title)}</a>'
        for anchor, title in links
    )
    return (
        '<nav class="section-toc" aria-label="报告目录" style="margin:0 0 22px;padding:14px 16px;'
        'background:#161b22;border:1px solid #30363d;border-radius:8px;font-size:13px;line-height:1.9">'
        '<div style="color:#8b949e;font-size:11px;font-weight:700;letter-spacing:1.5px;'
        'text-transform:uppercase;margin-bottom:8px">目录</div>'
        f'<div style="display:flex;flex-wrap:wrap;gap:6px 18px">{items}</div></nav>'
    )


def add_section_toc(document):
    """给长报告补一个页内目录 (就地给章节标题挂锚点)。

    为什么要: 主报告 1.1MB / 16 个章节 / 19 张表 / 13 张图, 而页内锚点链接是 0 ——
    读者过了首屏就只能一路滚到底。

    为什么要插在**第一个章节标题之前**: 那里正是正文开始的位置, 不必去猜 header 的
    闭合位置; 只有一个章节时不插 (没有可导航的东西)。
    已有 id 的标题沿用原 id, 不改名 —— 别的地方可能已经指向它。
    """
    document = _strip(document, "section-toc")
    if "<body" not in document.lower():
        return document

    prepared = []
    for start, end, tag, title in _section_titles(document):
        existing = _EXISTING_ID.search(tag)
        anchor = existing.group(1) if existing else f"sec-{len(prepared) + 1}"
        new_tag = tag if existing else f'{tag[:-1]} id="{anchor}">'
        prepared.append((start, end, new_tag, anchor, title))

    if len(prepared) < 2:
        return document

    nav = f"<!-- section-toc:start -->{_toc_block([(a, t) for _, _, _, a, t in prepared])}<!-- section-toc:end -->"

    pieces, cursor = [], 0
    for position, (start, end, new_tag, _anchor, _title) in enumerate(prepared):
        pieces.append(document[cursor:start])
        if position == 0:
            pieces.append(nav)
        pieces.append(new_tag)
        cursor = end
    pieces.append(document[cursor:])
    return "".join(pieces)
