# -*- coding: utf-8 -*-
"""报告体积: 不重复内嵌同一份数据, 并给 1MB 的长报告一个页内目录 (2026-09-12)。

病理 A3: 子图脚本先把整份窗口数据写进 `window.SUB_CHART_DATA[chart_id]`, 紧接着又把
**默认窗口的 series 再序列化一遍**塞进初始 `setOption`。同一个数组因此出现两次 ——
实测 1,123,701 字节的报告里有 68.9 KB 是逐字节重复的数组。`lbSwitch` 本来就是从
`SUB_CHART_DATA` 取数, 所以初始渲染完全可以读同一份。

病理 A6: 16 个章节 / 19 张表 / 13 张图 / 1.1MB, 而页内锚点链接 = 0 —— 唯一的 <nav>
只是"多板块观察"子页入口。读者过了首屏就只能一路滚。
"""
import os
import pathlib
import re

import pandas as pd
import pytest


def _minimal_report(monkeypatch, tmp_path, **overrides):
    import 主线强度追踪 as tracker

    # 与 test_report_rendering.py 同一约定: 单测不碰 AI 服务, 也不为等重试付时间。
    monkeypatch.setenv("AI_ENABLE", "0")
    # `generate_html` 会走阶段共振, 而它用的是**模块级**常量
    # `phase_resonance.THS_CACHE = DATA_DIR/ths_sector_hist.json`(import 时就绑定了),
    # 缓存覆盖不到请求日期时会**重拉并写回生产文件**。
    # 处理方式: 把生产缓存**拷一份**到临时目录再指过去 —— 既不写生产文件, 又因为副本
    # 覆盖得住请求日期而走"复用"分支, 不会变成联网重拉。
    import phase_resonance
    if os.path.exists(phase_resonance.THS_CACHE):
        seeded = tmp_path / "ths_sector_hist.json"
        seeded.write_bytes(pathlib.Path(phase_resonance.THS_CACHE).read_bytes())
        monkeypatch.setattr(phase_resonance, "THS_CACHE", str(seeded))
    output = tmp_path / "report.html"
    monkeypatch.setattr(tracker, "OUTPUT_HTML", str(output))
    empty = pd.DataFrame()
    kwargs = dict(
        ml_strength=empty, sub_strength=empty, ml_ma={}, sub_ma={},
        ml_thresh={}, sub_thresh={}, leaders={}, dates=["20260805", "20260806"],
        ratings={}, sub_ratings={}, echelon=[], top30_data={},
        advance_decline={"up": 2500, "down": 2500, "zt": 0, "dt": 0},
        sentiment_df=empty, classified_df=empty, price_df=empty,
    )
    kwargs.update(overrides)
    tracker.generate_html(**kwargs)
    return output.read_text(encoding="utf-8")


def _track_report(monkeypatch, tmp_path):
    """一份带"图榜联动"子图的报告: 一个板块、一个窗口, 曲线值可辨认。"""
    return _minimal_report(
        monkeypatch, tmp_path,
        sub_tracks={"测试板块": {10: [{
            "name": "测试股", "kind": "关联", "resonance": False, "lead": False,
            "curve": {"20260805": 1.5, "20260806": 2.5},
        }]}},
        sub_leaderboard={"测试板块": {10: [
            {"name": "测试股", "ret": 12.3, "kind": "关联", "tags": ["共振"]},
        ]}},
        sub_ratings={"测试板块": ("B", "")},
    )


def test_sub_chart_series_is_embedded_only_once(monkeypatch, tmp_path):
    html = _track_report(monkeypatch, tmp_path)

    assert "sub_0" in html, "子图没渲染, 这条断言就失去意义了"
    # 默认窗口的曲线数组只应存在一份 (存进 SUB_CHART_DATA 供 lbSwitch 复用)。
    assert html.count('"data": [1.5, 2.5]') == 1, (
        "默认窗口的 series 被重复序列化了; 初始 setOption 应当从 "
        "window.SUB_CHART_DATA 取数, 而不是再 dumps 一遍"
    )


