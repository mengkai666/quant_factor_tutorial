# -*- coding: utf-8 -*-
"""刷新主线强度追踪报告中内嵌的复盘、明日预案、可转债与抱团面板 UI 样式与同日数据。"""
import re
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import baotuan_cycle as bc
import cb_baotuan_report as cbr
import crowding_next_day_plan as ndp
import daily_fupan_section as fupan


def replace_between(html: str, start_marker: str, end_marker: str, replacement: str) -> str:
    pattern = re.compile(re.escape(start_marker) + r".*?" + re.escape(end_marker), re.DOTALL)
    if pattern.search(html):
        return pattern.sub(lambda _: replacement, html, count=1)
    return html


def upgrade_legacy_dbd_action_plan(html: str) -> str:
    """将已生成报告中的旧版平铺 .dbd-action-plan 升级为统一圆角深色卡片风格。"""
    html = html.replace(
        '<section class="dbd-action-plan" style="margin:16px 0">',
        '<section class="dbd-action-plan" style="margin:18px 0;padding:16px 18px;background:rgba(22,27,34,0.6);border:1px solid #30363d;border-radius:12px;">'
    )
    html = re.sub(
        r'<section class="dbd-action-group" style="min-width:0;border-top:3px solid (#[0-9a-fA-F]+);padding:10px 14px;background:#161b22">',
        r'<section class="dbd-action-group" style="min-width:0;border:1px solid #30363d;border-top:3px solid \1;border-radius:10px;padding:14px 16px;background:rgba(22,27,34,0.85);box-shadow:0 4px 12px rgba(0,0,0,0.2);">',
        html,
    )
    html = html.replace(
        '<article class="candidate-card" style="min-width:0;padding:12px 0;border-top:1px solid #30363d">',
        '<article class="candidate-card" style="min-width:0;padding:12px 14px;margin-top:10px;background:#0d1117;border:1px solid #21262d;border-radius:8px;">'
    )
    # 折叠重复的三行免责声明文字，让视觉核心聚焦于标的名称、触发与撤销条件
    html = re.sub(
        r'(<div><b>价格：</b>.*?</div><div><b>持仓：</b>.*?</div><div style="color:#8b949e">.*?</div>)',
        r'<details style="margin-top:5px;font-size:11px;color:#8b949e;"><summary style="cursor:pointer;color:#6e7681;user-select:none;">交易风控与T+1约束说明</summary><div style="margin-top:4px;padding-top:4px;border-top:1px dashed #21262d;">\1</div></details>',
        html,
    )
    return html


def upgrade_legacy_tactics_panel(html: str) -> str:
    """修正已生成报告中游资作战室内的英文角色名、退潮分支误判及未对账徽章。"""
    role_replacements = {
        ">attack</td>": ">进攻先锋 / 空间核心</td>",
        ">confirm</td>": ">确认观察 / 中位晋级</td>",
        ">risk</td>": ">风险锚定 / 防守观察</td>",
    }
    for old, new in role_replacements.items():
        html = html.replace(old, new)

    if "【无当日对账】" in html:
        html = html.replace(
            '<span style="font-size:11px;color:#3fb950;font-weight:bold;">已验证全命中 · 逻辑闭环</span>',
            '<span style="font-size:11px;color:#8b949e;font-weight:bold;">⚪ 历史预案待对账 · 不沿用旧结论</span>',
        )

    # 修正“广度弱、接力弱：高位退潮优先”分支被旧逻辑赋予修复触发词的问题
    old_retreat_block = (
        '<div style="font-weight:700;color:#f0f6fc;font-size:13px;margin-bottom:6px;display:flex;align-items:center;gap:8px;">\n'
        '                <span style="color:#e3b341;">▶</span> 分支 A · 广度弱、接力弱：高位退潮优先\n'
        '            </div>\n'
        '            <div style="font-size:12px;color:#8b949e;margin-bottom:6px;line-height:1.5;">\n'
        '                <b style="color:#c9d1d9;">触发条件：</b>09:25 集合竞价跌停 ≤ 3 家，09:35 前全市场上涨家数占比维持 &gt; 55%，中位连板梯队出现主动抢筹回封\n'
        '            </div>\n'
        '            <div style="font-size:12px;color:#7ee787;background:rgba(63,185,80,0.08);border-left:3px solid #3fb950;padding:6px 10px;border-radius:4px;line-height:1.5;">\n'
        '                <b style="color:#3fb950;">执行指令：</b>仓位上限 20%；若 9:35 前晋级率回升且中位龙头换手回封，动用 20% 仓位低吸/打板中位换手活口；若跌停 &gt; 5 家或高位连续负反馈则彻底放弃开仓\n'
        '            </div>'
    )
    new_retreat_block = (
        '<div style="font-weight:700;color:#f0f6fc;font-size:13px;margin-bottom:6px;display:flex;align-items:center;gap:8px;">\n'
        '                <span style="color:#e3b341;">▶</span> 分支 A · 广度弱、接力弱：高位退潮优先\n'
        '            </div>\n'
        '            <div style="font-size:12px;color:#8b949e;margin-bottom:6px;line-height:1.5;">\n'
        '                <b style="color:#c9d1d9;">触发条件：</b>高位核心竞价大幅低开或跌停，09:25 跌停家数快速扩张 (&gt;5 家)，或高位连板快速炸板跳水\n'
        '            </div>\n'
        '            <div style="font-size:12px;color:#ffa198;background:rgba(248,81,73,0.08);border-left:3px solid #f85149;padding:6px 10px;border-radius:4px;line-height:1.5;">\n'
        '                <b style="color:#f85149;">执行指令：</b>仓位上限 0%；高位退潮风险释放期，严禁盲目追高开新仓；手中持仓冲高分批减仓落袋，若 9:35 跌停家数继续扩大则执行 0 仓位绝对防守\n'
        '            </div>'
    )
    html = html.replace(old_retreat_block, new_retreat_block)
    return html


