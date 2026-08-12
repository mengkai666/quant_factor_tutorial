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