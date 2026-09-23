"""Portable, synthetic contract tests for the pure research brief view."""

from copy import deepcopy
from html import escape
from html.parser import HTMLParser
import re

import pytest

from research_brief_view import render_research_brief
from xml.etree.ElementTree import Element, SubElement


DAYS = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07"]


def make_brief():
    sectors = []
    for rank, name in enumerate(["AI应用", "AI算力", "军工航天", "周期资源", "机器人"], 1):
        stocks = [dict(
            code=f"sz{rank * 10 + index:06d}", name=f"观察股{rank}{index}",
            sector=name, sub_sector=f"{name}设备", current_height=4 - index,
            source_date=DAYS[-1], amount=123450000.0, turnover_rate=None,
            first_limit_time="093000", is_st=False, close=12.34,
            pct_5d=[6.78, -2.5, 0.0][index - 1], open=None, high=None,
            low=None, board_type=None, reason=f"排序理由{rank}{index}",
            risk=f"分歧风险{rank}{index}", watch=f"价格观察{rank}{index}",
            cancel=f"取消观察{rank}{index}",
        ) for index in range(1, 4)]
        sectors.append(dict(
            name=name, rank=rank, limit_up_count=4, streak_count=3,
            max_height=4, share_pct=20.0, previous_limit_up_count=2,
            change=2, stocks=stocks,
        ))
    recent = deepcopy(sectors[0]["stocks"][0])
    recent.update(
        code="sz000999", name="*ST观察", is_st=True, current_height=0,
        peak_height=4, limit_up_count=2, last_limit_date=DAYS[3],
        state="今日未涨停", trajectory=[
            dict(date=day, limit_up=up, height=height)
            for day, up, height in zip(DAYS, [None, True, False, True, False],
                                      [None, 4, None, 1, None])
        ],
    )
    return dict(
        schema_version="research-brief/v1", purpose="research_observation",
        report_date=DAYS[-1], target_trade_date="2026-09-08", title="多板块盘后观察",
        market=dict(limit_up=20, limit_down=2, max_height=4, breadth_ratio=0.6,
                    narrative=["上涨占比60.0%，比较板块扩散。", "题材聚集度并非资金净流入。"]),
        sectors=sectors,
        recent=dict(window_days=DAYS[:], coverage_days=DAYS[1:], complete=False,
                    total_count=1, stocks=[recent]),
        next_session=[dict(title="板块扩散", detail="比较三只观察股的收盘价承接。")],
        provenance=dict(population_scope="closing_limit_pool", authoritative_count=20,
                        priced_count=20, source="authoritative_snapshot", ranking_basis="观察排序"),
    )


