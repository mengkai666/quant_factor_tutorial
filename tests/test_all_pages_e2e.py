"""端到端全量页面链接与独立报告健壮性回归测试."""
import os
import re
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "output"
SITE_DIR = OUTPUT_DIR / "site"


def test_standalone_files_exist():
    """验证 5 个独立报告及导航门户在 output/ 根目录与 site/ 目录下均完整存在."""
    expected_local = [
        OUTPUT_DIR / "本地导航入口.html",
        OUTPUT_DIR / "主线强度追踪.html",
        OUTPUT_DIR / "今日复盘与明日预案_最新.html",
        OUTPUT_DIR / "强势板块回调跟踪_最新.html",
        OUTPUT_DIR / "zhao个股追踪_最新.html",
    ]
    for p in expected_local:
        assert p.exists(), f"本地独立页面缺失: {p}"

    expected_site = [
        SITE_DIR / "index.html",
        SITE_DIR / "reports" / "2026-09-28.html",
        SITE_DIR / "dashboards" / "latest.html",
        SITE_DIR / "dashboards" / "2026-09-28.html",
        SITE_DIR / "dragon" / "latest.html",
        SITE_DIR / "dragon" / "2026-09-28.html",
        SITE_DIR / "plan" / "latest.html",
        SITE_DIR / "plan" / "2026-09-28.html",
        SITE_DIR / "pullback" / "latest.html",
        SITE_DIR / "pullback" / "2026-09-28.html",
        SITE_DIR / "zhao" / "latest.html",
        SITE_DIR / "zhao" / "2026-09-28.html",
    ]
    for p in expected_site:
        assert p.exists(), f"Site 站点独立页面缺失: {p}"


def test_no_dead_relative_links_in_pages():
    """验证全量 14 个主要及归档页面内的所有相对 href 链接均可解析为存在的本地物理文件 (杜绝 404 死链)."""
    check_pages = [
        OUTPUT_DIR / "本地导航入口.html",
        OUTPUT_DIR / "主线强度追踪.html",
        OUTPUT_DIR / "今日复盘与明日预案_最新.html",
        OUTPUT_DIR / "强势板块回调跟踪_最新.html",
        OUTPUT_DIR / "zhao个股追踪_最新.html",
        SITE_DIR / "index.html",
        SITE_DIR / "latest.html",
        SITE_DIR / "reports" / "2026-09-28.html",
        SITE_DIR / "plan" / "latest.html",
        SITE_DIR / "plan" / "2026-09-28.html",
        SITE_DIR / "pullback" / "latest.html",
        SITE_DIR / "pullback" / "2026-09-28.html",
        SITE_DIR / "zhao" / "latest.html",
        SITE_DIR / "zhao" / "2026-09-28.html",
        SITE_DIR / "dashboards" / "latest.html",
        SITE_DIR / "dashboards" / "2026-09-28.html",
        SITE_DIR / "dragon" / "latest.html",
        SITE_DIR / "dragon" / "2026-09-28.html",
        OUTPUT_DIR / "9月底板块大洗牌与房地产战法深研.html",
        SITE_DIR / "research" / "september_regime_and_real_estate_study.html",
    ]

    for page in check_pages:
        assert page.exists(), f"测试文件缺失: {page}"
        content = page.read_text(encoding="utf-8")
        # 匹配所有形如 href="..." 的链接
        links = re.findall(r'href=["\']([^"\']+)["\']', content)
        for link in links:
            # 过滤 http/https 外部链接、纯锚点 #、javascript 伪协议
            if link.startswith(("http://", "https://", "#", "javascript:")):
                continue
            # 去掉锚点部分
            clean_link = link.split("#")[0]
            if not clean_link:
                continue
            # 解析相对路径
            target_path = (page.parent / clean_link).resolve()
            assert target_path.exists(), f"页面 {page} 存在死链: href='{link}' -> 目标文件不存在: {target_path}"


