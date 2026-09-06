from publish_site import resolve_generated_report_date


def test_resolve_generated_report_date_accepts_latest_real_trade_date(tmp_path):
    output_html = tmp_path / "主线强度追踪.html"
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    output_html.write_text("report-2026-08-07", encoding="utf-8")
    (reports_dir / "2026-08-07.html").write_text(
        "report-2026-08-07", encoding="utf-8"
    )

    assert resolve_generated_report_date(
        output_html, reports_dir, run_date="2026-08-12"
    ) == "2026-08-07"


def test_resolve_generated_report_date_prefers_today_when_today_report_exists(tmp_path):
    output_html = tmp_path / "主线强度追踪.html"
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    output_html.write_text("same-content", encoding="utf-8")
    (reports_dir / "2026-08-07.html").write_text("same-content", encoding="utf-8")
    (reports_dir / "2026-08-12.html").write_text("same-content", encoding="utf-8")

    assert resolve_generated_report_date(
        output_html, reports_dir, run_date="2026-08-12"
    ) == "2026-08-12"


def test_resolve_generated_report_date_does_not_select_future_report(tmp_path):
    output_html = tmp_path / "主线强度追踪.html"
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    output_html.write_text("report", encoding="utf-8")
    (reports_dir / "2026-08-13.html").write_text("report", encoding="utf-8")

    assert resolve_generated_report_date(
        output_html, reports_dir, run_date="2026-08-12"
    ) is None

def test_resolve_generated_report_date_uses_embedded_date_only_when_archive_matches(tmp_path):
    output_html = tmp_path / "主线强度追踪.html"
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    content = (
        '<!doctype html><head><meta name="report-date" content="2026-08-12">'
        '</head><body>current</body>'
    )
    output_html.write_text(content, encoding="utf-8")
    (reports_dir / "2026-08-07.html").write_text(content, encoding="utf-8")

    assert resolve_generated_report_date(
        output_html, reports_dir, run_date="2026-08-12"
    ) is None

    (reports_dir / "2026-08-12.html").write_text("different", encoding="utf-8")
    assert resolve_generated_report_date(
        output_html, reports_dir, run_date="2026-08-12"
    ) is None

    (reports_dir / "2026-08-12.html").write_text(content, encoding="utf-8")
    assert resolve_generated_report_date(
        output_html, reports_dir, run_date="2026-08-12"
    ) == "2026-08-12"


def test_resolve_generated_report_date_rejects_future_embedded_date(tmp_path):
    output_html = tmp_path / "主线强度追踪.html"
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    content = (
        '<!doctype html><head><meta name="report-date" content="2026-08-13">'
        '</head><body>future</body>'
    )
    output_html.write_text(content, encoding="utf-8")
    (reports_dir / "2026-08-13.html").write_text(content, encoding="utf-8")

    assert resolve_generated_report_date(
        output_html, reports_dir, run_date="2026-08-12"
    ) is None


def test_degraded_quality_never_gets_a_green_complete_badge_even_with_legacy_data_ok():
    from bs4 import BeautifulSoup
    from publish_site import _render_verdict
    html = _render_verdict({
        "data_ok": True, "data_quality": {"status": "degraded"},
        "stance": "数据待核验", "data_note": "炸板字段缺失",
    })
    soup = BeautifulSoup(html, "html.parser")
    assert soup.select_one(".badge.ok") is None
    assert "数据完整" not in soup.get_text()
    assert "数据降级" in soup.get_text()


def test_homepage_uses_the_shared_readiness_conclusion_not_a_conflicting_legacy_stance():
    from bs4 import BeautifulSoup
    from publish_site import _render_verdict
    html = _render_verdict({
        "data_ok": True, "stance": "中性震荡", "head": "旧标题", "play": "旧动作",
        "decision_readiness": {
            "report_date": "2026-09-03", "publication_mode": "observation",
            "data": {"status": "ready", "label": "通过", "scope": "核心行情"},
            "strategy": {"status": "unverified", "label": "尚未验证"},
            "signal": {"status": "not_evaluable", "label": "不可评估"},
            "action": {"status": "no_new_positions", "label": "不开新仓", "reason": "策略数据缺失"},
            "issues": [], "recheck_conditions": ["补齐炸板、回封数据后重新评估"],
            "execution_ready": False, "plan_permitted": False,
        },
    })
    soup = BeautifulSoup(html, "html.parser")
    panel = soup.select_one(".decision-readiness")
    assert panel is not None
    assert panel["data-action-status"] == "no_new_positions"
    assert "旧动作" not in soup.get_text()
    assert "重新评估" in panel.get_text()
