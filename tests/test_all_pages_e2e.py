"""端到端全量页面链接与独立报告健壮性回归测试."""
import os
import re
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "output"
SITE_DIR = OUTPUT_DIR / "site"


def test_standalone_files_exist():
    """验证 4 个独立报告及导航门户在 output/ 根目录与 site/ 目录下均完整存在."""
    expected_local = [
        OUTPUT_DIR / "本地导航入口.html",
        OUTPUT_DIR / "主线强度追踪.html",
        OUTPUT_DIR / "今日复盘与明日预案_最新.html",
        OUTPUT_DIR / "强势板块回调跟踪_最新.html",
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
    ]
    for p in expected_site:
        assert p.exists(), f"Site 站点独立页面缺失: {p}"


def test_no_dead_relative_links_in_pages():
    """验证全量 11 个主要及归档页面内的所有相对 href 链接均可解析为存在的本地物理文件 (杜绝 404 死链)."""
    check_pages = [
        OUTPUT_DIR / "本地导航入口.html",
        OUTPUT_DIR / "主线强度追踪.html",
        OUTPUT_DIR / "今日复盘与明日预案_最新.html",
        OUTPUT_DIR / "强势板块回调跟踪_最新.html",
        SITE_DIR / "index.html",
        SITE_DIR / "latest.html",
        SITE_DIR / "reports" / "2026-09-28.html",
        SITE_DIR / "plan" / "latest.html",
        SITE_DIR / "plan" / "2026-09-28.html",
        SITE_DIR / "pullback" / "latest.html",
        SITE_DIR / "pullback" / "2026-09-28.html",
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
    """验证所有 6 大子页面均具备统一的 .top-nav 顶部全功能导航栏与高亮状态."""
    nav_pages = [
        (SITE_DIR / "reports" / "2026-09-28.html", "主线追踪大报告"),
        (SITE_DIR / "dashboards" / "latest.html", "决策看板"),
        (SITE_DIR / "plan" / "latest.html", "今日复盘与明日预案"),
        (SITE_DIR / "pullback" / "latest.html", "强势板块回调跟踪"),
        (SITE_DIR / "dragon" / "latest.html", "龙头接替谱系"),
        (SITE_DIR / "research" / "september_regime_and_real_estate_study.html", "9月专题深研"),
    ]
    for page, title_keyword in nav_pages:
        assert page.exists(), f"页面不存在: {page}"
        content = page.read_text(encoding="utf-8")
        assert 'class="top-nav"' in content or "class='top-nav'" in content, f"{page.name} 缺少 unified .top-nav 导航栏"
        assert "导航门户" in content, f"{page.name} 导航栏缺少返回导航门户链接"
        assert title_keyword in content, f"{page.name} 缺少当前页面标识: {title_keyword}"


def test_plan_page_interactive_and_mobile_ux():
    """验证预案独立页面包含一键复制按钮、晨会操盘摘要脚本、极度防守熔断警示与移动端适配样式."""
    plan_html_path = SITE_DIR / "plan" / "latest.html"
    assert plan_html_path.exists()
    content = plan_html_path.read_text(encoding="utf-8")

    # 1. 一键复制晨会操盘卡片
    assert "copy-btn" in content
    assert "copyMorningPlanDigest" in content
    assert "一键复制晨会操盘卡片" in content

    # 2. 56 家跌停极端退潮熔断横幅
    assert "melt-banner" in content
    assert "防守熔断生效中" in content
    assert "跌停潮高达 56 家" in content

    # 3. 动态优先级分层矩阵与铁律
    assert "P0 空间独苗" in content
    assert "P1 中位换手" in content
    assert "P2 低位防守" in content
    assert "P3 风险熔断" in content
    assert "P-Black 禁买雷区" in content
    assert "新华传媒" in content

    # 4. 移动端表格首列吸顶/固定 CSS 规则
    assert ".tw table th:first-child" in content
    assert "position: sticky" in content


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