def test_sub_chart_still_exposes_its_data_for_window_switching(monkeypatch, tmp_path):
    """去重不能顺手把 lbSwitch 依赖的那份数据也删掉。"""
    html = _track_report(monkeypatch, tmp_path)

    assert "window.SUB_CHART_DATA['sub_0']" in html
    assert "lbSwitch" in html


def test_threshold_lines_are_stored_once_not_once_per_window(monkeypatch, tmp_path):
    """阈值线是**窗口无关**的, 却被逐窗口展开。

    实测: 5 个窗口 × 10 张子图 = 50 份常量数组, 合计 53.8 KB —— 每份都是上百个相同数字
    (10.0 重复 150 次这种)。它是水平参考线, 换窗口不会变, 所以只该存一份。
    """
    def _track(window):
        return {"name": f"股{window}", "kind": "关联", "resonance": False, "lead": False,
                "curve": {"20260805": float(window), "20260806": float(window) + 1}}

    def _row(window):
        return [{"name": f"股{window}", "ret": float(window), "kind": "关联", "tags": []}]

    html = _minimal_report(
        monkeypatch, tmp_path,
        sub_tracks={"测试板块": {3: [_track(3)], 10: [_track(10)]}},
        sub_leaderboard={"测试板块": {3: _row(3), 10: _row(10)}},
        sub_ratings={"测试板块": ("B", "")},
        sub_thresh={"10日阈值": [10.0, 10.0], "30日阈值": [20.0, 20.0]},
    )

    assert "sub_0" in html, "子图没渲染, 这条断言就失去意义了"
    # 两条阈值线各只应序列化一次
    assert html.count('"10日阈值"') == 1, "10日阈值被逐窗口重复内嵌了"
    assert html.count('"30日阈值"') == 1, "30日阈值被逐窗口重复内嵌了"
    assert html.count("[10.0, 10.0]") == 1
    assert html.count("[20.0, 20.0]") == 1
    # 且必须挂在那份全局里, 由两处 setOption 各自 concat 上去
    assert "window.SUB_CHART_THRESH" in html
    assert "_init.series.concat(window.SUB_CHART_THRESH" in html, "初始渲染没接上阈值"
    assert "wd[w].series.concat(window.SUB_CHART_THRESH" in html, "窗口切换没接上阈值"


def test_section_toc_links_every_section_title():
    from research_subpages import add_section_toc

    document = (
        '<html><body><h1>标题</h1>'
        '<h2 class="section-title">甲章节</h2><p>x</p>'
        '<h2 class="section-title">乙章节 <span class="help-icon" data-tip="说明">?</span></h2>'
        '</body></html>'
    )

    out = add_section_toc(document)

    assert 'class="section-toc"' in out
    assert 'href="#sec-1"' in out and 'href="#sec-2"' in out
    assert 'id="sec-1"' in out and 'id="sec-2"' in out
    assert "甲章节" in out
    # 帮助图标的 "?" 不该混进目录条目
    assert "乙章节 ?" not in out


def test_section_toc_is_idempotent():
    from research_subpages import add_section_toc

    document = ('<html><body><h1>T</h1><h2 class="section-title">甲</h2>'
                '<h2 class="section-title">乙</h2></body></html>')

    once = add_section_toc(document)
    twice = add_section_toc(once)

    assert once.count('class="section-toc"') == 1
    assert twice.count('class="section-toc"') == 1
    assert twice.count('id="sec-1"') == 1


def test_section_toc_keeps_existing_anchor_ids():
    from research_subpages import add_section_toc

    document = ('<html><body><h1>T</h1><h2 class="section-title" id="already">甲</h2>'
                '<h2 class="section-title">乙</h2></body></html>')

    out = add_section_toc(document)

    assert 'href="#already"' in out
    assert 'id="already"' in out
    assert 'href="#sec-2"' in out


