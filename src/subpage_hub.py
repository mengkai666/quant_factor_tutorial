"""核心实战子研报作战室快捷矩阵组件.

将分散在主报告各处的长篇子研报入口与全景全文解构，
聚合为高密度、精美响应式的现代化 4 卡片作战室快捷矩阵 (War Room Hub)。
提供直观的核心定性结论、关键量化指标与一键直达专属子研报的无缝双向导航。
"""
from __future__ import annotations

import html
from typing import Any


def _e(val: Any) -> str:
    return html.escape(str(val or ''), quote=True)


def render_war_room_hub_html(
    report_date: str = "",
    context: dict[str, Any] | None = None,
    advance_decline: dict[str, Any] | None = None,
) -> str:
    """渲染主报告中的【核心实战子研报作战室快捷矩阵】面板."""
    ctx = context or {}
    ad = advance_decline or ctx.get('advance_decline') or {}
    zt = ad.get('zt', '—')
    dt = ad.get('dt', '—')
    max_h = ad.get('zt_max_height', '—')

    r_date = str(report_date)
    if len(r_date) == 8 and r_date.isdigit():
        r_date = f"{r_date[:4]}-{r_date[4:6]}-{r_date[6:]}"

    # 动态定性与仓位
    is_heavy_retreat = isinstance(dt, int) and dt >= 20
    pos_cap = "0 仓位" if is_heavy_retreat else "≤ 1.5 成"
    stance_tag = "🚨 极端退潮防守" if is_heavy_retreat else "⚖️ 中性控仓防守"

    return f"""
<!-- war-room-hub:start -->
<section class="war-room-hub-section" id="sec-war-room-hub" style="margin: 24px 0 28px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:14px;border-bottom:1px solid #21262d;padding-bottom:10px;">
        <div>
            <h2 class="section-title" id="sec-hub-title" style="margin:0;font-size:18px;font-weight:800;color:#f0f6fc;display:flex;align-items:center;gap:8px;">
                <span>⚡ 核心实战子研报作战室快捷矩阵</span>
                <span style="font-size:11.5px;background:rgba(88,166,255,0.15);color:#58a6ff;border:1px solid rgba(88,166,255,0.3);padding:2px 8px;border-radius:12px;font-weight:600;">
                    4大专属深度研报 · 一键直达作战室
                </span>
            </h2>
            <div style="font-size:12.5px;color:#8b949e;margin-top:4px;">
                针对复盘预案、放量主升回调、专属持仓及龙头抱团周期已分别生成独立深度研报，主报告保留精炼结论与实时数据看板。
            </div>
        </div>
        <div style="font-size:12px;color:#8b949e;">
            基准交易日: <span style="color:#e3b341;font-weight:700;">{_e(r_date or '最新')}</span>
        </div>
    </div>

    <!-- 4 卡片高密度响应式矩阵网格 -->
    <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(260px, 1fr));gap:14px;">
        
        <!-- 卡片 1: 今日复盘与明日预案 -->
        <div style="background:#161b22;border:1px solid #30363d;border-top:3px solid #58a6ff;border-radius:10px;padding:16px 16px 14px;display:flex;flex-direction:column;justify-content:space-between;box-shadow:0 4px 12px rgba(0,0,0,0.25);transition:transform 0.2s ease;">
            <div>
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                    <span style="font-size:11px;font-weight:700;color:#58a6ff;background:rgba(88,166,255,0.12);padding:2px 8px;border-radius:10px;border:1px solid rgba(88,166,255,0.25);">
                        {stance_tag}
                    </span>
                    <span style="font-size:11px;color:#8b949e;">仓位上限 {pos_cap}</span>
                </div>
                <div style="font-size:15px;font-weight:700;color:#f0f6fc;margin-bottom:6px;">
                    📋 今日盘面复盘 × 明日实战预案
                </div>
                <div style="font-size:12px;color:#8b949e;line-height:1.55;margin-bottom:12px;">
                    涨停 {zt} 家 / 跌停 {dt} 家 · 空间最高 {max_h} 板。集合竞价信标 (09:25) · 盘中应变分支 (P0~P3) · 禁买雷区与一键复制晨会卡片。
                </div>
            </div>
            <div style="display:flex;gap:8px;padding-top:10px;border-top:1px solid #21262d;">
                <a href="今日复盘与明日预案_最新.html" style="flex:1;text-align:center;background:#1f6feb;color:#ffffff;text-decoration:none;font-size:12px;font-weight:700;padding:6px 0;border-radius:6px;box-shadow:0 2px 6px rgba(31,111,235,0.3);">
                    📁 本地直接打开 →
                </a>
                <a href="site/plan/latest.html" target="_blank" style="flex:1;text-align:center;background:#21262d;color:#58a6ff;border:1px solid #30363d;text-decoration:none;font-size:12px;font-weight:600;padding:6px 0;border-radius:6px;">
                    🌐 在线预案页 ↗
                </a>
            </div>
        </div>

        <!-- 卡片 2: 强势板块放量主升回调一笔作战室 -->
        <div style="background:#161b22;border:1px solid #30363d;border-top:3px solid #39c5bb;border-radius:10px;padding:16px 16px 14px;display:flex;flex-direction:column;justify-content:space-between;box-shadow:0 4px 12px rgba(0,0,0,0.25);transition:transform 0.2s ease;">
            <div>
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                    <span style="font-size:11px;font-weight:700;color:#39c5bb;background:rgba(57,197,187,0.12);padding:2px 8px;border-radius:10px;border:1px solid rgba(57,197,187,0.25);">
                        🌊 放量主升 · 回踩一笔
                    </span>
                    <span style="font-size:11px;color:#8b949e;">缠论笔中枢</span>
                </div>
                <div style="font-size:15px;font-weight:700;color:#f0f6fc;margin-bottom:6px;">
                    🌊 强势板块回调一笔量化跟踪
                </div>
                <div style="font-size:12px;color:#8b949e;line-height:1.55;margin-bottom:12px;">
                    相对大盘强弱比 RS ≥ 115% · 突破平台不破颈线确认。覆盖 AI算力硬件 / 精细化工 / 核心主线龙头共振低吸买点与风控防守线。
                </div>
            </div>
            <div style="display:flex;gap:8px;padding-top:10px;border-top:1px solid #21262d;">
                <a href="强势板块回调跟踪_最新.html" style="flex:1;text-align:center;background:#0969da;color:#ffffff;text-decoration:none;font-size:12px;font-weight:700;padding:6px 0;border-radius:6px;box-shadow:0 2px 6px rgba(9,105,218,0.3);">
                    📁 本地直接打开 →
                </a>
                <a href="site/pullback/latest.html" target="_blank" style="flex:1;text-align:center;background:#21262d;color:#39c5bb;border:1px solid #30363d;text-decoration:none;font-size:12px;font-weight:600;padding:6px 0;border-radius:6px;">
                    🌐 在线回调页 ↗
                </a>
            </div>
        </div>

        <!-- 卡片 3: 属于zhao的个股追踪专属作战室 -->
        <div style="background:#161b22;border:1px solid #30363d;border-top:3px solid #bc8cff;border-radius:10px;padding:16px 16px 14px;display:flex;flex-direction:column;justify-content:space-between;box-shadow:0 4px 12px rgba(0,0,0,0.25);transition:transform 0.2s ease;">
            <div>
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                    <span style="font-size:11px;font-weight:700;color:#bc8cff;background:rgba(188,140,255,0.12);padding:2px 8px;border-radius:10px;border:1px solid rgba(188,140,255,0.25);">
                        🎯 专属持仓 6 股
                    </span>
                    <span style="font-size:11px;color:#8b949e;">ECharts K线透视</span>
                </div>
                <div style="font-size:15px;font-weight:700;color:#f0f6fc;margin-bottom:6px;">
                    🎯 属于zhao的个股追踪专属作战室
                </div>
                <div style="font-size:12px;color:#8b949e;line-height:1.55;margin-bottom:12px;">
                    吉华集团 · 华映科技 · 焦作万方 · 深圳燃气 · 易明医药 · 锦鸡股份。日线与30分缠论中枢笔透视 × 游资量价攻防 × 斩钉截铁临盘指令。
                </div>
            </div>
            <div style="display:flex;gap:8px;padding-top:10px;border-top:1px solid #21262d;">
                <a href="zhao个股追踪_最新.html" style="flex:1;text-align:center;background:#8957e5;color:#ffffff;text-decoration:none;font-size:12px;font-weight:700;padding:6px 0;border-radius:6px;box-shadow:0 2px 6px rgba(137,87,229,0.3);">
                    📁 本地直接打开 →
                </a>
                <a href="site/zhao/latest.html" target="_blank" style="flex:1;text-align:center;background:#21262d;color:#bc8cff;border:1px solid #30363d;text-decoration:none;font-size:12px;font-weight:600;padding:6px 0;border-radius:6px;">
                    🌐 在线个股页 ↗
                </a>
            </div>
        </div>

        <!-- 卡片 4: 抱团周期演进与龙头接替谱系 -->
        <div style="background:#161b22;border:1px solid #30363d;border-top:3px solid #e3b341;border-radius:10px;padding:16px 16px 14px;display:flex;flex-direction:column;justify-content:space-between;box-shadow:0 4px 12px rgba(0,0,0,0.25);transition:transform 0.2s ease;">
            <div>
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                    <span style="font-size:11px;font-weight:700;color:#e3b341;background:rgba(227,179,65,0.12);padding:2px 8px;border-radius:10px;border:1px solid rgba(227,179,65,0.25);">
                        👑 抱团演进 · 龙头接替
                    </span>
                    <span style="font-size:11px;color:#8b949e;">全周期谱系</span>
                </div>
                <div style="font-size:15px;font-weight:700;color:#f0f6fc;margin-bottom:6px;">
                    👑 抱团周期演进与新周期试错
                </div>
                <div style="font-size:12px;color:#8b949e;line-height:1.55;margin-bottom:12px;">
                    旧抱团瓦解出清 vs 新周期试错竞争。连板总龙身位晋级、监管周期阶梯、近岸蛋白/善水科技等趋势候选证据核验与证伪。
                </div>
            </div>
            <div style="display:flex;gap:8px;padding-top:10px;border-top:1px solid #21262d;">
                <a href="抱团周期分析_最新.html" style="flex:1;text-align:center;background:#d29922;color:#ffffff;text-decoration:none;font-size:12px;font-weight:700;padding:6px 0;border-radius:6px;box-shadow:0 2px 6px rgba(210,153,34,0.3);">
                    📁 抱团分析 →
                </a>
                <a href="site/dragon/latest.html" target="_blank" style="flex:1;text-align:center;background:#21262d;color:#e3b341;border:1px solid #30363d;text-decoration:none;font-size:12px;font-weight:600;padding:6px 0;border-radius:6px;">
                    🐉 龙头谱系 ↗
                </a>
            </div>
        </div>

    </div>
</section>
<!-- war-room-hub:end -->
"""
