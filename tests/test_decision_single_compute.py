# -*- coding: utf-8 -*-
"""同一份决策在一次渲染里只算一次 (2026-09-12)。

病理: `generate_dashboard_section` 在 3155 行已经算出 `today_decision`, 紧接着 3165 行
调 `_today_three_html` —— 而后者在 812 行**又用同一个 ctx 和同一个 plan 算了一遍**。
`build_today_decision` 里 scoped 模式会丢弃传入的 plan 并从 ctx 重建, 所以两次结果必然
相同: 这是纯重复, 不是"两次不同口径"。

为什么仍然要钉住: 上一轮把 `_target_day_frame` 修好之后单次成本从 5,935ms 降到 277ms,
所以**收益不大**, 但"同一份决策算两次"是那种会随功能增长而悄悄变贵的东西, 而且两次
结果一旦哪天不再相同, 页面就会出现自相矛盾的两套数字。修法是现成的: `build_today_focus_rows`
早就有 `decision=` 参数, 只是 `_today_three_html` 没用。
"""
import pytest


def _decision_ctx():
    from decision_dashboard import build_dashboard_ctx

    ctx = build_dashboard_ctx(
        timing={"scene": "中性震荡", "action": "结构博弈 / 主线为纲", "position": "0 成"},
        advance_decline={"up": 3000, "down": 2000, "zt": 50, "dt": 5},
        report_date="2026-09-10",
    )
    ctx["publication_mode"] = "decision"
    ctx["market_state"] = {"publication_mode": "decision"}
    ctx["mainline_review"] = {"top1": "AI算力", "concentration": 0.41}
    ctx["echelon"] = [
        {"height": "2连板",
         "stock_details": [{"name": "主线候选", "code": "sh600001", "ml": "AI算力"}]},
        {"height": "6连板",
         "stock_details": [{"name": "情绪高标", "code": "sz000002", "ml": "周期资源"}]},
    ]
    return ctx


@pytest.fixture
def counting_decision(monkeypatch):
    import decision_dashboard as dd

    calls = []
    original = dd.build_today_decision

    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(dd, "build_today_decision", counting)
    return calls


def test_dashboard_section_computes_the_decision_exactly_once(counting_decision):
    import decision_dashboard as dd

    html = dd.generate_dashboard_section(_decision_ctx())

    assert "今日只看三件事" in html, "三件事卡没渲染, 这条断言就失去意义了"
    assert len(counting_decision) == 1, (
        f"同一份决策被算了 {len(counting_decision)} 次; "
        "调用方已经算好的 today_decision 应当直接透传给 _today_three_html"
    )


def test_standalone_dashboard_computes_the_decision_exactly_once(counting_decision):
    import decision_dashboard as dd

    html = dd.generate_dashboard_html(_decision_ctx())

    assert "今日只看三件事" in html
    assert len(counting_decision) == 1, f"独立看板把同一份决策算了 {len(counting_decision)} 次"


def test_today_three_html_reuses_a_supplied_decision(monkeypatch):
    """直接给 decision 时, 不允许再触发一次计算。"""
    import decision_dashboard as dd

    def explode(*args, **kwargs):
        raise AssertionError("_today_three_html 不该在拿到 decision 之后重算")

    monkeypatch.setattr(dd, "build_today_decision", explode)
    decision = {
        "watch_items": [{
            "code": "market_gate", "title": "市场开关",
            "headline": "宽度与接力同时止跌", "detail": "上涨占比 17%",
            "check": "明日验证",
        }],
        "readiness": {"action": {"status": "no_new_positions"}},
        "report_date": "2026-09-10",
    }

    html = dd._today_three_html({}, {}, decision=decision)

    assert "市场开关" in html
    assert "宽度与接力同时止跌" in html
    assert "2026-09-10" in html