class Document(HTMLParser):
    """Parse the rendered HTML, retaining text, attributes and table boundaries."""

    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = Element("document")
        self.stack = [self.root]
        self.feed(source)
        self.close()
        assert len(self.stack) == 1, "unclosed HTML elements"

    def handle_starttag(self, tag, attrs):
        node = SubElement(self.stack[-1], tag, dict(attrs))
        if tag not in {"meta", "link", "br", "hr", "img", "input"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        assert self.stack[-1].tag == tag, f"unbalanced HTML: {tag}"
        self.stack.pop()

    def handle_data(self, data):
        node = self.stack[-1]
        if len(node):
            node[-1].tail = (node[-1].tail or "") + data
        else:
            node.text = (node.text or "") + data


def text(node):
    assert node is not None, "expected report element is missing"
    return " ".join("".join(node.itertext()).split())


def test_standalone_report_has_identity_dates_and_section_order():
    html = render_research_brief(make_brief())
    doc = Document(html).root
    assert html.startswith("<!DOCTYPE html>")
    assert doc.find("html").get("lang") == "zh-CN"
    assert text(doc.find(".//title")) == "多板块盘后观察"
    assert text(doc.find(".//h1")) == "多板块盘后观察"
    assert doc.find(".//*[@data-purpose]").get("data-purpose") == "research_observation"
    assert [text(node) for node in doc.findall(".//h2")] == [
        "市场事实与判断", "多板块三股", "近期多板轨迹", "次日观察",
    ]
    dates = {node.get("datetime") for node in doc.iter("time")}
    assert {"2026-09-07", "2026-09-08"} <= dates
    assert doc.find(".//meta[@charset='utf-8']") is not None
    assert doc.find(".//meta[@name='viewport']") is not None


def definitions(node):
    assert node is not None, "expected report section or stock row is missing"
    return {text(item.find("dt")): text(item.find("dd"))
            for item in node.findall(".//dl/div")}


def recent_cells(doc):
    table = doc.find(".//*[@id='rb-recent']//table")
    assert table is not None, "recent trajectory table is missing"
    headers = [text(node) for node in table.findall("thead/tr/th")]
    return dict(zip(headers, [text(node) for node in table.find("tbody/tr")]))


def test_market_facts_and_narrative_use_the_supplied_values():
    brief = make_brief()
    market = Document(render_research_brief(brief)).root.find(".//*[@id='rb-market']")
    assert definitions(market) == {
        "涨停家数": "20只", "跌停家数": "2只", "最高连板": "4板", "上涨占比": "60.0%",
    }
    for narrative in brief["market"]["narrative"]:
        assert narrative in text(market)


def test_five_sectors_keep_three_rows_each_and_the_model_order():
    brief = make_brief()
    doc = Document(render_research_brief(brief)).root
    panels = [node for node in doc.iter() if "data-sector" in node.attrib
              and "data-code" not in node.attrib]
    assert [node.get("data-sector") for node in panels] == [
        "AI应用", "AI算力", "军工航天", "周期资源", "机器人",
    ]
    for panel, sector in zip(panels, brief["sectors"]):
        rows = panel.findall(".//*[@data-code]")
        assert len(rows) == 3
        assert [row.get("data-code") for row in rows] == [s["code"] for s in sector["stocks"]]
        assert all(row.get("data-sector") == sector["name"] for row in rows)
        assert str(sector["rank"]) in text(panel.find("header"))
    assert len({node.get("data-code") for node in doc.findall(".//*[@data-code]")}) == 15


def test_stock_rows_show_actual_prices_amounts_and_all_four_notes():
    brief = make_brief()
    rows = Document(render_research_brief(brief)).root.findall(".//*[@data-code]")
    assert len(rows) == 15
    for row, stock in zip(rows, [s for sector in brief["sectors"] for s in sector["stocks"]]):
        values = definitions(row)
        assert stock["name"] in text(row) and stock["code"] in text(row)
        assert values["收盘"] == "12.34元"
        assert values["成交额"] == "1.23亿元"
        assert values["排序理由"] == stock["reason"]
        assert values["风险"] == stock["risk"]
        assert values["观察"] == stock["watch"]
        assert values["取消观察"] == stock["cancel"]
    assert [definitions(row)["近5日涨幅"] for row in rows[:3]] == ["+6.78%", "-2.50%", "0.00%"]


def test_missing_optional_figures_are_omitted_but_zero_is_preserved():
    brief = make_brief()
    brief["market"]["breadth_ratio"] = None
    stock = brief["sectors"][0]["stocks"][0]
    stock.update(pct_5d=None, amount=None, turnover_rate=None)
    doc = Document(render_research_brief(brief)).root
    row = doc.find(".//*[@data-code]")
    assert "近5日涨幅" not in definitions(row)
    assert "成交额" not in definitions(row)
    assert "上涨占比" not in definitions(doc.find(".//*[@id='rb-market']"))
    assert "收盘" in definitions(row)
    assert not any(word in text(row) for word in ("未知", "暂无", "None", "nan"))
    stock.update(pct_5d=0.0, amount=0.0)
    brief["market"]["breadth_ratio"] = 0.0
    doc = Document(render_research_brief(brief)).root
    assert definitions(doc.find(".//*[@data-code]"))["成交额"] == "0.00元"
    assert definitions(doc.find(".//*[@data-code]"))["近5日涨幅"] == "0.00%"
    assert definitions(doc.find(".//*[@id='rb-market']"))["上涨占比"] == "0.0%"


def test_recent_table_is_independent_of_the_sector_watchlist():
    brief = make_brief()
    duplicate = deepcopy(brief["recent"]["stocks"][0])
    duplicate.update(code="sz000011", name="观察股11", is_st=False)
    brief["recent"]["stocks"].append(duplicate)
    brief["recent"]["total_count"] = 2
    doc = Document(render_research_brief(brief)).root
    assert len(doc.findall(".//*[@data-code]")) == 15
    assert [row.get("data-recent-code") for row in doc.findall(".//tr[@data-recent-code]")] == [
        "sz000999", "sz000011",
    ]


def test_trajectory_aligns_dates_and_distinguishes_true_false_and_none():
    brief = make_brief()
    brief["recent"]["stocks"][0]["trajectory"].reverse()
    doc = Document(render_research_brief(brief)).root
    cells = recent_cells(doc)
    assert [cells[day] for day in DAYS] == [
        "未覆盖", "涨停 · 4板", "未涨停", "涨停 · 1板", "未涨停",
    ]
    assert cells["当前连板"] == "0板"
    assert cells["窗口最高板"] == "4板"
    assert cells["窗口涨停次数"] == "2次"
    assert cells["最近涨停日"] == "2026-09-04"
    row = doc.find(".//tr[@data-recent-code]")
    assert "今日未涨停" in text(row)
    assert not any(word in text(row) for word in ("亏损", "跌停"))
    assert row.find(".//*[@aria-label='ST风险标记']") is not None


def test_missing_trajectory_day_or_height_does_not_invent_a_result():
    brief = make_brief()
    stock = brief["recent"]["stocks"][0]
    stock["trajectory"] = stock["trajectory"][:-1]
    stock["trajectory"][1]["height"] = None
    stock["current_height"] = None
    stock["state"] = "窗口内有涨停记录"
    cells = recent_cells(Document(render_research_brief(brief)).root)
    assert cells[DAYS[-1]] == "未覆盖"
    assert cells[DAYS[1]] == "涨停"
    assert cells["当前连板"] == "—"


def test_partial_coverage_uses_one_short_caption_not_a_diagnostic_panel():
    brief = make_brief()
    doc = Document(render_research_brief(brief)).root
    captions = doc.findall(".//*[@id='rb-recent']//caption")
    assert len(captions) == 1
    assert "4/5" in text(captions[0])
    assert len(text(captions[0])) < 150
    brief["recent"]["complete"] = True
    brief["recent"]["coverage_days"] = DAYS[:]
    caption = Document(render_research_brief(brief)).root.find(".//*[@id='rb-recent']//caption")
    assert "4/5" not in text(caption) and "已覆盖" not in text(caption)


def test_empty_recent_list_does_not_invent_stock_rows():
    brief = make_brief()
    brief["recent"].update(stocks=[], total_count=0)
    doc = Document(render_research_brief(brief)).root
    recent = doc.find(".//*[@id='rb-recent']")
    assert recent is not None
    assert not recent.findall(".//tr[@data-recent-code]")
    assert len(text(recent)) < 150


def test_next_session_notes_are_present_without_private_or_permission_panels():
    brief = make_brief()
    secret = "PRIVATE-CONTEXT-DO-NOT-RENDER"
    brief["_private_context"] = secret
    brief["provenance"]["source"] = secret
    brief["sectors"][0]["stocks"][0]["_internal"] = secret
    html = render_research_brief(brief)
    notes = Document(html).root.find(".//*[@id='rb-next']")
    assert "板块扩散" in text(notes)
    assert "比较三只观察股的收盘价承接。" in text(notes)
    assert secret not in html
    assert not any(word in html for word in ("资格审批", "AI异常", "缺验证报告", "交易许可", "plan_permitted"))


@pytest.mark.parametrize("embedded", [False, True])
def test_accessible_tables_and_internal_anchors(embedded):
    doc = Document(render_research_brief(make_brief(), embedded=embedded)).root
    ids = [node.get("id") for node in doc.iter() if node.get("id")]
    assert len(ids) == len(set(ids))
    links = doc.findall(".//nav/a")
    assert len(links) == 4
    for link in links:
        assert link.get("href").startswith("#")
        assert link.get("href")[1:] in ids
        assert text(link)
    for table in doc.iter("table"):
        assert table.find("caption") is not None
        headers = table.findall("thead/tr/th")
        assert len(headers) == 12
        assert all(header.get("scope") == "col" for header in headers)
        for row in table.findall("tbody/tr"):
            assert row.find("th").get("scope") == "row"
            assert len(row) == len(headers)


def test_embedded_output_is_a_scoped_self_contained_responsive_section():
    html = render_research_brief(make_brief(), embedded=True)
    doc = Document(html).root
    assert len(doc) == 1 and doc[0].tag == "section"
    assert "research-brief" in doc[0].get("class").split()
    assert not any(doc.find(".//" + tag) is not None for tag in ("html", "head", "body", "script", "link", "img", "iframe"))
    assert "<!DOCTYPE" not in html
    style = doc[0].find("style")
    assert style is not None
    css = style.text
    assert "@media print" in css and "max-width:" in css and ":focus-visible" in css
    assert all(color in css for color in ("#F5F7FB", "#FFFFFF", "#16263B", "#087E8B", "#B74248", "#277B62", "#DCE3ED"))
    assert "Microsoft YaHei" in css and "Segoe UI" in css and "Consolas" in css
    assert not any(token in css for token in ("@import", "url(", "@font-face"))
    for selector in re.findall(r"([^{}]+)\{", css):
        if selector.strip().startswith("@media"):
            continue
        assert all(part.strip().startswith(".research-brief") for part in selector.split(","))


@pytest.mark.parametrize("path", [
    "title", "purpose", "report_date", "target_trade_date", "market.narrative.0",
    "sectors.0.name", "sectors.0.stocks.0.code", "sectors.0.stocks.0.name",
    "sectors.0.stocks.0.sub_sector", "sectors.0.stocks.0.reason",
    "sectors.0.stocks.0.risk", "sectors.0.stocks.0.watch", "sectors.0.stocks.0.cancel",
    "recent.window_days.0", "recent.stocks.0.code", "recent.stocks.0.name",
    "recent.stocks.0.sector", "recent.stocks.0.state", "recent.stocks.0.last_limit_date",
    "next_session.0.title", "next_session.0.detail",
])
def test_every_displayed_dynamic_string_is_html_escaped(path):
    brief = make_brief()
    payload = '\"><script>alert("x")</script>&\'<img src=x onerror=alert(1)>'
    parts = [int(part) if part.isdigit() else part for part in path.split(".")]
    target = brief
    for key in parts[:-1]:
        target = target[key]
    target[parts[-1]] = payload
    html = render_research_brief(brief)
    assert escape(payload, quote=True) in html
    assert payload not in html
    doc = Document(html).root
    assert doc.find(".//script") is None and doc.find(".//img") is None
    assert all(not key.lower().startswith("on") for node in doc.iter() for key in node.attrib)


def test_rendering_is_deterministic_does_not_mutate_and_needs_no_io(monkeypatch):
    import builtins
    import io
    import socket

    brief = make_brief()
    before = deepcopy(brief)

    def forbidden(*args, **kwargs):
        raise AssertionError("a pure renderer must not perform file or network I/O")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", forbidden)
        patch.setattr(io, "open", forbidden)
        patch.setattr(socket, "socket", forbidden)
        first = render_research_brief(brief)
        second = render_research_brief(brief)
    assert first == second and "多板块盘后观察" in first
    assert brief == before


@pytest.mark.parametrize("embedded", [False, True])
def test_all_57_recent_rows_keep_20_visible_and_37_in_closed_details(embedded):
    brief = make_brief()
    sample = brief["recent"]["stocks"][0]
    stocks = []
    for index in range(57):
        stock = deepcopy(sample)
        stock.update(code=f"sz{100 + index:06d}", name=f"近期样本{index + 1}",
                     close=20.01 + index, is_st=False)
        stocks.append(stock)
    brief["recent"].update(stocks=stocks[:20], more_stocks=stocks[20:], total_count=57)
    before = deepcopy(brief)
    doc = Document(render_research_brief(brief, embedded=embedded)).root
    recent = doc.find(".//*[@id='rb-recent']")
    details = recent.find("details")
    assert details is not None, "additional recent rows need a native disclosure"
    assert "open" not in details.attrib
    assert text(recent.find("p")) == "共57只 · 默认展示20只"
    assert text(details.find("summary")) == "查看其余37只（展开后共展示57只）"
    primary = recent.find("div/table")
    extra = details.find(".//table")
    assert primary is not None and extra is not None
    assert len(primary.findall("tbody/tr")) == 20
    assert len(extra.findall("tbody/tr")) == 37
    codes = [row.get("data-recent-code") for row in recent.findall(".//tbody/tr")]
    assert codes == [f"sz{100 + index:06d}" for index in range(57)]
    assert len(set(codes)) == 57
    for table, first_close, last_close in ((primary, "20.01元", "39.01元"),
                                            (extra, "40.01元", "76.01元")):
        headers = [text(cell) for cell in table.findall("thead/tr/th")]
        assert headers.count("当日收盘") == 1
        close_index = headers.index("当日收盘")
        rows = table.findall("tbody/tr")
        assert text(rows[0][close_index]) == first_close
        assert text(rows[-1][close_index]) == last_close
        assert table.find("caption") is not None
        assert all(header.get("scope") == "col" for header in table.findall("thead/tr/th"))
        assert all(len(row) == len(headers) and row[0].get("scope") == "row" for row in rows)
    ids = [node.get("id") for node in doc.iter() if node.get("id")]
    assert len(ids) == len(set(ids))
    assert [text(heading) for heading in recent.iter("h2")] == ["近期多板轨迹"]
    assert text(primary.find("caption")) != text(extra.find("caption"))
    assert brief == before


@pytest.mark.parametrize("field", ["code", "name", "sector", "state", "last_limit_date"])
def test_additional_recent_rows_escape_dynamic_text_and_attributes(field):
    brief = make_brief()
    extra = deepcopy(brief["recent"]["stocks"][0])
    extra.update(code="sz000998", close=31.23)
    payload = '"><img src=x onerror=alert(1)>&<script>extra</script>'
    extra[field] = payload
    brief["recent"].update(more_stocks=[extra], total_count=2)
    html = render_research_brief(brief)
    doc = Document(html).root
    row = doc.find(".//details//tr[@data-recent-code]")
    assert row is not None, "extra row must not be discarded"
    assert escape(payload, quote=True) in html and payload not in html
    assert row.get("data-recent-code") == extra["code"]
    assert "31.23元" in text(row)
    assert doc.find(".//script") is None and doc.find(".//img") is None
    assert all(not key.lower().startswith("on") for node in doc.iter() for key in node.attrib)


@pytest.mark.parametrize("has_empty_more_list", [False, True])
def test_legacy_recent_rows_stay_visible_without_an_empty_disclosure(has_empty_more_list):
    brief = make_brief()
    brief["recent"]["total_count"] = 7  # Legacy models may supply only a displayed subset.
    if has_empty_more_list:
        brief["recent"]["more_stocks"] = []
    doc = Document(render_research_brief(brief)).root
    recent = doc.find(".//*[@id='rb-recent']")
    assert recent.find("details") is None
    assert len(recent.findall(".//table")) == 1
    assert [row.get("data-recent-code") for row in recent.findall(".//tbody/tr")] == ["sz000999"]
    assert text(recent.find("p")) == "共7只 · 默认展示1只"
    cells = recent_cells(doc)
    assert cells["当日收盘"] == "12.34元"
    assert [cells[day] for day in DAYS] == ["未覆盖", "涨停 · 4板", "未涨停", "涨停 · 1板", "未涨停"]


def test_first_board_is_not_labeled_as_a_multi_day_streak():
    brief=make_brief()
    html=render_research_brief(brief)
    doc=Document(html).root
    first=doc.find(".//li[@data-code='sz000013']")
    second=doc.find(".//li[@data-code='sz000012']")
    assert text(first.find(".//span[@class='rb-height']"))=="首板"
    assert text(second.find(".//span[@class='rb-height']"))=="当前2连板"
    assert "题材：" in text(first.find(".//p[@class='rb-subsector']"))


def test_empty_recent_window_does_not_turn_missing_history_into_a_negative_result():
    brief=make_brief()
    brief['recent'].update(stocks=[],more_stocks=[],total_count=0,complete=False,coverage_days=[DAYS[-1]])
    html=render_research_brief(brief)
    assert "暂无多板个股" not in html
    assert "1/5" in html
    brief['recent'].update(complete=True,coverage_days=DAYS)
    complete=render_research_brief(brief)
    assert "本次未列出近期多板观察股" in complete