def test_section_toc_is_skipped_when_there_is_nothing_to_navigate():
    from research_subpages import add_section_toc

    only_one = '<html><body><h1>T</h1><h2 class="section-title">甲</h2></body></html>'
    none_at_all = '<html><body><h1>T</h1><p>x</p></body></html>'

    assert "section-toc" not in add_section_toc(only_one)
    assert "section-toc" not in add_section_toc(none_at_all)


def test_section_toc_survives_a_document_without_body():
    from research_subpages import add_section_toc

    assert add_section_toc("<h2 class='section-title'>甲</h2>") == "<h2 class='section-title'>甲</h2>"


def test_section_toc_does_not_require_the_section_title_class():
    """真实报告最靠前的两个章节用的是裸 `<h2>` 和内联样式, 不是 class="section-title"。

    按 class 选会把"今日决策看板""反弹分类复盘"这两节**漏在目录之外** ——
    而它们恰恰是最该被导航到的两节。这是在真实 1MB 报告上实测发现的。
    """
    from research_subpages import add_section_toc

    document = (
        '<html><body>'
        '<h2>今日决策看板 · D_冰点抄底</h2>'
        '<h2 style="color:#58a6ff;font-size:19px;">🧭 反弹分类复盘</h2>'
        '<h2 class="section-title">🔥 冰火之歌</h2>'
        '</body></html>'
    )

    out = add_section_toc(document)

    assert out.count('href="#sec-') == 3
    assert "今日决策看板" in out.split('class="section-toc"')[1].split("</nav>")[0]
    assert "反弹分类复盘" in out.split('class="section-toc"')[1].split("</nav>")[0]


def test_section_toc_skips_headings_inside_a_collapsible_panel():
    """折叠块里的 h2 是面板小标题, 不是章节 —— 进了目录就会指向一个藏起来的东西。"""
    from research_subpages import add_section_toc

    document = (
        '<html><body>'
        '<h2>甲章节</h2>'
        '<details><summary>x</summary><h2>面板小标题</h2></details>'
        '<h2>乙章节</h2>'
        '</body></html>'
    )

    out = add_section_toc(document)
    toc = out.split('class="section-toc"')[1].split("</nav>")[0]

    assert "甲章节" in toc and "乙章节" in toc
    assert "面板小标题" not in toc
    assert out.count('href="#sec-') == 2


def test_real_report_ships_a_working_table_of_contents(monkeypatch, tmp_path):
    html = _minimal_report(monkeypatch, tmp_path)

    assert 'class="section-toc"' in html, "真实报告没有目录"

    anchors = re.findall(r'class="section-toc".*?</nav>', html, re.S)
    assert anchors, "目录块没找到"
    targets = re.findall(r'href="#([^"]+)"', anchors[0])
    assert len(targets) >= 2, f"目录里只有 {len(targets)} 个条目"

    ids = set(re.findall(r'\bid="([^"]+)"', html))
    missing = [t for t in targets if t not in ids]
    assert not missing, f"目录指向了不存在的锚点: {missing}"


def test_section_toc_survives_the_research_subpage_entry():
    """目录和"多板块观察"入口都要往同一份文档里插东西, 顺序与锚点都得活下来。

    发布时同一份报告会变成三个副本 (output/ / site/latest.html / site/reports/{date}.html),
    后两者的子页入口 href 前缀不同 (`research_briefs/` vs `../research_briefs/`)。
    这里确认插入口不会碰掉 `#sec-N`。
    """
    from research_subpages import add_research_entry, add_section_toc

    document = (
        '<html><body><h1>报告</h1>'
        '<h2 class="section-title">甲章节</h2><p>x</p>'
        '<h2 class="section-title">乙章节</h2>'
        '</body></html>'
    )
    document = add_section_toc(document)

    for href in ("research_briefs/research_brief_2026-09-16.html",
                 "../research_briefs/research_brief_2026-09-16.html"):
        out = add_research_entry(document, href, report_date="2026-09-16")

        toc = out.split('class="section-toc"')[1].split("</nav>")[0]
        ids = set(re.findall(r'\bid="([^"]+)"', out))
        links = re.findall(r'href="#([^"]+)"', toc)
        assert links, "目录没了"
        assert [link for link in links if link not in ids] == [], f"锚点被入口插入弄丢了: {href}"
        assert href in out, "子页入口没插进去"


