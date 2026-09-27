"""tools/verify_published_report.py：发布副本被 publish() 合法改写后仍算一致，被篡改则拒绝。

CI 曾用 cmp 逐字节比对，publish() 开始改写副本（研究入口 / 年度专题链接）后每天必挂。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from publish_site import publish  # noqa: E402
from report_integrity import build_report_integrity, render_report_integrity_metadata  # noqa: E402
import verify_published_report as vpr  # noqa: E402

_QUALITY = {
    "status": "ok", "critical_blocked": [],
    "modules": {name: {"status": "ok", "coverage_pct": 100.0, "source": "test"}
                for name in ("universe", "price_raw", "breadth", "limit_pool")},
}


def _report(tmp_path: Path, day: str = "2026-09-24") -> Path:
    meta = render_report_integrity_metadata(build_report_integrity(
        report_date=day, market_date=day, phase_result={}, quality=_QUALITY))
    html = (f'<!doctype html><html><head><meta charset="utf-8"><meta name="report-date" content="{day}">{meta}</head>'
            f'<body><h1>主线强度追踪 <a data-annual-height-study href="annual_height_research.html">年度专题</a></h1>'
            f'<p>{day} 最高板 新华文轩 5 板</p></body></html>')
    out = tmp_path / "out" / "主线强度追踪.html"
    out.parent.mkdir()
    out.write_text(html, encoding="utf-8")
    return out


def test_publish_rewrites_are_accepted_and_tampering_rejected(tmp_path):
    source, site = _report(tmp_path), tmp_path / "site"
    publish(str(source), str(site))   # 报告旁没有年度专题文件：链接被换成提示文字（CI 实况）
    latest = site / "latest.html"
    assert latest.read_bytes() != source.read_bytes()
    assert vpr.verify(source, site, "2026-09-24") == []

    latest.write_text(latest.read_text(encoding="utf-8").replace("新华文轩", "新华文X"), encoding="utf-8")
    problems = vpr.verify(source, site, "2026-09-24")
    assert len(problems) == 1 and "latest.html" in problems[0] and "页面文字" in problems[0]


def test_missing_copy_or_wrong_date_is_reported(tmp_path):
    source, site = _report(tmp_path), tmp_path / "site"
    publish(str(source), str(site))
    (site / "reports" / "2026-09-24.html").unlink()
    problems = vpr.verify(source, site, "2026-09-24")
    assert any("不存在" in p for p in problems)
    assert vpr.verify(source, site, "2026-09-25") != []


@pytest.mark.parametrize("argv,code", [([], 2)])
def test_cli_usage(argv, code):
    assert vpr.main(argv) == code