def test_unified_top_nav_bar_symmetry():
    """验证所有核心子页面均具备统一的 .top-nav 顶部全功能导航栏与高亮状态."""
    nav_pages = [
        (SITE_DIR / "reports" / "2026-09-28.html", "主线追踪大报告"),
        (SITE_DIR / "dashboards" / "latest.html", "决策看板"),
        (SITE_DIR / "plan" / "latest.html", "今日复盘与明日预案"),
        (SITE_DIR / "pullback" / "latest.html", "强势板块回调跟踪"),
        (SITE_DIR / "dragon" / "latest.html", "龙头接替谱系"),
        (SITE_DIR / "research" / "september_regime_and_real_estate_study.html", "9月专题深研"),
        (SITE_DIR / "zhao" / "latest.html", "属于zhao的个股追踪"),
    ]
    for page, title_keyword in nav_pages:
        assert page.exists(), f"页面不存在: {page}"
        content = page.read_text(encoding="utf-8")
        assert 'class="top-nav"' in content or "class='top-nav'" in content, f"{page.name} 缺少 unified .top-nav 导航栏"
        assert "导航门户" in content, f"{page.name} 导航栏缺少返回导航门户链接"
        assert title_keyword in content, f"{page.name} 缺少当前页面标识: {title_keyword}"


def test_plan_page_interactive_and_mobile_ux():
    """验证预案独立页面包含一键复制按钮、晨会操盘摘要脚本、极度防守熔断警示与移动端适配样式."""
    plan_archive = SITE_DIR / "plan" / "2026-09-28.html"
    plan_latest = SITE_DIR / "plan" / "latest.html"
    assert plan_latest.exists()
    content_latest = plan_latest.read_text(encoding="utf-8")

    # 1. 一键复制晨会操盘卡片与移动端表格吸顶
    assert "copy-btn" in content_latest
    assert "copyMorningPlanDigest" in content_latest
    assert "一键复制晨会操盘卡片" in content_latest
    assert ".tw table th:first-child" in content_latest
    assert "position: sticky" in content_latest

    # 2. 验证极端退潮历史归档 (2026-09-28) 包含 56 家跌停极端退潮熔断横幅
    if plan_archive.exists():
        content_archive = plan_archive.read_text(encoding="utf-8")
        assert "melt-banner" in content_archive
        assert "防守熔断生效中" in content_archive
        assert "跌停潮高达 56 家" in content_archive
        assert "P0 空间独苗" in content_archive
        assert "P1 中位换手" in content_archive
        assert "P2 低位防守" in content_archive
        assert "P3 风险熔断" in content_archive
        assert "P-Black 禁买雷区" in content_archive
        assert "新华传媒" in content_archive


def test_pullback_page_quant_structure():
    """验证强势板块回调跟踪独立页面符合量化标准且无教科书冗余废话."""
    pb_html_path = SITE_DIR / "pullback" / "latest.html"
    assert pb_html_path.exists()
    content = pb_html_path.read_text(encoding="utf-8")

    # 1. 核心看板指标
    assert "相对大盘强弱比 RS" in content or "RS ≥ 115%" in content
    assert "Level A" in content
    assert "规则四 · 分时执行与风控熔断规则" in content or "分时操盘执行量化表" in content

    # 2. 标的覆盖
    assert "AI算力硬件" in content or "中际旭创" in content
    assert "新华传媒" in content

    # 3. 100% 杜绝教科书冗余废话
    forbidden_terms = ["深度机理解析", "量化机理", "历史回测真值", "盘面机理"]
    for term in forbidden_terms:
        assert term not in content, f"发现冗余教科书废话词汇: {term}"


def test_zhao_tracker_page_features():
    """验证属于zhao的个股追踪专属页面完整性：6只股票、复盘推演、游资战法、缠论中枢、分时执行表与一键复制功能."""
    zhao_html_path = SITE_DIR / "zhao" / "latest.html"
    assert zhao_html_path.exists(), "zhao专属页面不存在"
    content = zhao_html_path.read_text(encoding="utf-8")

    # 1. 验证6只核心自选持仓个股全部覆盖
    stocks = [
        ("吉华集团", "603980"),
        ("华映科技", "000536"),
        ("焦作万方", "000612"),
        ("深圳燃气", "601139"),
        ("易明医药", "002826"),
        ("锦鸡股份", "300798"),
    ]
    for name, code in stocks:
        assert name in content, f"缺失标的: {name}"
        assert code in content, f"缺失代码: {code}"

    # 2. 验证关键分析模块
    assert "每日走势深度复盘" in content
    assert "缠论多级别结构透视" in content
    assert "游资量价战法攻防" in content
    assert "明日三大走势推演" in content
    assert "实战操盘预案矩阵" in content
    assert "分时操盘执行检查表" in content

    # 3. 验证缠论专业要素
    assert "中枢" in content
    assert "分型" in content
    assert "三买" in content or "买卖点" in content

    # 4. 验证一键复制晨会操盘摘要
    assert "copyMorningBrief" in content
    assert "复制操盘摘要" in content

