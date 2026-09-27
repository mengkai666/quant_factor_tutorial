"""校验站点里发布出去的报告副本就是本次跑批生成的那一份。

publish() 会有意改写副本：去掉 / 注入研究子页入口、把年度研究链接打包到站点目录，
所以 reports/<日期>.html 和 latest.html 与 output/主线强度追踪.html 逐字节不同。
CI 原来用 `cmp --silent` 比对，自 2026-09-23 引入这些改写后每次都失败，站点停在 9/22。

这里改成比内容：两份副本都要通过完整性校验，报告日期、report-integrity 元数据、
页面可见文字（去掉研究入口卡后）都必须与生成的报告一致，只允许链接目标不同。

    python tools/verify_published_report.py <生成的报告> <站点目录> <报告日期>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from publish_site import extract_report_date, remove_research_entry  # noqa: E402
from report_integrity import extract_report_integrity, validate_rendered_report  # noqa: E402

# annual_height_view.package_annual_study 的两种合法改写：年度专题链接要么改 href，
# 要么（专题文件不在报告旁边时，CI 上就是这样）整段换成下面这句提示。比对前两边都抹掉。
_ANNUAL_LINK = re.compile(r'<a\b[^>]*data-annual-height-study[^>]*>.*?</a>', re.S)
_ANNUAL_MISSING = '年度专题文件未随本次发布提供'


def _visible_text(path: Path) -> str:
    html = remove_research_entry(path.read_text(encoding="utf-8"))
    html = _ANNUAL_LINK.sub('', html).replace(_ANNUAL_MISSING, '')
    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)


def verify(source: Path, site_dir: Path, report_date: str) -> list[str]:
    """返回问题列表；空列表表示发布副本与生成的报告一致。"""
    problems: list[str] = []
    expected_meta = extract_report_integrity(source)
    expected_text = _visible_text(source)
    for copy in (site_dir / "reports" / f"{report_date}.html", site_dir / "latest.html"):
        if not copy.is_file() or copy.stat().st_size == 0:
            problems.append(f"{copy} 不存在或为空")
            continue
        try:
            validate_rendered_report(copy)
        except Exception as exc:  # 校验器抛的就是具体原因，原样带出
            problems.append(f"{copy} 未通过完整性校验: {exc}")
            continue
        if extract_report_date(copy) != report_date:
            problems.append(f"{copy} 的报告日期 {extract_report_date(copy)} 不是 {report_date}")
        if extract_report_integrity(copy) != expected_meta:
            problems.append(f"{copy} 的 report-integrity 元数据与生成的报告不一致")
        if _visible_text(copy) != expected_text:
            problems.append(f"{copy} 的页面文字与生成的报告不一致（只允许链接目标被改写）")
    index = site_dir / "index.html"
    if not index.is_file() or f"reports/{report_date}.html" not in index.read_text(encoding="utf-8"):
        problems.append(f"{index} 没有链接 reports/{report_date}.html")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    problems = verify(Path(argv[0]), Path(argv[1]), argv[2])
    for problem in problems:
        print(f"❌ {problem}", file=sys.stderr)
    if not problems:
        print(f"✅ 站点发布副本与生成的报告一致 ({argv[2]})")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