def test_real_report_carries_a_freshness_note_for_an_old_report(monkeypatch, tmp_path):
    """纯函数测过了还不够 —— 要确认它真的接进了报告头部。"""
    html = _minimal_report(monkeypatch, tmp_path)   # 报告日 2026-08-06, 远早于今天

    assert "本报告数据截止 2026-08-06" in html
    assert "最新报告" in html or "自然日" in html


def test_section_toc_is_built_after_every_other_document_mutation():
    """目录必须**最后**挂锚点 (2026-09-15 正式报告实测踩到)。

    病理: `generate_html` 末尾还有几处整段替换 —— 策略中和器之后的
    `html.replace(marker, trusted_*_html)` 会把标记区域换成模块自带的渲染结果, 而那份
    渲染结果**不带我加的 id**; 年线高度区块自己也有一个 `replace_height_section()`,
    同样是"正则找到 h2、整段换掉"。

    目录若在这些替换之前挂锚点, 落在被换区域里的章节就会丢锚点。实测后果: 2026-09-15 的
    正式报告里 `#sec-5`(连板高度分析) 指向了不存在的锚点 —— 点击没反应, 而且**不报错**。
    旁证: 那条目录项的标题是"…(市场高度)", 而正文里是"…(市场高度) · 最近一年" ——
    两处文本不一致, 正说明标题在挂完锚点之后被整段替换过。

    这条断言用 AST 比对**调用位置**, 因为"谁先谁后"是纯顺序问题, 用输出反推太绕。
    """
    import ast

    source = pathlib.Path("src/主线强度追踪.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    toc_calls, mutators = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        segment = ast.get_source_segment(source, node) or ""
        if segment.startswith("add_section_toc("):
            toc_calls.append(node.lineno)
        elif ".replace(" in segment and "sanitize_marker" in segment:
            mutators.append(("trusted 模块替换", node.lineno))
        elif segment.startswith("add_research_entry("):
            mutators.append(("研究子页入口", node.lineno))

    assert toc_calls, "主线强度追踪.py 里找不到 add_section_toc 调用"
    assert mutators, "找不到 trusted-* 替换, 这条断言就失去意义了"

    first_toc = min(toc_calls)
    later = [f"{name}@{line}" for name, line in mutators if line > first_toc]
    assert not later, (
        f"页内目录在第 {first_toc} 行挂锚点, 但后面还有会整段替换文档的步骤: {later} —— "
        "落在被替换区域里的章节会丢锚点(点击无反应且不报错)。目录必须放在最后。"
    )


def test_report_embeds_the_wordcloud_as_inline_svg(monkeypatch, tmp_path):
    """词云要走矢量: 报告里不该再出现 base64 位图。"""
    svg = ('<svg viewBox="0 0 800 400" role="img" aria-label="热门股票词云">'
           '<text x="10" y="20" font-size="30" fill="#7ee787">甲股</text></svg>')
    html = _minimal_report(monkeypatch, tmp_path, wc_data={
        "hot_stock_svg": svg, "top_stocks": {"cls": ["甲股"], "em": [], "ths": []},
    })

    assert "热门股票词云" in html
    assert 'aria-label="热门股票词云"' in html
    assert "data:image/png;base64" not in html, "还在用 base64 位图"


def test_report_falls_back_to_the_bitmap_wordcloud(monkeypatch, tmp_path):
    """拿不到排版数据时, 位图那条路必须还在。"""
    png = "data:image/png;base64,iVBORw0KGgo="
    html = _minimal_report(monkeypatch, tmp_path, wc_data={
        "hot_stock_b64": png, "top_stocks": {"cls": ["甲股"], "em": [], "ths": []},
    })

    assert "热门股票词云" in html
    assert png in html
