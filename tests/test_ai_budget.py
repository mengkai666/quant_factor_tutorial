# -*- coding: utf-8 -*-
"""AI 研判的时间预算与阶段可观测性 (2026-09-12)。

要防的是两件事, 都来自 2026-09-12 的数据获取复盘:

1. **静默**: 2026-08-27 那次跑批在 "板块指数缓存复用" 之后有 73 秒完全没有输出
   —— 复盘确认那就是 AI 研判调用, 而它在**成功时不打印任何日志**。一次成功的
   远端推理于是变成了日志里的一团黑洞。
2. **无界**: `.env` 当前是 AI_PRIMARY_MAX_ATTEMPTS=3 / AI_REQUEST_TIMEOUT=240,
   再加备用模型 1 次 —— 最坏 3×240 + 1×240 = 960s (16 分钟) 阻塞在报告主链路上,
   而 AI 只是增强项。

因此: 给一次调用一个**墙钟总预算**, 并在阶段边界**无论成败都打印一行**含耗时的状态。
"""
import pytest


import json


def _fake_response(status_code, payload=None, text="boom"):
    """最小响应替身。

    注意不要写成 `class Response: status_code = status_code` —— 类体里的 LOAD_NAME
    看不到外层函数的局部变量, 那样会直接 NameError。
    """
    response = type("Response", (), {})()
    response.status_code = status_code
    response.text = text
    response.json = lambda: payload if payload is not None else {}
    return response


@pytest.fixture
def fake_clock(monkeypatch):
    """可推进的墙钟: 每次 requests.post 消耗固定秒数。"""
    import ai_rebound

    state = {"now": 1000.0, "cost": 100.0}

    monkeypatch.setattr(ai_rebound, "_clock", lambda: state["now"])

    def advance():
        state["now"] += state["cost"]

    state["advance"] = advance
    return state


def _arm(monkeypatch, tmp_path, fake_clock, *, attempts=5, budget=250.0,
         status=503, fallback=""):
    import ai_rebound

    monkeypatch.setattr(ai_rebound, "AI_OUTPUT_CACHE_DIR", tmp_path / "ai-output-cache")
    monkeypatch.setattr(ai_rebound, "AI_PRIMARY_MAX_ATTEMPTS", attempts)
    monkeypatch.setattr(ai_rebound, "AI_TOTAL_BUDGET", budget)
    monkeypatch.setattr(ai_rebound, "ANTHROPIC_FALLBACK_MODEL", fallback)
    monkeypatch.setattr(ai_rebound, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai_rebound.time, "sleep", lambda *_: None)

    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs)
        fake_clock["advance"]()
        return _fake_response(status)

    monkeypatch.setattr(ai_rebound.requests, "post", post)
    return calls


def test_ai_total_budget_stops_before_next_attempt(tmp_path, monkeypatch, fake_clock):
    """预算用尽后不得再发请求: 5 次重试 × 100s 的假时钟在 250s 预算下只能发出 3 次。"""
    import ai_rebound
    from report_logic import ReportPolicy

    calls = _arm(monkeypatch, tmp_path, fake_clock, attempts=5, budget=250.0)

    result = ai_rebound.run_guarded_ai(
        {"breadth": 0.6}, ReportPolicy.from_mode("observation"),
    )

    assert len(calls) == 3, "预算耗尽后仍发出了请求"
    assert result["lineage"]["budget_exhausted"] is True
    assert result["lineage"]["elapsed_seconds"] == pytest.approx(300.0)
    assert "预算" in result["reason"]


def test_ai_per_request_timeout_is_capped_by_remaining_budget(tmp_path, monkeypatch, fake_clock):
    """单请求 timeout 必须被剩余预算夹住, 否则一次卡住的连接就能吃掉整个预算。"""
    import ai_rebound
    from report_logic import ReportPolicy

    calls = _arm(monkeypatch, tmp_path, fake_clock, attempts=5, budget=250.0)
    # 单请求超时故意设得比总预算还大, 才能看出"被剩余预算夹住"这件事真的生效。
    # (若设为 240 < 250, min() 取 240, 断言就退化成在验证旧行为。)
    monkeypatch.setattr(ai_rebound, "AI_REQUEST_TIMEOUT", 900.0)

    ai_rebound.run_guarded_ai(
        {"breadth": 0.6}, ReportPolicy.from_mode("observation"),
    )

    timeouts = [call["timeout"] for call in calls]
    assert timeouts, "没有发出任何请求"
    assert all(value <= 250.0 for value in timeouts), timeouts
    assert timeouts[0] == pytest.approx(250.0)


def test_ai_stage_always_logs_elapsed_even_on_success(tmp_path, monkeypatch, fake_clock, capsys):
    """成功路径同样必须留下带耗时的一行 —— 静默的 73 秒就是这么来的。"""
    import ai_rebound
    from report_logic import ReportPolicy

    monkeypatch.setattr(ai_rebound, "AI_OUTPUT_CACHE_DIR", tmp_path / "ai-output-cache")
    monkeypatch.setattr(ai_rebound, "AI_TOTAL_BUDGET", 250.0)
    monkeypatch.setattr(ai_rebound, "ANTHROPIC_FALLBACK_MODEL", "")
    monkeypatch.setattr(ai_rebound, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai_rebound.time, "sleep", lambda *_: None)

    payload_body = {
        "market_summary": "多头占优",
        "observations": ["广度回升"],
        "conditions": [],
        "risks": [],
        "decision": "",
    }

    def post(*args, **kwargs):
        fake_clock["advance"]()
        return _fake_response(
            200, payload={"content": [{"type": "text", "text": json.dumps(payload_body)}]},
        )

    monkeypatch.setattr(ai_rebound.requests, "post", post)

    result = ai_rebound.run_guarded_ai(
        {"breadth": 0.6}, ReportPolicy.from_mode("observation"),
    )

    assert result["status"] in {"ok", "sanitized"}
    assert result["lineage"]["elapsed_seconds"] == pytest.approx(100.0)
    out = capsys.readouterr().out
    assert "AI 研判" in out and "耗时" in out


def test_ai_stage_logs_skipped_without_calling(tmp_path, monkeypatch, fake_clock, capsys):
    """策略禁止 AI 时也要有一行 —— 否则"没跑"和"跑了但没输出"在日志里无法区分。"""
    import ai_rebound
    from report_logic import ReportPolicy

    calls = _arm(monkeypatch, tmp_path, fake_clock)

    result = ai_rebound.run_guarded_ai(
        {"breadth": 0.6}, ReportPolicy.from_mode("facts_only"),
    )

    assert result["status"] == "skipped"
    assert not calls
    out = capsys.readouterr().out
    assert "AI 研判" in out


def test_ai_budget_default_leaves_room_inside_global_timeout():
    """默认预算必须远小于价格抓取的 GLOBAL_TIMEOUT(300s), 不然 AI 能挤掉行情。"""
    import ai_rebound

    assert 0 < ai_rebound.AI_TOTAL_BUDGET <= 300