def refresh_reports(dates=None):
    if dates is None:
        dates = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]
    for d in dates:
        site_path = PROJECT_ROOT / "output" / "site" / "reports" / f"{d}.html"
        if not site_path.exists():
            continue
        text = site_path.read_text(encoding="utf-8")
        # 1. 更新 daily-fupan-continuous
        fupan_html = fupan.generate_daily_fupan_html(d)
        text = replace_between(text, "<!-- daily-fupan-continuous:start -->", "<!-- daily-fupan-continuous:end -->", fupan_html.strip())
        # 2. 更新 crowding-plan-panel
        ctx = ndp.load_plan_context(d)
        plan_data = ndp.build_next_day_plan(ctx)
        plan_panel_site = ndp.render_plan_panel_html(plan_data, is_site_mode=True)
        text = replace_between(text, "<!-- crowding-plan-panel:start -->", "<!-- crowding-plan-panel:end -->", plan_panel_site.strip())
        # 3. 更新 cb-tracking-teaser
        cb_teaser_site = cbr.render_cb_teaser_card_html(d, is_site_mode=True, prefix="../")
        text = replace_between(text, "<!-- cb-tracking-teaser:start -->", "<!-- cb-tracking-teaser:end -->", cb_teaser_site.strip())
        # 4. 更新 baotuan-teaser
        try:
            bt_res = bc.build_baotuan_cycle(d)
            bt_html = bc.render_baotuan_teaser_html(bt_res).replace('href="baotuan/latest.html"', f'href="../baotuan/{d}.html"')
            text = re.sub(r'<section class="baotuan-teaser".*?</section>', lambda _: bt_html, text, count=1, flags=re.DOTALL)
        except Exception as exc:
            print(f"[{d}] baotuan-teaser skip: {exc}")
        # 5. 升级 dbd-action-plan 与 tactics-review-panel
        text = upgrade_legacy_dbd_action_plan(text)
        text = upgrade_legacy_tactics_panel(text)
        site_path.write_text(text, encoding="utf-8")
        print(f"✅ 已刷新主线报告 UI 与转债数据: {site_path.name}")

    latest_site = PROJECT_ROOT / "output" / "site" / "reports" / "2026-11-06.html"
    if latest_site.exists():
        shutil.copy2(latest_site, PROJECT_ROOT / "output" / "site" / "reports" / "latest.html")
        local_text = latest_site.read_text(encoding="utf-8")
        plan_panel_local = ndp.render_plan_panel_html(ndp.build_next_day_plan(ndp.load_plan_context("2026-11-06")), is_site_mode=False)
        local_text = replace_between(local_text, "<!-- crowding-plan-panel:start -->", "<!-- crowding-plan-panel:end -->", plan_panel_local.strip())
        cb_teaser_local = cbr.render_cb_teaser_card_html("2026-11-06", is_site_mode=False, prefix="")
        local_text = replace_between(local_text, "<!-- cb-tracking-teaser:start -->", "<!-- cb-tracking-teaser:end -->", cb_teaser_local.strip())
        (PROJECT_ROOT / "output" / "主线强度追踪.html").write_text(local_text, encoding="utf-8")
        print("✅ 已同步更新 output/site/reports/latest.html 与 output/主线强度追踪.html")


if __name__ == "__main__":
    refresh_reports()
