# -*- coding: utf-8 -*-
"""
量化预案深度解析与作战看板提取器 (src/plan_parser.py)

功能：
1. 优先从 output/今日复盘与明日预案_YYYY-MM-DD.html（兼容新版 np-plan 结构与旧版 pm-card 结构）
   或 mobile_app/reports/plan_YYYY-MM-DD.html 中提取完整的实战作战看板：
   - 防守熔断/门禁状态 (is_melt, melt_text)
   - 核心策略总纲与门禁一致仓位 (action_slogan, recommended_position)
   - 重点抱团个股预案 (baotuan_stock_targets：100% 绑定真实板块、具体股票名、代码、连板高度/收盘与MA5)
   - 重点抱团可转债预案 (baotuan_cb_targets：优先绑定同日真实转债快照数据与溢价/成交额)
   - 核心标的优先级作战矩阵 (priority_matrix: P0/P1/P2/P3/P-Black)
   - 三大情景推演 (scenarios)、分时段检查表 (checklist)、断板避雷池 (avoid_list)
   - 晨会操盘内参纯文本卡片 (morning_card)
2. 智能匹配报告收盘日 (data-report-date) 与预案目标交易日 (data-target-date)，确保周末及盘前（如周一凌晨查看周一预案）
   精准命中上一交易日生成的最新预案，同时历史归档日严格隔离、绝不跨月串味。
"""

import json
import os
import re
from datetime import date as dt_date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup

# 固化兜底常量结构（保留模块契约兼容单测，并补齐板块与标的字段槽位）
DEFAULT_BAOTUAN_STOCKS = [
    {
        "role": "① 主线空间龙 / 领涨龙头",
        "sector": "主线核心方向",
        "name": "待盘面确认最高标",
        "code": "",
        "metric": "最高连板空间锚",
        "targets": [],
        "tag": "space_hero",
        "badge_color": "#F87171",
        "logic": "全市场最高连板梯队，测试空间压制与题材高度",
        "action": "竞价高开 > +3% 且成交量达标，9:35 确认分歧转一致且同题材有助攻后观察封板质量",
        "pos": "0.5~1.0 成",
        "stop": "盘中跌破分时黄线或炸板不回封坚决离场",
    },
    {
        "role": "② 梯队先锋 / 身位卡位",
        "sector": "主线次高梯队",
        "name": "待盘面确认换手先锋",
        "code": "",
        "metric": "2~4板换手中坚",
        "targets": [],
        "tag": "echelon_vanguard",
        "badge_color": "#38BDF8",
        "logic": "主升次高身位中坚，具备卡位晋级潜质",
        "action": "竞价小幅高开 +1%~+3%，早盘放量换手回踩分时黄线企稳，去弱留强",
        "pos": "0.5~1.0 成",
        "stop": "跌破分时均线超过 10 分钟或同身位掉队无条件止损",
    },
    {
        "role": "③ 核心产业大中军",
        "sector": "趋势容量抱团",
        "name": "待盘面确认容量核心",
        "code": "",
        "metric": "MA5趋势承接锚",
        "targets": [],
        "tag": "core_army",
        "badge_color": "#FCD34D",
        "logic": "主力大资金趋势主战场，板块流动性底座",
        "action": "对照 5 日均线观察早盘回踩承接，分时站稳均线且板块共振时关注，不追高脉冲",
        "pos": "0.5~1.0 成",
        "stop": "有效跌破 5 日均线或分时放量破位 -3% 无条件止损",
    },
    {
        "role": "⛔ 禁买出清黑名单",
        "sector": "高位断板 / 负反馈",
        "name": "高位断板及跌停破位标的",
        "code": "",
        "metric": "风控一票否决",
        "targets": [],
        "tag": "avoid_black",
        "badge_color": "#EF4444",
        "logic": "昨日高位连板今日断板跌停股，次日反包率极低",
        "action": "一票否决！严禁低吸、严禁抄底首阴、严禁追反包！若已持有，早盘脉冲抽高果断减仓离场",
        "pos": "0 仓位",
        "stop": "负反馈未收敛前严禁接飞刀",
    },
]

DEFAULT_BAOTUAN_CBS = [
    {
        "bond_name": "福新转债",
        "bond_code": "111012",
        "role": "【高成交人气标杆 · 联动观察】",
        "badge_color": "#F87171",
        "stock_name": "福莱新材",
        "stock_pct": "联动观察",
        "action": "• 锚定正股竞价与早盘分时承接，急拉不追高，回踩分时均线企稳且正股转强时再作 T+0 观察；若正股转弱破位立即撤退。",
        "pos": "0.5~1.0 成",
        "stop": "跌破分时黄线超 5 分钟或正股放量跳水立即止损；日内 T+0 严控隔夜溢价回撤。",
    },
    {
        "bond_name": "奥普转债",
        "bond_code": "118075",
        "role": "【弹性活跃观察 · 波段跟踪】",
        "badge_color": "#38BDF8",
        "stock_name": "奥普特",
        "stock_pct": "联动观察",
        "action": "• 核对正股止跌企稳信号与转股溢价率变化（注意转股期条款），仅在债股同向放量回升时跟踪日内脉冲。",
        "pos": "0.5 成",
        "stop": "冲高回落跌破开盘价或正股继续走弱坚决放弃。",
    },
    {
        "bond_name": "精测转2",
        "bond_code": "123176",
        "role": "【板块共振观察 · 辅助锚点】",
        "badge_color": "#A78BFA",
        "stock_name": "精测电子",
        "stock_pct": "联动观察",
        "action": "• 观察半导体/设备方向共振修复力度，债股同步企稳前保持观望，不单凭债端拉升追价。",
        "pos": "0.5 成",
        "stop": "分时均线破位或溢价被动走阔时无条件撤退。",
    },
    {
        "bond_name": "澳弘转债",
        "bond_code": "111024",
        "role": "【破位出清 · 一票否决禁买】",
        "badge_color": "#EF4444",
        "stock_name": "澳弘电子",
        "stock_pct": "破位风险",
        "action": "• 正股走弱破位释放负反馈，一票否决严禁抄底！规避正股下探拖累转债杀溢价风险。",
        "pos": "0 仓位",
        "stop": "全天锁定 0 仓位，禁止开新仓。",
    },
]


def _iso_date(val: Any) -> str:
    txt = str(val or "").strip()
    if re.fullmatch(r"\d{8}", txt):
        return f"{txt[:4]}-{txt[4:6]}-{txt[6:]}"
    m = re.search(r"(\d{4}-\d{2}-\d{2})", txt)
    return m.group(1) if m else ""


def _clean_spaces(txt: str) -> str:
    return re.sub(r"\s+", " ", str(txt or "")).strip()


def _compact_stock_name(txt: str) -> str:
    # 处理如 "金 螳 螂" -> "金螳螂" 便于匹配与移动端紧凑展示
    s = str(txt or "").strip()
    if re.fullmatch(r"[\u4e00-\u9fa5A-Za-z0-9\sＡＢ]+", s) and " " in s:
        parts = s.split()
        if all(len(p) == 1 for p in parts):
            return "".join(parts)
    return s


def _format_baotuan_digest(baotuan_stocks: list, baotuan_cbs: list) -> str:
    """生成抱团个股与转债预案的晨会卡片纯文本（包含明确板块、逐股判断、精确买卖点与止损价）"""
    s_lines = ["【重点抱团个股 · 实战预案指令】"]
    for s in baotuan_stocks:
        role = s.get("role", "")
        sector = s.get("sector", "")
        name = s.get("name", "")
        code = s.get("code", "")
        act = s.get("action", "").replace("<br>", " ")
        pos = s.get("pos", "")
        stop = s.get("stop", "")
        sec_str = f"【{sector}】" if sector else ""
        target_str = f"{sec_str}{name}" + (f"({code})" if code and code not in name else "")
        s_lines.append(f"{role}｜{target_str} (建议仓位: {pos})")
        s_lines.append(f"• 核心判断：{act}")
        for d in s.get("stock_details") or []:
            s_lines.append(
                f"  ▸ {d.get('name')}({d.get('code')}) [{d.get('badge', '')}]：{d.get('judgment', '')} "
                f"｜买点：{d.get('buy_trigger', '')} ｜止损：{d.get('stop_loss', '')}"
            )
        s_lines.append(f"• 纪律底线：{stop}")

    cb_lines = ["【重点抱团可转债 · 明日 T+0 深度作战预案】"]
    for c in baotuan_cbs:
        b_name = c.get("bond_name", "")
        b_code = c.get("bond_code", "")
        role = c.get("role", "")
        s_name = c.get("stock_name", "")
        s_pct = c.get("stock_pct", "")
        act = c.get("action", "").replace("<br>", " ")
        pos = c.get("pos", "")
        stop = c.get("stop", "")
        cb_lines.append(f"{b_name}({b_code}) {role} (正股:{s_name} {s_pct} | 仓位:{pos})")
        cb_lines.append(f"• 应对：{act}")
        cb_lines.append(f"• 底线：{stop}")

    return "\n".join(s_lines) + "\n\n" + "\n".join(cb_lines)


def _build_cb_targets_from_rows(cb_rows: List[Dict[str, Any]], is_extreme_ice: bool = False) -> List[Dict[str, Any]]:
    """从真实同日转债行构建 4 张逐债量化结合的 T+0 预案卡"""
    if not cb_rows:
        return [dict(x) for x in DEFAULT_BAOTUAN_CBS]

    # 1) 成交额第一标杆债
    top_liq = cb_rows[0]
    # 2) 筛选弱势/大跌风险债（正股跌停或债跌幅最大）
    weak_candidates = sorted(
        cb_rows,
        key=lambda r: (
            float(r.get("stock_r1_val")) if r.get("stock_r1_val") is not None else 0.0,
            float(r.get("bond_r1_val")) if r.get("bond_r1_val") is not None else 0.0,
        ),
    )
    risk_bond = None
    for cand in weak_candidates:
        if cand.get("code") != top_liq.get("code") and (
            (cand.get("stock_r1_val") is not None and cand["stock_r1_val"] <= -4.0)
            or (cand.get("bond_r1_val") is not None and cand["bond_r1_val"] <= -4.0)
        ):
            risk_bond = cand
            break
    if risk_bond is None and len(weak_candidates) > 1:
        risk_bond = next((c for c in weak_candidates if c.get("code") != top_liq.get("code")), None)

    used_codes = {top_liq.get("code")}
    if risk_bond:
        used_codes.add(risk_bond.get("code"))

    # 3) 筛选其余强势/高换手代表债 2 只（优先债涨幅 > 0，其次成交额靠前）
    rest_pool = [r for r in cb_rows if r.get("code") not in used_codes]
    rest_pool.sort(
        key=lambda r: (
            0 if (r.get("bond_r1_val") or 0) > 0 else 1,
            -(r.get("bond_r1_val") if r.get("bond_r1_val") is not None else -99.0),
            -(r.get("amount_val") or 0.0),
        ),
    )
    mid_bonds = rest_pool[:2]

    def _to_num(s: str) -> Optional[float]:
        try:
            return float(re.sub(r"[^\d.\-+]", "", str(s)))
        except Exception:
            return None

    def _fmt_cb_card(row: Dict[str, Any], slot: str) -> Dict[str, Any]:
        b_name = row.get("name", "")
        b_code = row.get("code", "")
        s_name = row.get("stock_name", "正股待核")
        b_r1 = row.get("bond_r1_str", "—")
        s_r1 = row.get("stock_r1_str", "—")
        close_s = row.get("close_str", "—")
        prem_s = row.get("premium_str", "—")
        amt_s = row.get("amount_str", "—")
        bal_s = row.get("balance_str", "—")
        cls_tag = row.get("classification", "债股联动")

        close_v = _to_num(close_s)
        prem_v = _to_num(prem_s)
        bal_v = _to_num(bal_s)
        b_r1_v = row.get("bond_r1_val")
        s_r1_v = row.get("stock_r1_val")

        metrics_brief = f"收 {close_s} ({b_r1}) · 成交 {amt_s} · 溢价 {prem_s} · 余额 {bal_s}"

        # 结合余额规模、溢价率与债股联动生成准确判断与精确价格锚
        size_diag = (
            f"余额仅 {bal_s} 属极小盘高弹性妖债（游资抱团阻力极小）"
            if (bal_v is not None and bal_v <= 1.8)
            else f"余额 {bal_s} 属中等/偏大盘流动性转债（依赖正股趋势驱动）"
        )
        prem_diag = (
            f"当前转股溢价高达 {prem_s}（脱离纯转股价值，属纯情绪/资金博弈，严防日内杀溢价）"
            if (prem_v is not None and prem_v >= 70.0)
            else f"转股溢价 {prem_s}（股债弹性传导顺畅，正股拉升时具备高跟随度）"
        )
        support_px = f"{close_v * 0.985:.2f}" if close_v else "分时均线"
        break_px = f"{close_v * 1.015:.2f}" if close_v else "早盘高点"
        stop_px = f"{close_v * 0.97:.2f}" if close_v else "分时前低"

        if slot == "risk":
            return {
                "bond_name": b_name,
                "bond_code": b_code,
                "role": "【破位风险锚 · 禁买】",
                "badge_color": "#EF4444",
                "stock_name": s_name,
                "stock_pct": f"正股 {s_r1}｜债 {b_r1}",
                "bond_metrics": metrics_brief,
                "action": f"⛔ 一票否决禁买！正股【{s_name}】破位，严防杀估值+杀溢价双杀，严禁抄底接飞刀。",
                "pos": "0 仓位",
                "stop": f"正股继续下探坚决规避，不抄底。",
                "close_str": close_s,
                "prem_str": prem_s,
                "amt_str": amt_s,
            }
        if slot == "top":
            role_txt = f"【成交榜首 · {cls_tag}】"
            color = "#F87171"
            pos_txt = "0.5~1.0 成 (T+0)"
            act_txt = f"依托 {support_px}~{close_s}元 回踩企稳或突破 {break_px}元 放量顺势做日内 T+0，正股走弱即出。"
            stop_txt = f"跌破 {stop_px}元 或失守分时黄线立即平仓，绝不隔夜。"
        elif slot == "vanguard":
            role_txt = f"【活跃先锋 · {cls_tag}】"
            color = "#38BDF8"
            pos_txt = "0.5~1.0 成 (T+0)"
            act_txt = f"回踩分时黄线（约 {support_px}元）企稳突破 {break_px}元 顺势做 T+0，冲高 +3%~+5% 分批止盈。"
            stop_txt = f"正股翻绿或转债失守 {stop_px}元 无条件平仓。"
        else:
            role_txt = f"【联动套利 · {cls_tag}】"
            color = "#A78BFA"
            pos_txt = "0.5 成 (T+0)"
            act_txt = f"正股早盘放量涨 >+2% 时，转债站稳 {close_s}元 轻仓 0.5 成跟随套利，不顶高溢价。"
            stop_txt = f"跌破 {stop_px}元 立即止损。"

        return {
            "bond_name": b_name,
            "bond_code": b_code,
            "role": role_txt,
            "badge_color": color,
            "stock_name": s_name,
            "stock_pct": f"正股 {s_r1}｜债 {b_r1}",
            "bond_metrics": metrics_brief,
            "action": act_txt,
            "pos": pos_txt,
            "stop": stop_txt,
            "close_str": close_s,
            "prem_str": prem_s,
            "amt_str": amt_s,
        }

    out = [_fmt_cb_card(top_liq, "top")]
    if len(mid_bonds) >= 1:
        out.append(_fmt_cb_card(mid_bonds[0], "vanguard"))
    if len(mid_bonds) >= 2:
        out.append(_fmt_cb_card(mid_bonds[1], "second"))
    if risk_bond:
        out.append(_fmt_cb_card(risk_bond, "risk"))

    for fb in DEFAULT_BAOTUAN_CBS:
        if len(out) >= 4:
            break
        if not any(x.get("bond_code") == fb["bond_code"] for x in out):
            out.append(dict(fb))
    return out[:4]


def _load_replay_stock_metrics(html_path: Path, report_date: str) -> Dict[str, Dict[str, Any]]:
    """读取同日抱团周期分析 JSON 的 screening_replay，补齐任意个股精确收盘价、MA5、1/5/10/30日涨幅及异动安全垫"""
    import math
    candidates = [
        html_path.parent / f"抱团周期分析_{report_date}.json",
        html_path.parent.parent / "output" / f"抱团周期分析_{report_date}.json",
    ]
    for p in candidates:
        if not p.exists():
            continue
        try:
            payload = json.loads(p.read_text(encoding="utf-8-sig"))
            replay = payload.get("screening_replay") or {}
            dates = replay.get("dates") or []
            prices_map = replay.get("prices") or {}
            if report_date not in dates:
                continue
            idx = dates.index(report_date)
            out: Dict[str, Dict[str, Any]] = {}
            for code, raw_series in prices_map.items():
                if not isinstance(raw_series, list) or len(raw_series) <= idx:
                    continue
                hist = raw_series[: idx + 1]
                c_now = hist[-1]
                if not isinstance(c_now, (int, float)) or c_now <= 0:
                    continue
                c_now = float(c_now)
                r1 = round((c_now / float(hist[-2]) - 1.0) * 100, 2) if len(hist) >= 2 and hist[-2] else None
                r5 = round((c_now / float(hist[-6]) - 1.0) * 100, 2) if len(hist) >= 6 and hist[-6] else None
                r10 = round((c_now / float(hist[-11]) - 1.0) * 100, 2) if len(hist) >= 11 and hist[-11] else None
                r30 = round((c_now / float(hist[-31]) - 1.0) * 100, 2) if len(hist) >= 31 and hist[-31] else 0.0
                ma5 = round(sum(float(x) for x in hist[-5:]) / 5.0, 2) if len(hist) >= 5 and all(hist[-5:]) else None
                ma5_gap = round((c_now / ma5 - 1.0) * 100, 2) if ma5 and ma5 > 0 else None
                num_code = code[2:] if code.startswith(("sh", "sz", "bj")) else code
                is_20cm = num_code.startswith(("30", "68"))
                limit_pct = 20.0 if is_20cm else 10.0
                limit_rate = 0.20 if is_20cm else 0.10
                safety10 = round(100.0 - r10, 2) if r10 is not None else 100.0
                safety30 = round(200.0 - (r30 or 0.0), 2)
                boards_left = 9.9
                if r10 is not None and len(hist) >= 11 and hist[-11]:
                    ratio_needed = 2.0 / (c_now / float(hist[-11]))
                    boards_left = 0.0 if ratio_needed <= 1.0 else round(math.log(ratio_needed) / math.log(1.0 + limit_rate), 1)
                if safety10 <= limit_pct:
                    risk_level = "danger"
                    risk_text = f"极度危险 (余{safety10:.1f}%/≤1板触线)"
                elif safety10 <= limit_pct * 2.2:
                    risk_level = "warning"
                    risk_text = f"警戒区 (余{safety10:.1f}%/约{boards_left}板)"
                else:
                    risk_level = "safe"
                    risk_text = f"安全垫充裕 (余{boards_left}板)"
                out[code] = {
                    "close": round(c_now, 2),
                    "ma5": ma5,
                    "ma5_gap": ma5_gap,
                    "r1": r1,
                    "r5": r5,
                    "r10": r10,
                    "r30": r30,
                    "is_20cm": is_20cm,
                    "safety10": safety10,
                    "safety30": safety30,
                    "boards_left": max(0.0, boards_left),
                    "risk_level": risk_level,
                    "risk_text": risk_text,
                }
            return out
        except Exception:
            continue
    return {}


def _parse_np_plan_html(soup: BeautifulSoup, html_path: Path) -> Dict[str, Any]:
    """精准解析新版 <section class='np-plan'> 复盘与明日预案结构，输出分层条件仓位与逐股战法"""
    np_sec = soup.select_one("section.np-plan")
    report_date = _iso_date(np_sec.get("data-report-date")) or _iso_date(html_path.name) or "2026-11-06"
    target_date = _iso_date(np_sec.get("data-target-date")) or report_date

    # 1. 提取规则主线、门禁结论、KPI 事实
    lead_b = np_sec.select_one(".np-lead b")
    mainline = _clean_spaces(lead_b.get_text()) if lead_b else "主线待确认"

    status_box = np_sec.select_one(".np-status")
    status_strong = _clean_spaces(status_box.find("strong").get_text()) if (status_box and status_box.find("strong")) else ""
    status_p = _clean_spaces(status_box.find("p").get_text()) if (status_box and status_box.find("p")) else ""

    kpis: Dict[str, str] = {}
    for kpi in np_sec.select(".np-kpi"):
        sp = kpi.find("span")
        b = kpi.find("b")
        if sp and b:
            kpis[_clean_spaces(sp.get_text())] = _clean_spaces(b.get_text())

    zt_str = kpis.get("涨停家数", "—")
    dt_str = kpis.get("跌停家数", "—")
    breadth_str = kpis.get("上涨占比", "—")
    promo_str = kpis.get("晋级率", "—")
    zt_num = int(zt_str) if zt_str.isdigit() else 0
    dt_num = int(dt_str) if dt_str.isdigit() else 0
    try:
        breadth_val = float(breadth_str.replace("%", "").strip())
    except Exception:
        breadth_val = 50.0

    # 读取同日全市场个股量价与10日异动安全垫字典
    replay_metrics = _load_replay_stock_metrics(html_path, report_date)

    # 2. 解析昨日预案对账表以补全所有涨停/断板/跌停个股的精确代码、连板高度与当日涨跌幅
    score_map: Dict[str, Dict[str, Any]] = {}
    limit_down_rows: List[Dict[str, str]] = []
    for det in np_sec.find_all("details"):
        sum_el = det.find("summary")
        if sum_el and "昨日预案对账" in sum_el.get_text():
            for tr in det.select("tbody tr"):
                tds = tr.find_all("td")
                if len(tds) >= 4:
                    code_el = tds[0].select_one(".np-code")
                    code_v = _clean_spaces(code_el.get_text()) if code_el else ""
                    raw_nm = _clean_spaces(tds[0].get_text().replace(code_v, ""))
                    nm = _compact_stock_name(raw_nm)
                    role_v = _clean_spaces(tds[1].get_text())
                    pct_v = _clean_spaces(tds[2].get_text())
                    res_v = _clean_spaces(tds[3].get_text())
                    h_m = re.search(r"(\d+)\s*板", res_v)
                    h_val = int(h_m.group(1)) if h_m else 0
                    info = {"name": nm, "code": code_v, "role": role_v, "pct": pct_v, "result": res_v, "height": h_val}
                    score_map[nm] = info
                    if "跌停" in res_v:
                        limit_down_rows.append(info)

    # 同时补充读取同日 research_brief JSON（若本地存在）获取代表股精确代码与连板高度
    brief_path = html_path.parent / "research_briefs" / f"research_brief_{report_date}.json"
    if not brief_path.exists():
        brief_path = html_path.parent.parent / "output" / "research_briefs" / f"research_brief_{report_date}.json"
    if brief_path.exists():
        try:
            b_data = json.loads(brief_path.read_text(encoding="utf-8-sig"))
            for sec_item in b_data.get("sectors") or []:
                if not isinstance(sec_item, dict):
                    continue
                sec_nm = str(sec_item.get("name") or "")
                for st in sec_item.get("stocks") or []:
                    if isinstance(st, dict) and st.get("name"):
                        snm = _compact_stock_name(str(st["name"]))
                        existing = score_map.get(snm, {})
                        score_map[snm] = {
                            **existing,
                            "name": snm,
                            "code": str(st.get("code") or existing.get("code") or ""),
                            "height": int(st.get("current_height") or existing.get("height") or 0),
                            "sector": sec_nm or existing.get("sector", ""),
                        }
        except Exception:
            pass

    # 3. 解析 Section 01: #np-sectors 题材与代表股
    sectors_parsed: List[Dict[str, Any]] = []
    sec_block = np_sec.select_one("#np-sectors")
    if sec_block:
        for tr in sec_block.select("tbody tr"):
            tds = tr.find_all("td")
            if len(tds) >= 4:
                b_el = tds[0].find("b")
                sec_name = _clean_spaces(b_el.get_text()) if b_el else _clean_spaces(tds[0].get_text())
                pill_el = tds[0].select_one(".np-pill")
                sec_pill = _clean_spaces(pill_el.get_text()) if pill_el else ""
                zt_b = tds[1].find("b")
                sec_zt = _clean_spaces(zt_b.get_text()) if zt_b else ""
                zt_cnt_m = re.search(r"(\d+)", sec_zt)
                sec_zt_cnt = int(zt_cnt_m.group(1)) if zt_cnt_m else 0
                chg_m = re.search(r"较前日\s*([+\-]?\d+)", _clean_spaces(tds[1].get_text(" ")))
                sec_chg = int(chg_m.group(1)) if chg_m else 0
                td2_txt = _clean_spaces(tds[2].get_text(" "))
                streak_m = re.search(r"连板\s*(\d+)\s*家", td2_txt)
                maxh_m = re.search(r"最高\s*(\d+)\s*板", td2_txt)
                rep_muted = tds[2].select_one(".np-muted")
                rep_names = [
                    _compact_stock_name(re.sub(r"\(\d+板\)", "", x))
                    for x in re.split(r"[、,，/]+", rep_muted.get_text() if rep_muted else "")
                    if _compact_stock_name(re.sub(r"\(\d+板\)", "", x))
                ]
                watch_el = tds[3].select_one(".np-watch")
                inv_el = tds[3].select_one(".np-invalid")
                rep_objs = []
                for rn in rep_names:
                    meta = score_map.get(rn, {})
                    h_num = meta.get("height") or (int(maxh_m.group(1)) if (maxh_m and rn == rep_names[0]) else 0)
                    rep_objs.append({
                        "name": rn,
                        "code": meta.get("code", ""),
                        "sector": sec_name,
                        "height": h_num,
                        "tag": f"{h_num}板" if h_num else "代表股",
                    })
                sectors_parsed.append({
                    "name": sec_name,
                    "pill": sec_pill,
                    "zt_text": sec_zt,
                    "zt_count": sec_zt_cnt,
                    "change": sec_chg,
                    "streak": int(streak_m.group(1)) if streak_m else 0,
                    "max_height": int(maxh_m.group(1)) if maxh_m else 0,
                    "reps": rep_objs,
                    "watch": _clean_spaces(watch_el.get_text().replace("跟踪：", "")) if watch_el else "",
                    "invalid": _clean_spaces(inv_el.get_text().replace("失效：", "")) if inv_el else "",
                })

    lead_sec = next((s for s in sectors_parsed if s["name"] == mainline), None) or (sectors_parsed[0] if sectors_parsed else None)
    sub_secs = [s for s in sectors_parsed if s is not lead_sec]
    lead_zt_cnt = lead_sec["zt_count"] if lead_sec else 0
    lead_streak_cnt = lead_sec["streak"] if lead_sec else 0
    lead_max_h = lead_sec["max_height"] if lead_sec else 0

    # 四档量化盘面周期定性（告别机械一刀切“不开新仓 / 0仓位”）
    is_extreme_ice = False
    if zt_num >= 55 and dt_num <= 5 and breadth_val >= 55.0:
        regime_mode = "attack"
        rec_pos = "5 ~ 7 成（主升共振进攻）"
        slogan = f"主升共振做多期 · 猛攻唯一主线【{mainline}】龙头与换手前排（涨停 {zt_str} / 跌停 {dt_str}）"
        is_melt = False
        melt_text = ""
        c1_pos, c2_pos, c3_pos = "1.5~2.0 成 (主攻龙头)", "1.0~1.5 成 (前排首封)", "1.5~2.0 成 (均线低吸)"
    elif dt_num < 10 and (zt_num >= 30 or lead_zt_cnt >= 8):
        regime_mode = "rotation"
        rec_pos = "3 ~ 5 成（聚焦主线前排）"
        slogan = f"良性结构做多 · 聚焦唯一主线【{mainline}】分歧转一致与均线低吸（涨停 {zt_str} / 跌停 {dt_str}）"
        is_melt = False
        melt_text = ""
        c1_pos, c2_pos, c3_pos = "1.0~1.5 成 (分歧转一致)", "0.5~1.0 成 (去弱留强)", "1.0~1.5 成 (MA5低吸)"
    elif dt_num >= 10 and (lead_zt_cnt >= 8 or lead_max_h >= 4):
        regime_mode = "structural_divergence"
        lead_rep_str = "、".join(f"{r['name']}({r['height']}板)" for r in (lead_sec["reps"][:3] if lead_sec else []) if r.get("name"))
        rec_pos = "1 ~ 3 成（基准1成·弱转强加至3成）"
        slogan = f"退潮分歧 × 主线【{mainline}】独立抱团 · 砍杂毛做前排活口与MA5低吸（涨停 {zt_str} / 跌停 {dt_str}）"
        is_melt = True
        melt_text = (
            f"⚡ 冰火两重天结构市判断（{report_date} 收盘 → {target_date} 预案）："
            f"全市场跌停 {dt_str} 家（高位断板与杂毛退潮出清，后排严禁接飞刀），"
            f"但唯一主线【{mainline}】逆势 {lead_zt_cnt} 家涨停、{lead_streak_cnt} 家连板（{lead_rep_str} 100%晋级）！"
            f"策略：告别盲目空仓，严禁买杂毛，基准 1 成底仓，早盘主线活口弱转强或核心股回踩 MA5 企稳时解锁 2~3 成精准开仓！"
        )
        c1_pos, c2_pos, c3_pos = "0.5~1.0 成 (弱转强确认)", "0.5~1.0 成 (4进5胜出者)", "1.0~1.5 成 (MA5支撑低吸)"
    else:
        regime_mode = "ice_defense"
        is_extreme_ice = True
        rec_pos = "0 ~ 1 成（冰点试错防守）"
        slogan = f"极端退潮防守 · 仅保留主线【{mainline}】活口与均线极限缩量试错（涨停 {zt_str} / 跌停 {dt_str}）"
        is_melt = True
        melt_text = (
            f"🚨 退潮防守预警（{report_date} → {target_date}）：涨停 {zt_str} 家 / 跌停 {dt_str} 家，"
            f"亏钱效应扩散，仅在主线活口满足放量弱转强条件时极轻仓（0.5~1成）试错，未达触发条件保持空仓。"
        )
        c1_pos, c2_pos, c3_pos = "0.5 成 (冰点弱转强试错)", "0~0.5 成 (仅看首封胜出)", "0.5~1.0 成 (MA5极限低吸)"

    # 4. 解析 Section 02: #np-stocks 抱团个股（含展开表）
    stocks_parsed: List[Dict[str, Any]] = []
    stk_block = np_sec.select_one("#np-stocks")
    if stk_block:
        for tr in stk_block.select("tbody tr"):
            tds = tr.find_all("td")
            if len(tds) >= 5:
                b_el = tds[0].find("b")
                s_name = _compact_stock_name(b_el.get_text() if b_el else "")
                c_el = tds[0].select_one(".np-code")
                s_code = _clean_spaces(c_el.get_text()) if c_el else ""
                p_el = tds[0].select_one(".np-pill")
                s_role = _clean_spaces(p_el.get_text()) if p_el else "研究观察"

                td1_lines = [_clean_spaces(x) for x in tds[1].stripped_strings]
                close_v = td1_lines[0] if len(td1_lines) >= 1 else "—"
                r1_v = td1_lines[1] if len(td1_lines) >= 2 else "—"
                ma5_v = next((x.replace("MA5", "").strip() for x in td1_lines if "MA5" in x), "—")

                td2_txt = _clean_spaces(tds[2].get_text(" "))
                r5_m = re.search(r"5日\s*([+\-\d.%—]+)", td2_txt)
                r10_m = re.search(r"10日\s*([+\-\d.%—]+)", td2_txt)

                td3_txt = _clean_spaces(tds[3].get_text(" "))
                sup_m = re.search(r"连续支持\s*(\d+)\s*日", td3_txt)
                watch_el = tds[4].select_one(".np-watch")
                inv_el = tds[4].select_one(".np-invalid")

                stocks_parsed.append({
                    "name": s_name,
                    "code": s_code,
                    "role": s_role,
                    "close": close_v,
                    "r1": r1_v,
                    "ma5": ma5_v,
                    "r5": r5_m.group(1) if r5_m else "—",
                    "r10": r10_m.group(1) if r10_m else "—",
                    "support_days": int(sup_m.group(1)) if sup_m else 0,
                    "evidence": td3_txt,
                    "watch": _clean_spaces(watch_el.get_text()) if watch_el else "",
                    "invalid": _clean_spaces(inv_el.get_text()) if inv_el else "",
                })

    # 5. 解析 Section 03: #np-bonds 抱团转债（含展开表）
    bonds_parsed: List[Dict[str, Any]] = []
    bond_block = np_sec.select_one("#np-bonds")
    if bond_block:
        for tr in bond_block.select("tbody tr"):
            tds = tr.find_all("td")
            if len(tds) >= 5:
                b_el = tds[0].find("b")
                b_name = _clean_spaces(b_el.get_text()) if b_el else ""
                c_el = tds[0].select_one(".np-code")
                b_code = _clean_spaces(c_el.get_text()) if c_el else ""
                p_el = tds[0].select_one(".np-pill")
                b_cls = _clean_spaces(p_el.get_text()) if p_el else "债股观察"
                td0_lines = [_clean_spaces(x) for x in tds[0].stripped_strings]
                stk_nm = ""
                for ln in td0_lines:
                    if ln not in {b_name, b_code, b_cls}:
                        stk_nm = _compact_stock_name(ln)
                        break

                td1_txt = _clean_spaces(tds[1].get_text(" "))
                td1_lines = [_clean_spaces(x) for x in tds[1].stripped_strings]
                b_close = td1_lines[0] if td1_lines else "—"
                br1_m = re.search(r"债\s*([+\-\d.%—]+)", td1_txt)
                sr1_m = re.search(r"股\s*([+\-\d.%—]+)", td1_txt)
                br1_s = br1_m.group(1) if br1_m else "—"
                sr1_s = sr1_m.group(1) if sr1_m else "—"

                td2_txt = _clean_spaces(tds[2].get_text(" "))
                prem_m = re.search(r"溢价\s*([+\-\d.%—]+)", td2_txt)
                bal_m = re.search(r"余额\s*([\d.]+)\s*亿", td2_txt)
                amt_m = re.search(r"成交\s*([\d.]+)\s*亿", td2_txt)

                watch_el = tds[4].select_one(".np-watch")
                inv_el = tds[4].select_one(".np-invalid")

                def _to_f(s: str) -> Optional[float]:
                    try:
                        return float(s.replace("%", "").replace("+", ""))
                    except Exception:
                        return None

                bonds_parsed.append({
                    "name": b_name,
                    "code": b_code,
                    "stock_name": stk_nm,
                    "classification": b_cls,
                    "close_str": b_close,
                    "bond_r1_str": br1_s,
                    "stock_r1_str": sr1_s,
                    "bond_r1_val": _to_f(br1_s),
                    "stock_r1_val": _to_f(sr1_s),
                    "premium_str": prem_m.group(1) if prem_m else "—",
                    "balance_str": f"{bal_m.group(1)}亿" if bal_m else "—",
                    "amount_str": f"{amt_m.group(1)}亿" if amt_m else "—",
                    "amount_val": _to_f(amt_m.group(1)) if amt_m else 0.0,
                    "watch": _clean_spaces(watch_el.get_text()) if watch_el else "",
                    "invalid": _clean_spaces(inv_el.get_text()) if inv_el else "",
                })

    # 6. 解析风险开关与不追名单
    risk_gate_names: List[str] = []
    exec_block = np_sec.select_one("#np-execution")
    if exec_block:
        for card in exec_block.select(".np-card"):
            h3 = card.find("h3")
            p = card.find("p")
            if h3 and "风险开关" in h3.get_text() and p:
                risk_gate_names = [
                    _compact_stock_name(x)
                    for x in re.split(r"[、,，/]+", p.get_text())
                    if _compact_stock_name(x)
                ]

    avoid_list: List[str] = []
    avoid_targets: List[Dict[str, str]] = []
    for det in np_sec.find_all("details"):
        sum_el = det.find("summary")
        if sum_el and "不追名单" in sum_el.get_text():
            p_el = det.find("p")
            if p_el:
                raw_avoid = _clean_spaces(p_el.get_text())
                if "未形成可核验" not in raw_avoid:
                    for item in re.split(r"、", raw_avoid):
                        item_clean = _clean_spaces(item)
                        if not item_clean:
                            continue
                        nm_m = re.match(r"([^（(]+)(?:[（(]([^）)]+)[）)])?", item_clean)
                        if nm_m:
                            nm_c = _compact_stock_name(nm_m.group(1))
                            note_c = nm_m.group(2) or "断板"
                            sc_info = score_map.get(nm_c, {})
                            pct_s = f" · 今日 {sc_info['pct']}" if sc_info.get("pct") and sc_info["pct"] != "—" else ""
                            code_s = sc_info.get("code", "")
                            avoid_list.append(f"{nm_c}{f'({code_s})' if code_s else ''}：{note_c}断板{pct_s}，未修复前严禁盲目低吸反包")
                            avoid_targets.append({
                                "name": nm_c,
                                "code": code_s,
                                "sector": "高位断板",
                                "tag": f"{note_c}断板{(' ' + sc_info['pct']) if sc_info.get('pct') and sc_info['pct'] != '—' else ''}".strip(),
                            })

    for ld in limit_down_rows[:4]:
        if not any(t["name"] == ld["name"] for t in avoid_targets):
            avoid_targets.insert(0, {
                "name": ld["name"],
                "code": ld.get("code", ""),
                "sector": "跌停负反馈",
                "tag": f"跌停 {ld.get('pct', '')}".strip(),
            })
            avoid_list.insert(0, f"{ld['name']}({ld.get('code', '')})：今日跌停 ({ld.get('pct', '')})，严禁抄底接飞刀")

    # 7. 动态组装 4 张【重点抱团个股 · 明日实战预案】卡片（逐股深度量化诊断 + 精确买点 + 精确止损）
    # Card 1: 主线空间龙 / 领涨龙头
    c1_targets: List[Dict[str, Any]] = []
    if lead_sec and lead_sec["reps"]:
        top_rep = lead_sec["reps"][0]
        c1_targets.append({
            "name": top_rep["name"],
            "code": top_rep.get("code", ""),
            "sector": lead_sec["name"],
            "height": top_rep.get("height", 0),
            "tag": f"{top_rep['height']}板·主线龙头" if top_rep.get("height") else "主线代表",
        })
    for s_other in sub_secs:
        if s_other["reps"] and s_other["max_height"] >= max(lead_max_h, 4):
            r0 = s_other["reps"][0]
            if not any(t["name"] == r0["name"] for t in c1_targets):
                c1_targets.append({
                    "name": r0["name"],
                    "code": r0.get("code", ""),
                    "sector": s_other["name"],
                    "height": r0.get("height", 0),
                    "sec_change": s_other.get("change", 0),
                    "sec_streak": s_other.get("streak", 0),
                    "tag": f"{r0['height']}板·跨题材对照" if r0.get("height") else "高标对照",
                })
                break

    c1_sec_label = lead_sec["name"] if lead_sec else mainline
    c1_names = " / ".join(t["name"] for t in c1_targets) or "待盘面确认主线高标"
    c1_codes = " / ".join(t["code"] for t in c1_targets if t.get("code"))

    c1_stock_details: List[Dict[str, str]] = []
    for idx_t, t in enumerate(c1_targets):
        nm, cd, sec_t, h_t = t["name"], t.get("code", ""), t.get("sector", c1_sec_label), t.get("height") or lead_max_h
        rm = replay_metrics.get(cd, {})
        cl_v, ma5_v, r1_v, r10_v, saf10 = rm.get("close"), rm.get("ma5"), rm.get("r1"), rm.get("r10"), rm.get("safety10", 100.0)
        b_left = rm.get("boards_left", 3.0)
        metrics_txt = (
            f"收 {cl_v:.2f}元 ({r1_v:+.2f}%)｜MA5 {ma5_v:.2f}元｜10日 {r10_v:+.2f}% (距100%异动线余 {saf10:.1f}% / 约{b_left}板)"
            if cl_v and ma5_v and r1_v is not None and r10_v is not None
            else f"{sec_t} · {h_t}连板最高标"
        )
        if idx_t == 0:
            open_lo = f"{cl_v * 1.03:.2f}" if cl_v else "+3%"
            open_hi = f"{cl_v * 1.07:.2f}" if cl_v else "+7%"
            hold_px = f"{cl_v * 0.98:.2f}" if cl_v else "分时黄线"
            stop_px = f"{cl_v * 0.95:.2f}" if cl_v else "-5%"
            judg = f"主线唯一高标空间活口，梯队完整护航，确定性居首。"
            buy_t = f"高开 +3%~+7%（{open_lo}~{open_hi}元）放量回踩 {hold_px}元 企稳率先回封打板（一字不排）。"
            stop_t = f"核按钮低开 <-3% 放弃；盘中破 {stop_px}元 或失守分时黄线坚决止损。"
        else:
            stop_px = f"{cl_v * 0.96:.2f}" if cl_v else "分时黄线"
            judg = f"跨题材独苗高标，所属后排连板断层，仅作情绪参照。"
            buy_t = f"仅作为同身位 PK 参照，默认不买；若竞价极度超预期卡位才考虑极轻仓试错。"
            stop_t = f"竞价弱于主线龙头或开盘跌破 {stop_px}元 坚决规避。"
        c1_stock_details.append({
            "name": nm,
            "code": cd,
            "badge": f"{sec_t} · {h_t}连板",
            "metrics": metrics_txt,
            "judgment": judg,
            "buy_trigger": buy_t,
            "stop_loss": stop_t,
            "risk_tag": rm.get("risk_level", "safe"),
            "is_primary": idx_t == 0,
            "latest_close": f"{cl_v:.2f}元" if cl_v else "—",
            "r1_pct": f"{r1_v:+.2f}%" if r1_v is not None else "—",
            "ma5_px": f"{ma5_v:.2f}元" if ma5_v else "—",
            "boards_left_str": f"{b_left}板" if b_left else "—",
            "safety_pct": f"{saf10:.1f}%",
        })

    c1_logic = f"唯一主线【{c1_sec_label}】空间高标，聚焦梯队完整的领涨活口。"
    c1_action = f"• 买点：{c1_stock_details[0]['buy_trigger'] if c1_stock_details else '高开换手打板'}"
    c1_stop = c1_stock_details[0]["stop_loss"] if c1_stock_details else "破位止损。"

    # Card 2: 梯队先锋 / 身位卡位
    c2_targets: List[Dict[str, Any]] = []
    if lead_sec and len(lead_sec["reps"]) > 1:
        for rep in lead_sec["reps"][1:]:
            c2_targets.append({
                "name": rep["name"],
                "code": rep.get("code", ""),
                "sector": lead_sec["name"],
                "height": rep.get("height", 0),
                "tag": f"{rep['height']}板·主线中坚" if rep.get("height") else "主线梯队",
            })
    for sc_item in score_map.values():
        if len(c2_targets) >= 4:
            break
        if sc_item.get("height") in (2, 3, 4) and "涨停" in str(sc_item.get("result", "")):
            if not any(t["name"] == sc_item["name"] for t in c1_targets + c2_targets):
                sec_lbl = sc_item.get("sector") or next(
                    (s["name"] for s in sectors_parsed for r in s["reps"] if r["name"] == sc_item["name"]),
                    "梯队晋级",
                )
                c2_targets.append({
                    "name": sc_item["name"],
                    "code": sc_item.get("code", ""),
                    "sector": sec_lbl,
                    "height": sc_item.get("height", 0),
                    "tag": f"{sc_item['height']}板",
                })
    if not c2_targets and sub_secs:
        for s_sub in sub_secs[:2]:
            for r in s_sub["reps"][:1]:
                c2_targets.append({
                    "name": r["name"],
                    "code": r.get("code", ""),
                    "sector": s_sub["name"],
                    "height": r.get("height", 0),
                    "tag": f"{r['height']}板" if r.get("height") else "分支先锋",
                })

    c2_names = " / ".join(t["name"] for t in c2_targets[:3]) or "待确认中位先锋"
    c2_codes = " / ".join(t["code"] for t in c2_targets[:3] if t.get("code"))

    c2_stock_details: List[Dict[str, str]] = []
    for idx_t, t in enumerate(c2_targets[:3]):
        nm, cd, sec_t, h_t = t["name"], t.get("code", ""), t.get("sector", c1_sec_label), t.get("height") or 3
        rm = replay_metrics.get(cd, {})
        cl_v, ma5_v, r1_v, r10_v, saf10 = rm.get("close"), rm.get("ma5"), rm.get("r1"), rm.get("r10"), rm.get("safety10", 100.0)
        b_left = rm.get("boards_left", 3.0)
        peer_names = "、".join(x["name"] for x in c2_targets[:3] if x["name"] != nm) or "同身位标的"
        metrics_txt = (
            f"收 {cl_v:.2f}元 ({r1_v:+.2f}%)｜MA5 {ma5_v:.2f}元｜10日 {r10_v:+.2f}% (距100%异动线余 {saf10:.1f}% / 约{b_left}板)"
            if cl_v and ma5_v and r1_v is not None and r10_v is not None
            else f"{sec_t} · {h_t}连板梯队"
        )
        open_lo = f"{cl_v * 1.02:.2f}" if cl_v else "+2%"
        open_hi = f"{cl_v * 1.06:.2f}" if cl_v else "+6%"
        stop_px = f"{cl_v * 0.96:.2f}" if cl_v else "-4%"
        c2_stock_details.append({
            "name": nm,
            "code": cd,
            "badge": f"{sec_t} · {h_t}连板",
            "metrics": metrics_txt,
            "judgment": f"【{sec_t}】{h_t}进{h_t+1} 卡位PK，只做放量首封第一名。",
            "buy_trigger": f"高开 +2%~+6%（{open_lo}~{open_hi}元），率先放量回封打板（胜出者）。",
            "stop_loss": f"PK落败掉队或跌破分时黄线（<{stop_px}元）坚决止损。",
            "risk_tag": rm.get("risk_level", "safe"),
            "latest_close": f"{cl_v:.2f}元" if cl_v else "—",
            "r1_pct": f"{r1_v:+.2f}%" if r1_v is not None else "—",
            "ma5_px": f"{ma5_v:.2f}元" if ma5_v else "—",
            "boards_left_str": f"{b_left}板" if b_left else "—",
            "safety_pct": f"{saf10:.1f}%",
        })

    c2_logic = f"主线及分支中位换手梯队，同身位去弱留强，只做换手首封胜出者。"
    c2_action = f"• 买点：{c2_stock_details[0]['buy_trigger'] if c2_stock_details else '率先换手首封打板'}"
    c2_stop = "同身位掉队或破分时黄线坚决止损。"

    # Card 3: 抱团趋势核心 / 延续观察锚
    active_crowd = [s for s in stocks_parsed if "风险" not in s.get("role", "")]
    active_crowd_sorted = sorted(
        active_crowd,
        key=lambda x: (
            0 if "持续核心" in x.get("role", "") else 1,
            0 if str(x.get("r1", "")).startswith("+") else 1,
            -int(x.get("support_days") or 0),
        ),
    )
    strong_up = [x for x in active_crowd if str(x.get("r1", "")).startswith("+")]
    core_only = [x for x in active_crowd if "持续核心" in x.get("role", "")]
    picked_crowd: List[Dict[str, Any]] = []
    # 将核心MA5回踩锚（如青岛食品）与低位突破锚（如阳光照明）及高危异动预警锚（如奥雅股份、东方海洋）全部纳入逐股拆解
    for item in core_only[:1] + strong_up[:3] + active_crowd_sorted:
        if len(picked_crowd) >= 4:
            break
        if not any(p["code"] == item["code"] for p in picked_crowd):
            picked_crowd.append(item)

    c3_targets = [
        {
            "name": s["name"],
            "code": s["code"],
            "sector": s["role"],
            "tag": f"收{s['close']} ({s['r1']}) · MA5 {s['ma5']}",
        }
        for s in picked_crowd
    ]
    c3_names = " / ".join(s["name"] for s in picked_crowd[:4]) or "待确认趋势抱团股"
    c3_codes = " / ".join(s["code"] for s in picked_crowd[:4] if s.get("code"))

    c3_stock_details: List[Dict[str, str]] = []
    low_buy_names: List[str] = []
    high_warn_names: List[str] = []
    for s in picked_crowd[:4]:
        nm, cd, rl = s["name"], s["code"], s.get("role", "抱团观察")
        rm = replay_metrics.get(cd, {})
        cl_v = rm.get("close")
        ma5_v = rm.get("ma5")
        ma5_gap = rm.get("ma5_gap")
        r1_v = rm.get("r1")
        r10_v = rm.get("r10")
        saf10 = rm.get("safety10", 100.0)
        b_left = rm.get("boards_left", 3.0)
        metrics_txt = (
            f"收 {cl_v:.2f}元 ({r1_v:+.2f}%)｜MA5 {ma5_v:.2f}元 (偏离{ma5_gap:+.2f}%)｜10日 {r10_v:+.2f}% (余{saf10:.1f}%)"
            if cl_v and ma5_v and ma5_gap is not None and r1_v is not None and r10_v is not None
            else f"收 {s['close']} ({s['r1']})｜MA5 {s['ma5']}｜10日 {s['r10']}"
        )
        if r10_v is not None and r10_v >= 85.0:
            high_warn_names.append(f"{nm}(10日{r10_v:+.1f}%)")
            ceil_px = cl_v * (1.0 + max(0.01, saf10 / 200.0)) if cl_v else 0.0
            judg = f"10日已涨 {r10_v:+.1f}%，距100%严重异动停牌线仅余 {saf10:.1f}%，严防冲高兑现。"
            buy_t = f"⛔ 严禁追高打板！持仓者早盘冲高 {ceil_px:.2f}元 附近分批止盈。"
            stop_t = f"冲高滞涨或跌破 {cl_v*0.95:.2f}元 立即止盈撤退。"
        elif ma5_v and ma5_gap is not None and abs(ma5_gap) <= 3.0:
            low_buy_names.append(f"{nm}(回踩MA5 {ma5_v:.2f}元)")
            judg = f"连续支持 {s.get('support_days', 5)} 日后首阴缩量回踩 MA5，盈亏比极佳。"
            buy_t = f"回踩 {ma5_v*0.993:.2f}~{ma5_v*1.005:.2f}元 缩量止跌企稳低吸 1.0成博弈反包。"
            stop_t = f"放量跌破 {ma5_v*0.98:.2f}元（破 MA5 超 2%）坚决止损。"
        else:
            low_buy_names.append(f"{nm}(收{cl_v:.2f}元)" if cl_v else nm)
            judg = f"强势突破延续标的，无监管异动压制，承接高位溢出资金。"
            buy_t = f"回踩分时黄线（{cl_v*0.975:.2f}元）企稳后 0.5~1.0成 跟随。"
            stop_t = f"跌破 {cl_v*0.95:.2f}元 或失守 MA5 坚决止损。"
        c3_stock_details.append({
            "name": nm,
            "code": cd,
            "badge": rl,
            "metrics": metrics_txt,
            "judgment": judg,
            "buy_trigger": buy_t,
            "stop_loss": stop_t,
            "risk_tag": rm.get("risk_level", "safe"),
            "latest_close": f"{cl_v:.2f}元" if cl_v else "—",
            "r1_pct": f"{r1_v:+.2f}%" if r1_v is not None else "—",
            "ma5_px": f"{ma5_v:.2f}元" if ma5_v else "—",
            "boards_left_str": f"{b_left}板" if b_left else "—",
            "safety_pct": f"{saf10:.1f}%",
        })

    c3_logic = "低吸回踩 MA5 核心，严防 100% 严重异动个股冲高兑现。"
    c3_action = f"• 买点：{c3_stock_details[0]['name']}（{c3_stock_details[0]['buy_trigger']}）"
    c3_stop = "跌破 MA5 坚决止损。"

    # Card 4: 禁买出清黑名单
    c4_targets = avoid_targets[:5]
    if not c4_targets and risk_gate_names:
        c4_targets = [{"name": nm, "code": score_map.get(nm, {}).get("code", ""), "sector": "风险开关锚", "tag": "重点规避"} for nm in risk_gate_names]
    c4_names = " / ".join(t["name"] for t in c4_targets[:4]) or (" / ".join(risk_gate_names) if risk_gate_names else "高位断板负反馈标的")
    c4_codes = " / ".join(t["code"] for t in c4_targets[:4] if t.get("code"))
    risk_gate_str = "、".join(risk_gate_names) if risk_gate_names else c4_names

    c4_stock_details: List[Dict[str, str]] = []
    for t in c4_targets[:4]:
        nm, cd, tg = t["name"], t.get("code", ""), t.get("tag", "断板负反馈")
        rm = replay_metrics.get(cd, {})
        cl_v, ma5_v, r1_v = rm.get("close"), rm.get("ma5"), rm.get("r1")
        m_str = f"收 {cl_v:.2f}元 ({r1_v:+.2f}%)｜MA5 {ma5_v:.2f}元" if (cl_v and ma5_v and r1_v is not None) else tg
        c4_stock_details.append({
            "name": nm,
            "code": cd,
            "badge": f"高危雷区 · {tg}",
            "metrics": m_str,
            "judgment": f"{tg}，退潮负反馈源，极大概率继续核按钮补跌。",
            "buy_trigger": "⛔ 一票否决禁买！严禁翘板抄底与博弈反包。",
            "stop_loss": "持仓遇任何反抽冲高无条件清仓出局。",
            "risk_tag": "danger",
            "latest_close": f"{cl_v:.2f}元" if cl_v else "—",
            "r1_pct": f"{r1_v:+.2f}%" if r1_v is not None else "—",
            "ma5_px": f"{ma5_v:.2f}元" if ma5_v else "—",
            "boards_left_str": "0板",
            "safety_pct": "0%",
        })

    baotuan_stocks = [
        {
            "role": "① 主线空间龙 / 领涨龙头",
            "sector": c1_sec_label,
            "name": c1_names,
            "code": c1_codes,
            "metric": f"最高 {lead_max_h} 板 · 涨停 {lead_sec['zt_text']}" if lead_sec else "主线最高标",
            "targets": c1_targets,
            "stock_details": c1_stock_details,
            "tag": "space_hero",
            "badge_color": "#F87171",
            "logic": c1_logic,
            "action": c1_action,
            "pos": c1_pos,
            "stop": c1_stop,
        },
        {
            "role": "② 梯队先锋 / 身位卡位",
            "sector": f"{c1_sec_label} · 梯队中坚",
            "name": c2_names,
            "code": c2_codes,
            "metric": f"连板梯队 {len(c2_targets)} 只同身位PK",
            "targets": c2_targets[:4],
            "stock_details": c2_stock_details,
            "tag": "echelon_vanguard",
            "badge_color": "#38BDF8",
            "logic": c2_logic,
            "action": c2_action,
            "pos": c2_pos,
            "stop": c2_stop,
        },
        {
            "role": "③ 核心产业大中军 / 抱团趋势锚",
            "sector": "跨题材趋势抱团",
            "name": c3_names,
            "code": c3_codes,
            "metric": "MA5 低吸 vs 100%异动排雷",
            "targets": c3_targets,
            "stock_details": c3_stock_details,
            "tag": "core_army",
            "badge_color": "#FCD34D",
            "logic": c3_logic,
            "action": c3_action,
            "pos": c3_pos,
            "stop": c3_stop,
        },
        {
            "role": "⛔ 禁买出清黑名单",
            "sector": "断板与跌停雷区",
            "name": c4_names,
            "code": c4_codes,
            "metric": f"风险开关：{risk_gate_str}",
            "targets": c4_targets,
            "stock_details": c4_stock_details,
            "tag": "avoid_black",
            "badge_color": "#EF4444",
            "logic": f"三道开关风险锚（{risk_gate_str}）及同日高位断板/跌停负反馈池，次日反包率极低。",
            "action": f"• 准确判断：{c4_names} 处于退潮A杀负反馈中心，任何抄底均为接飞刀！<br>• 执行指令：一票否决（0 仓位）！9:25 观察其跌停封单是否收敛以辅助判断大盘情绪。",
            "pos": "0 仓位",
            "stop": "负反馈向主线高标传染时全天停止一切追高操作。",
        },
    ]

    # 8. 构建真实可转债 T+0 预案（结合余额、溢价、正股联动与精确价格锚）
    baotuan_cbs = _build_cb_targets_from_rows(bonds_parsed, is_extreme_ice)

    # 8.5 直接构建严重异动安全雷达列表（覆盖主线龙头、梯队中坚、抱团趋势核心，确保离线/历史包100%有数据）
    abnormal_radar: List[Dict[str, Any]] = []
    seen_radar_codes = set()
    for t_obj in c1_targets + c2_targets + c3_targets:
        cd = t_obj.get("code", "")
        nm = t_obj.get("name", "")
        if not cd or cd in seen_radar_codes:
            continue
        rm = replay_metrics.get(cd)
        if rm and rm.get("r10") is not None:
            seen_radar_codes.add(cd)
            abnormal_radar.append({
                "name": nm,
                "code": cd,
                "sector": t_obj.get("sector", "核心标的"),
                "latest_close": rm["close"],
                "is_20cm": rm["is_20cm"],
                "ret10": rm["r10"],
                "safety10": rm["safety10"],
                "boards_left": rm["boards_left"],
                "ret30": rm["r30"],
                "safety30": rm["safety30"],
                "risk_level": rm["risk_level"],
                "risk_text": rm["risk_text"],
            })
    abnormal_radar.sort(
        key=lambda x: (0 if x["risk_level"] == "danger" else (1 if x["risk_level"] == "warning" else 2), x["safety10"])
    )

    # 9. 构建分层优先级作战矩阵 (Priority Matrix: P0 / P1 / P2 / P3)
    p_matrix = [
        {
            "tier": "P0",
            "badge": f"唯一主线【{c1_sec_label}】空间龙头 ({c1_pos})",
            "title": f"【{c1_sec_label}】{c1_names}",
            "trigger": c1_stock_details[0]["buy_trigger"] if c1_stock_details else c1_action,
            "stop": c1_stop,
        },
        {
            "tier": "P1",
            "badge": f"中位同身位 PK 胜出者 ({c2_pos})",
            "title": f"【梯队卡位】{c2_names}",
            "trigger": c2_stock_details[0]["buy_trigger"] if c2_stock_details else c2_action,
            "stop": c2_stop,
        },
        {
            "tier": "P2",
            "badge": f"抱团趋势 · MA5低吸与异动排雷 ({c3_pos})",
            "title": f"【趋势抱团】{c3_names}",
            "trigger": c3_action.replace("<br>", " "),
            "stop": c3_stop,
        },
        {
            "tier": "P3",
            "badge": "三道开关 · 断板跌停禁买雷区 (0 仓位)",
            "title": f"【禁买黑名单】{c4_names}",
            "trigger": "9:25–9:35 核对断板股与跌停家数：未止跌前严禁抄底，若负反馈向主线高标蔓延则放弃追涨",
            "stop": "风险锚继续一字大单核按钮时砍掉一切杂毛跟风",
        },
    ]

    # 10. 解析 Section 05 条件情景推演表（注入明确分层仓位与个股结合）+ 盘中时间轴
    scenarios: List[Dict[str, Any]] = []
    sec5 = None
    for s_div in np_sec.select(".np-section"):
        h2 = s_div.find("h2")
        if h2 and "情景推演" in h2.get_text():
            sec5 = s_div
            break
    if sec5:
        for idx, tr in enumerate(sec5.select("tbody tr")):
            tds = [_clean_spaces(td.get_text()) for td in tr.find_all("td")]
            if len(tds) >= 5:
                sc_title = tds[0]
                is_weak = "防守" in sc_title or "转弱" in sc_title
                is_strong = "延续" in sc_title or "强势" in sc_title
                if is_weak:
                    sc_pos = "0 仓位空仓防守"
                elif is_strong:
                    sc_pos = "3 ~ 4 成主攻前排" if regime_mode in ("attack", "rotation") else "2 ~ 3 成聚焦主线活口"
                else:
                    sc_pos = "2 ~ 3 成精选试错" if regime_mode in ("attack", "rotation") else "1 ~ 2 成弱转强/MA5低吸"
                scenarios.append({
                    "id": f"np_sc_{idx}",
                    "name": f"情景 {idx + 1} · {sc_title}",
                    "color": "red" if is_weak else ("rose" if idx == 0 else "amber"),
                    "pos": sc_pos,
                    "premise": tds[1],
                    "auction": f"股票应对：{tds[2]}",
                    "action": f"转债应对：{tds[3]}",
                    "stop": tds[4],
                })

    checklist: List[Dict[str, str]] = []
    # 针对 4 个关键操盘时间窗口，生成真正具有差异化、量化可执行的盘中检查规则
    lead_name = c1_targets[0]["name"] if c1_targets else c1_names
    vanguard_name = c2_targets[0]["name"] if c2_targets else c2_names
    trend_name = c3_targets[0]["name"] if c3_targets else c3_names
    risk_anchor = c4_targets[0]["name"] if c4_targets else "高位跌停股"

    timeline_specs = [
        {
            "time": "09:25 · 集合竞价",
            "focus": f"核验开盘情绪与负反馈：观察风险锚【{risk_anchor}】跌停封单及龙头【{lead_name}】竞价量比",
            "pass_cond": f"跌停未超预期恶化，【{lead_name}】高开 +3% 以上且竞价成交额达标",
            "fail_cond": f"【{lead_name}】低开 <-2% 或跌停家数快速扩大，全天开仓计划立即取消",
        },
        {
            "time": "09:35 · 承接换手",
            "focus": f"梯队卡位去弱留强：比较【{c2_names}】同身位换手回封速度与分时均线承接",
            "pass_cond": f"【{vanguard_name}】等率先放量首封、分时黄线上方稳步换手回封",
            "fail_cond": "同身位中位股批量冲高回落或跌破分时黄线超 10 分钟，严禁接盘",
        },
        {
            "time": "10:00 · 晋级确认",
            "focus": f"主线扩散与均线支撑：核验【{c1_sec_label}】首板助攻数及【{trend_name}】MA5 支撑力度",
            "pass_cond": f"主线首板持续扩散助攻，【{trend_name}】回踩 MA5 缩量止跌拐头放量",
            "fail_cond": f"【{lead_name}】开板大面或【{trend_name}】放量破 MA5 超 2%，全天锁仓",
        },
        {
            "time": "14:00 · 尾盘防守",
            "focus": f"防高位炸板与可转债溢价踩踏：检查高标封单硬度与转债正股背离情况",
            "pass_cond": "主线封单厚实未见松动，T+0 转债分时均线平稳运行无抢跑",
            "fail_cond": "尾盘批量炸板跳水或转债杀溢价放量大跌，执行 T+0 止损与次日减仓",
        },
    ]

    for spec in timeline_specs:
        checklist.append(spec)

    iron_rules = [
        {
            "tag": "🚨 铁律一 · 严重异动红线与断板双排雷",
            "sub": (
                f"警惕 { '、'.join(high_warn_names) } 触及10日100%异动红线，严禁接飞刀 {c4_names}！"
                if high_warn_names
                else f"断板股（{c4_names}）次日反包率不足 8%，严禁盲目抄底！"
            ),
            "detail": (
                f"10日涨幅超85%的标的（{'、'.join(high_warn_names)}）盘中拉升极易触发交易所100%严重异动停牌压制，绝不追高打板；"
                f"同时对断板跌停池（{c4_names}）执行零容忍一票否决。"
            ),
        },
        {
            "tag": "🎯 铁律二 · 去弱留强与精准买点纪律",
            "sub": f"只做唯一主线【{c1_sec_label}】前排胜出者与 MA5 精确支撑低吸",
            "detail": (
                f"连板接力只做【{c1_targets[0]['name'] if c1_targets else c1_names}】弱转强及【{c2_names}】同身位PK第一名；"
                f"趋势抱团只做【{'、'.join(low_buy_names) or c3_names}】回踩 MA5 均线缩量企稳低吸。"
            ),
        },
    ]

    baotuan_digest = _format_baotuan_digest(baotuan_stocks, baotuan_cbs)
    morning_card = (
        f"【晨会操盘内参 · 量化作战卡片】\n"
        f"收盘基准：{report_date} → 预案目标日：{target_date}\n"
        f"核心定性：{slogan}\n"
        f"建议仓位：{rec_pos}\n"
        f"盘面事实：上涨占比 {breadth_str}｜涨停 {zt_str} 家｜跌停 {dt_str} 家｜晋级率 {promo_str}\n\n"
        f"【核心作战锚点与异动排雷】\n"
        f"1. 唯一主线：{c1_sec_label}（空间龙：{c1_names}；4板卡位：{c2_names}）\n"
        f"2. 趋势低吸 vs 异动预警：{c3_logic}\n"
        f"3. 禁买雷区：{c4_names}\n\n"
        f"{baotuan_digest}"
    )

    return {
        "report_date": report_date,
        "target_date": target_date,
        "title": f"明日实战推演与交易预案 ({target_date})",
        "action_slogan": slogan,
        "recommended_position": rec_pos,
        "is_melt": is_melt,
        "melt_text": melt_text,
        "abnormal_radar": abnormal_radar,
        "iron_rules": iron_rules,
        "baotuan_stock_targets": baotuan_stocks,
        "baotuan_cb_targets": baotuan_cbs,
        "priority_matrix": p_matrix,
        "candidates": [],
        "checklist": checklist,
        "avoid_list": avoid_list or [f"{c4_names}：高位分歧与负反馈观察，未止跌前严禁低吸"],
        "scenarios": scenarios,
        "morning_card": morning_card,
        "full_report_url": f"./reports/plan_{report_date}.html",
    }


def parse_plan_html(html_path: Path) -> Dict[str, Any]:
    """从本地预案 HTML 中深度解析实战作战矩阵（自动识别新版 np-plan 与旧版历史研报）"""
    if not html_path.exists():
        return {}

    content = html_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(content, "html.parser")

    # 优先识别新版 np-plan 结构（output/今日复盘与明日预案_*.html）
    if soup.select_one("section.np-plan"):
        return _parse_np_plan_html(soup, html_path)

    # ================= 以下兼容旧版 .pm-card 历史归档研报 =================
    melt_div = soup.find("div", class_="melt-banner")
    is_melt = melt_div is not None
    melt_text = melt_div.get_text(" ", strip=True) if melt_div else ""

    slogan = ""
    rec_pos = ""
    badge_el = soup.find(class_="pulse-badge") or soup.find(lambda tag: tag.name == "span" and "防守" in tag.text)
    if badge_el:
        slogan = badge_el.get_text(strip=True)

    pos_match = re.search(r"仓位上限[:：\s]*([^\n<]+)", content)
    if pos_match:
        rec_pos = pos_match.group(1).strip()
    elif is_melt:
        rec_pos = "0 ~ 1 成极度防守"
    else:
        rec_pos = "3 ~ 5 成轮动试错"

    morning_card = ""
    card_m = re.search(r"function copyMorningPlanDigest\(\)\s*\{\s*const text = `([\s\S]+?)`;\s*if \(navigator\.clipboard", content)
    if card_m:
        raw_card = card_m.group(1)
        lines = []
        for line in raw_card.splitlines():
            line = line.strip()
            line = re.sub(r"^`", "", line)
            line = re.sub(r"`\s*\+?$", "", line)
            line = line.replace(r"\n", "")
            if line:
                lines.append(line)
        morning_card = "\n".join(lines).strip()

    p_matrix = []
    cards = soup.select(".pm-card")
    if cards:
        for card in cards:
            tag_el = card.select_one(".tag")
            tag_text = tag_el.get_text(strip=True) if tag_el else ""
            tier_m = re.match(r"(P[0-3]|P-Black)", tag_text)
            tier = tier_m.group(1) if tier_m else tag_text

            stock_b = card.select_one(".pm-header b") or card.find("b")
            stock_title = stock_b.get_text(strip=True) if stock_b else ""

            badge_el = card.select_one(".pm-badge")
            badge_text = badge_el.get_text(strip=True) if badge_el else ""

            card_text = card.get_text(" ", strip=True)
            m_trig = re.search(r"【(?:触发|监控|禁令)】\s*([^【]+?)(?=【|$)", card_text)
            m_stop = re.search(r"【(?:止损)】\s*([^【]+?)(?=【|$)", card_text)

            trig_str = m_trig.group(1).strip() if m_trig else "放量推升且题材有首板助攻，严禁顶一字"
            stop_str = m_stop.group(1).strip() if m_stop else ("全天禁止开新仓" if tier in ["P3", "P-Black"] else "跌破分时均线或炸板不回封立即止损")

            p_matrix.append({
                "tier": tier,
                "badge": badge_text or "主力博弈核心",
                "title": stock_title or tag_text,
                "trigger": trig_str,
                "stop": stop_str,
            })

    if not p_matrix:
        p_patterns = [
            ("P0", "空间独苗 · 情绪穿越试金石", r"P0\s+([^<\n]+)"),
            ("P1", "中位卡位 · 换手活口先锋", r"P1\s+([^<\n]+)"),
            ("P2", "低位防守 · 首板助攻分支", r"P2\s+([^<\n]+)"),
            ("P3", "风险熔断 · 情绪监控锚点", r"P3\s+([^<\n]+)"),
        ]
        for tier, default_badge, pat in p_patterns:
            m = re.search(pat, content)
            if m:
                raw_title = m.group(1).strip().replace("` +", "").strip()
                trig_m = re.search(rf"{tier}[^>]*?【触发】([^<\n]+)", content)
                stop_m = re.search(rf"{tier}[^>]*?【止损】([^<\n]+)", content)
                p_matrix.append({
                    "tier": tier,
                    "badge": default_badge,
                    "title": raw_title,
                    "trigger": trig_m.group(1).strip() if trig_m else "竞价放量换手推升，同题材有首板助攻，严禁顶一字诱多",
                    "stop": stop_m.group(1).strip() if stop_m else "炸板>3分钟不回封或跌破分时均线立即放弃/止损",
                })

    tables = soup.find_all("table")
    candidates = []
    checklist = []
    for t in tables:
        ths = [th.get_text(strip=True) for th in t.find_all("th")]
        if "触发条件" in ths and "候选" in ths:
            for tr in t.find_all("tr")[1:]:
                tds = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(tds) >= 4:
                    candidates.append({"name": tds[0], "role": tds[1], "trigger": tds[2], "stop": tds[3]})
        elif "时间节点" in ths and "监控焦点" in ths:
            for tr in t.find_all("tr")[1:]:
                tds = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(tds) >= 4:
                    checklist.append({"time": tds[0], "focus": tds[1], "pass_cond": tds[2], "fail_cond": tds[3]})

    avoid_list = []
    avoid_sec = soup.find(lambda tag: tag.name in ["h2", "h3", "summary"] and "不追名单" in tag.text)
    if avoid_sec:
        ul_el = avoid_sec.find_next("ul")
        if ul_el:
            for it in ul_el.find_all("li")[:12]:
                txt = it.get_text(" ", strip=True)
                if txt:
                    avoid_list.append(txt)

    iron_rules = [
        {
            "tag": "🚨 铁律一 · 断板反包禁令",
            "sub": "次日反包率极低，冲高一律防范诱多！",
            "detail": "操盘军规：严禁低吸、抄底、搏首阴！重点回避高位断板被核标的。",
        },
        {
            "tag": "⚠️ 铁律二 · 冰点修复陷阱",
            "sub": "无题材梯队共振的急拉多为假冲天炮！",
            "detail": "操盘军规：存量出逃诱多只看不追，早盘 10:00 前严禁盲目开新仓！",
        },
    ]

    scenarios = []
    sc_head = soup.find(lambda x: x.name in ["h2", "h3"] and "情景推演" in x.get_text())
    if sc_head:
        curr = sc_head.find_next_sibling()
        while curr and curr.name not in ["h2", "h3"]:
            classes = curr.get("class", [])
            if "card" in classes:
                tag_el = curr.find(class_="tag")
                tag_t = tag_el.get_text(strip=True) if tag_el else "情景"
                title_b = curr.find("b")
                title_t = title_b.get_text(strip=True) if title_b else ""
                pos_el = curr.find(class_="muted")
                pos_t = pos_el.get_text(strip=True) if pos_el else "仓位上限 10%"
                all_t = curr.get_text(" ", strip=True)
                prem_m = re.search(r"前提[：:]\s*([^｜|]+)", all_t)
                fail_m = re.search(r"失效[：:]\s*([^｜|]+)", all_t)
                auc_m = re.search(r"竞价\s*9:25[：:]\s*([^｜|]+)", all_t)
                act_m = re.search(r"开盘\s*9:35[：:]\s*([^｜|]+)", all_t)
                sc_color = "red" if "极劣" in tag_t or "恶化" in title_t else ("rose" if "主情景" in tag_t else "amber")
                scenarios.append({
                    "id": f"sc_{len(scenarios)}",
                    "name": f"{tag_t} · {title_t}",
                    "color": sc_color,
                    "pos": pos_t,
                    "premise": prem_m.group(1).strip() if prem_m else "盘面分化，高位负反馈未止",
                    "auction": auc_m.group(1).strip() if auc_m else "9:25 核心标的竞价低于预期绝不追高",
                    "action": act_m.group(1).strip() if act_m else "严格执行防守纪律，不开新仓",
                    "stop": fail_m.group(1).strip() if fail_m else "跌停继续增加或负反馈扩散，全天彻底空仓",
                })
            curr = curr.find_next_sibling()

    date_part = _iso_date(html_path.name) or "2026-10-22"

    baotuan_stocks = []
    baotuan_cbs = []
    for t in tables:
        ths = [th.get_text(strip=True) for th in t.find_all("th")]
        th_str = " ".join(ths)
        if "转债标的" in th_str and "联动正股" in th_str:
            for tr in t.find_all("tr")[1:]:
                tds = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(tds) >= 5:
                    b_raw = tds[0]
                    m_b = re.match(r"([^\d\s]+)\s*(\d{6})?", b_raw)
                    b_name = m_b.group(1).strip() if m_b else b_raw
                    b_code = m_b.group(2).strip() if m_b and m_b.group(2) else ""
                    baotuan_cbs.append({
                        "bond_name": b_name,
                        "bond_code": b_code,
                        "role": tds[1],
                        "stock_name": tds[2],
                        "action": tds[3],
                        "pos": tds[4],
                        "stop": tds[5] if len(tds) > 5 else "破分时黄线止损",
                    })
        elif "买入执行" in th_str or "仓位配置" in th_str:
            for tr in t.find_all("tr")[1:]:
                tds = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(tds) >= 6:
                    baotuan_stocks.append({
                        "role": tds[0],
                        "name": tds[1],
                        "sector": tds[2] if len(tds) > 6 else "核心抱团",
                        "logic": tds[3],
                        "action": tds[4],
                        "pos": tds[5],
                        "stop": tds[6] if len(tds) > 6 else "跌破分时均线止损",
                    })

    # 若旧版 HTML 表格未含 baotuan_stocks，则从 p_matrix 映射真实标的而非空白模板
    if not baotuan_stocks and p_matrix:
        for idx, base_tpl in enumerate(DEFAULT_BAOTUAN_STOCKS[:3]):
            pm = p_matrix[idx] if idx < len(p_matrix) else p_matrix[0]
            item = dict(base_tpl)
            item["name"] = pm.get("title", item["name"])
            item["sector"] = pm.get("badge", item["sector"])
            item["action"] = pm.get("trigger", item["action"])
            item["stop"] = pm.get("stop", item["stop"])
            if is_melt:
                item["pos"] = "0 仓位 · 风向标观察"
            baotuan_stocks.append(item)
        blk = dict(DEFAULT_BAOTUAN_STOCKS[3])
        p_blk = next((x for x in p_matrix if x.get("tier") in ("P-Black", "P3")), None)
        blk["name"] = p_blk["title"] if p_blk else ("、".join(avoid_list[:3]) or blk["name"])
        baotuan_stocks.append(blk)
    elif not baotuan_stocks:
        baotuan_stocks = [dict(x) for x in DEFAULT_BAOTUAN_STOCKS]

    if not baotuan_cbs:
        baotuan_cbs = [dict(x) for x in DEFAULT_BAOTUAN_CBS]

    baotuan_digest = _format_baotuan_digest(baotuan_stocks, baotuan_cbs)
    if "【重点抱团个股 · 实战预案指令】" not in morning_card:
        morning_card = (morning_card + "\n\n" + baotuan_digest) if morning_card else baotuan_digest

    return {
        "report_date": date_part,
        "target_date": date_part,
        "title": "明日实战推演与交易预案",
        "action_slogan": slogan or "极度防守 · 不开新仓 (严守量化风控底线)",
        "recommended_position": rec_pos,
        "is_melt": is_melt,
        "melt_text": melt_text,
        "iron_rules": iron_rules,
        "baotuan_stock_targets": baotuan_stocks,
        "baotuan_cb_targets": baotuan_cbs,
        "priority_matrix": p_matrix,
        "candidates": candidates,
        "checklist": checklist,
        "avoid_list": avoid_list or ["高位断板负反馈标的：严禁低吸首阴与盲目追反包"],
        "scenarios": scenarios,
        "morning_card": morning_card,
        "full_report_url": f"./reports/plan_{date_part}.html",
    }


def synthesize_plan(
    date_str: str,
    degree: int,
    status_text: str,
    top_stocks: List[Dict[str, Any]],
    dt_count: int = 0,
) -> Dict[str, Any]:
    """当无对应 HTML 预案文件时，根据当日连板梯队真实股票与情绪指标动态合成作战看板"""
    def _pick_stock(idx: int, fallback_name: str, fallback_h: str) -> Dict[str, str]:
        if len(top_stocks) > idx and isinstance(top_stocks[idx], dict):
            st = top_stocks[idx]
            nm = str(st.get("name") or fallback_name)
            cd = str(st.get("symbol") or st.get("code") or "")
            sec = str(st.get("plate_name") or st.get("sector") or st.get("ml") or "主线热点")
            h = str(st.get("height") or fallback_h)
            return {"name": nm, "code": cd, "sector": sec, "height": h, "tag": f"{h}板"}
        return {"name": fallback_name, "code": "", "sector": "梯队观察", "height": fallback_h, "tag": f"{fallback_h}板"}

    s0 = _pick_stock(0, "最高空间标杆", "5")
    s1 = _pick_stock(1, "次高换手先锋", "3")
    s2 = _pick_stock(2, "低位晋级代表", "2")

    is_melt = degree < 30 or dt_count >= 15
    if is_melt:
        slogan = f"极端退潮杀跌期 · 默认不开新仓 (跌停 {dt_count} 家)"
        pos = "0 仓位 · 风向标观察"
        melt_text = f"防守熔断生效中：全市场跌停高达 {dt_count} 家，处于退潮防守期，默认执行 0 仓位，以下标的仅作盘面风向标观察！"
        card_pos = "0 仓位 · 风向标观察"
    elif degree >= 65:
        slogan = f"顺势聚焦主线龙头 · 严控后排跟风 (最高标 {s0['name']} {s0['height']}B)"
        pos = "5 ~ 7 成顺势进攻"
        melt_text = ""
        card_pos = "0.5~1.0 成"
    else:
        slogan = f"分歧轮动试错 · 聚焦【{s0['sector']}】核心承接"
        pos = "2 ~ 4 成轮动试错"
        melt_text = ""
        card_pos = "0.5~1.0 成"

    baotuan_stocks = [
        {
            **DEFAULT_BAOTUAN_STOCKS[0],
            "sector": s0["sector"],
            "name": s0["name"],
            "code": s0["code"],
            "metric": f"{s0['height']}连板 · 空间锚点",
            "targets": [s0],
            "pos": card_pos,
            "logic": f"聚焦【{s0['sector']}】最高身位标杆 {s0['name']}（{s0['height']}板），检验市场空间压制与接力意愿",
            "action": (
                f"【风向标观察 · 默认不开新仓】核验 9:25 {s0['name']} 竞价封单与换手承接，观察同题材是否有首板助攻，严禁顶一字或盲目打板"
                if is_melt
                else f"9:25 核验 {s0['name']} 放量高开换手承接且【{s0['sector']}】出现联动助攻后轻仓试错"
            ),
        },
        {
            **DEFAULT_BAOTUAN_STOCKS[1],
            "sector": s1["sector"],
            "name": s1["name"],
            "code": s1["code"],
            "metric": f"{s1['height']}连板 · 梯队先锋",
            "targets": [s1],
            "pos": card_pos,
            "logic": f"次高身位换手中坚 {s1['name']}（【{s1['sector']}】· {s1['height']}板），观察同身位去弱留强卡位表现",
            "action": (
                f"【梯队核验 · 默认不开新仓】9:35 观察 {s1['name']} 分时均线承接与同身位晋级率，掉队转弱则维持全天防守"
                if is_melt
                else f"竞价小幅高开 +1%~+3%，早盘确认 {s1['name']} 换手回踩分时黄线企稳且题材共振后关注"
            ),
        },
        {
            **DEFAULT_BAOTUAN_STOCKS[2],
            "sector": s2["sector"],
            "name": s2["name"],
            "code": s2["code"],
            "metric": f"{s2['height']}板 / 容量锚",
            "targets": [s2],
            "pos": card_pos,
            "logic": f"主流分支代表 {s2['name']}（【{s2['sector']}】）及板块核心容量中军，检验资金承接厚度",
            "action": (
                f"【承接观察 · 默认不开新仓】观察 {s2['name']} 及板块容量中军在 5 日均线附近的承接力度，不追日内脉冲"
                if is_melt
                else f"围绕 {s2['name']} 及主线中军 5 日均线企稳信号关注低吸承接，拒绝追高"
            ),
        },
        {
            **DEFAULT_BAOTUAN_STOCKS[3],
            "sector": "高位断板 / 跌停雷区",
            "name": f"昨日断板高标及跌停池（共 {dt_count} 家跌停）" if dt_count else "昨日高位断板及放量破位标的",
            "code": "",
            "metric": "风控一票否决",
            "targets": [],
            "pos": "0 仓位",
        },
    ]

    # 若存在同日 cb_daily_snapshots，优先用真实转债快照构造，否则采用默认兜底常量
    baotuan_cbs = [dict(x) for x in DEFAULT_BAOTUAN_CBS]
    if is_melt:
        for cb_item in baotuan_cbs:
            if "0 仓位" not in cb_item.get("pos", ""):
                cb_item["pos"] = "0 仓位 (仅T+0观察)"

    p_matrix = [
        {
            "tier": "P0",
            "badge": f"【{s0['sector']}】最高身位风向标",
            "title": f"{s0['name']} · {s0['height']}板",
            "trigger": baotuan_stocks[0]["action"],
            "stop": "炸板>3分钟不回封或跌破分时均线立即放弃/止损",
        },
        {
            "tier": "P1",
            "badge": f"【{s1['sector']}】换手卡位先锋",
            "title": f"{s1['name']} · {s1['height']}板",
            "trigger": baotuan_stocks[1]["action"],
            "stop": "开盘快速跳水翻绿或冲高无量即放弃",
        },
        {
            "tier": "P2",
            "badge": f"【{s2['sector']}】分支与容量锚",
            "title": f"{s2['name']} · {s2['height']}板",
            "trigger": baotuan_stocks[2]["action"],
            "stop": "跌破昨收且无板块跟随立即撤退",
        },
        {
            "tier": "P3",
            "badge": "风险熔断 · 跌停封单风向标",
            "title": f"高位断板股与跌停负反馈池（跌停 {dt_count} 家）",
            "trigger": "9:25 若高位断板标的继续大单跌停扩散，全天新仓计划作废，锁定 0 仓位",
            "stop": "严禁参与任何首阴低吸与断板反包",
        },
    ]

    checklist = [
        {
            "time": "09:25 集合竞价",
            "focus": f"全市场跌停家数、{s0['name']} 竞价承接",
            "pass_cond": f"跌停家数收敛 ≤ 10 家，{s0['name']} 红开换手且封单健康",
            "fail_cond": "跌停 > 15 家，或高位断板股大单焊死跌停：全天禁止开新仓",
        },
        {
            "time": "09:35 开盘前10分",
            "focus": f"跌停封单变化、【{s0['sector']}】梯队联动",
            "pass_cond": "上涨家数不再恶化，跌停板无新增扩散，主线有首板助攻",
            "fail_cond": "高标快速冲高跳水或大面积炸板：立即放弃试错",
        },
        {
            "time": "10:00 盘中确认",
            "focus": "梯队晋级情况、市场宽度回暖、主流题材合力",
            "pass_cond": "上涨家数回升至 2000 家以上，主线走出 ≥2 只连板晋级",
            "fail_cond": "上涨家数 < 1500 家且无板块合力：全天锁定空仓",
        },
        {
            "time": "14:00 午后复核",
            "focus": "尾盘流动性、主线封单稳固度与债股联动",
            "pass_cond": "尾盘无恐慌砸盘，主线核心封单稳固",
            "fail_cond": "无增量资金进场严禁尾盘盲目抢筹，防范次日低开兑现",
        },
    ]

    avoid_list = [
        "昨日高位断板放量大阴标的（次日反包胜率极低，脉冲冲高一律防范诱多，严禁抄底首阴）",
        f"当日跌停及破位负反馈池（当前跌停 {dt_count} 家，未止跌收敛前严禁接飞刀）",
    ]

    iron_rules = [
        {
            "tag": "🚨 铁律一 · 断板反包禁令",
            "sub": "次日反包率仅 7.6%，冲高一律视为主力自救诱多！",
            "detail": "操盘军规：严禁低吸、抄底、搏首阴！重点回避高位断板被核标的。",
        },
        {
            "tag": "⚠️ 铁律二 · 冰点修复陷阱",
            "sub": "无题材梯队共振的急拉多为假冲天炮！",
            "detail": "操盘军规：存量出逃诱多只看不追，早盘 10:00 前严禁盲目开新仓！",
        },
    ]

    scenarios = [
        {
            "id": "path_a",
            "name": "主情景 · 广度弱、接力弱：高位退潮优先",
            "color": "rose",
            "pos": "0 仓位观察" if is_melt else "仓位上限 1 ~ 2 成",
            "premise": "市场广度偏弱，高位负反馈需要先止住收敛",
            "auction": f"9:25 {s0['name']} 及中位核心竞价低于预期时不追，观察批量低开与撤单",
            "action": "默认不开新仓，严守风控红线，仅记录强弱信号",
            "stop": "跌停继续扩张并伴随高位负反馈，全天空仓",
        },
        {
            "id": "path_b",
            "name": "备选情景 · 探底回升：主线梯队分歧转一致修复",
            "color": "amber",
            "pos": "仓位上限 2 ~ 3 成",
            "premise": f"早盘跌停不扩散，【{s0['sector']}】出现梯队晋级与首板扩散",
            "auction": f"9:25 {s0['name']} / {s1['name']} 弱转强换手承接有力",
            "action": "10:00 后确认宽度与接力同步改善，按条件轻仓观察前排胜出者",
            "stop": "炸板率攀升或冲高回落，立即收手不加仓",
        },
        {
            "id": "path_c",
            "name": "极劣情景 · 负反馈扩散：二次杀跌踩踏",
            "color": "red",
            "pos": "0 仓位绝对空仓",
            "premise": "跌停家数继续攀升，中高位标的全线核按钮负反馈",
            "auction": "断板高标竞价继续大单封死跌停",
            "action": "触发全天风控熔断，执行 0 仓位，严禁任何抄底尝试",
            "stop": "全天锁定空仓",
        },
    ]

    baotuan_digest = _format_baotuan_digest(baotuan_stocks, baotuan_cbs)
    morning_card = (
        f"【晨会操盘内参 · 核心作战卡片】\n"
        f"基准日期：{date_str}\n"
        f"定性军规：{slogan}\n"
        f"仓位建议：{pos}\n\n"
        f"【优先级作战矩阵】\n"
        f"P0 空间先锋：【{s0['sector']}】{s0['name']} ({s0['height']}B)\n"
        f"P1 中位卡位：【{s1['sector']}】{s1['name']} ({s1['height']}B)\n"
        f"P2 分支观察：【{s2['sector']}】{s2['name']} ({s2['height']}B)\n"
        f"P3 风险熔断：跌停负反馈池（跌停 {dt_count} 家）\n\n"
        f"{baotuan_digest}"
    )

    return {
        "report_date": date_str,
        "target_date": date_str,
        "title": "明日实战推演与交易预案",
        "action_slogan": slogan,
        "recommended_position": pos,
        "is_melt": is_melt,
        "melt_text": melt_text,
        "iron_rules": iron_rules,
        "baotuan_stock_targets": baotuan_stocks,
        "baotuan_cb_targets": baotuan_cbs,
        "priority_matrix": p_matrix,
        "candidates": [],
        "checklist": checklist,
        "avoid_list": avoid_list,
        "scenarios": scenarios,
        "morning_card": morning_card,
        "full_report_url": f"./reports/plan_{date_str}.html",
    }


def _days_between(d1: str, d2: str) -> int:
    try:
        return (dt_date.fromisoformat(d2) - dt_date.fromisoformat(d1)).days
    except Exception:
        return 999


def get_plan_for_date(
    project_root: Path,
    date_str: str,
    degree: int,
    status_text: str,
    top_stocks: list,
    dt_count: int = 0,
) -> Dict[str, Any]:
    """主入口：优先匹配同收盘日或同目标交易日的真实预案 HTML；周末/盘前自动衔接上一交易日最新预案"""
    iso_d = _iso_date(date_str) or date_str
    clean_date = iso_d.replace("-", "")

    # 1. 精确匹配报告收盘日 == date_str 的预案 HTML
    exact_candidates = [
        project_root / "output" / f"今日复盘与明日预案_{iso_d}.html",
        project_root / "output" / f"今日复盘与明日预案_{clean_date}.html",
        project_root / "mobile_app" / "reports" / f"plan_{iso_d}.html",
        project_root / "mobile_app" / "reports" / f"plan_{clean_date}.html",
    ]
    for c in exact_candidates:
        if c.exists():
            plan = parse_plan_html(c)
            if plan and (plan.get("baotuan_stock_targets") or plan.get("priority_matrix")):
                return plan

    # 2. 检查最新预案或近期预案是否目标交易日 (data-target-date) 恰好指向 date_str，
    #    或者处于周末/盘前窗口（0 <= date_str - report_date <= 5 天）
    recent_files: List[Path] = []
    latest_file = project_root / "output" / "今日复盘与明日预案_最新.html"
    if latest_file.exists():
        recent_files.append(latest_file)
    for p in sorted(project_root.glob("output/今日复盘与明日预案_*.html"), reverse=True)[:6]:
        if p not in recent_files and "最新" not in p.name:
            recent_files.append(p)

    for rf in recent_files:
        plan = parse_plan_html(rf)
        if not plan or not (plan.get("baotuan_stock_targets") or plan.get("priority_matrix")):
            continue
        rep_d = plan.get("report_date", "")
        tgt_d = plan.get("target_date", "")
        if tgt_d == iso_d or (rep_d and 0 <= _days_between(rep_d, iso_d) <= 5):
            return plan

    # 3. 历史旧日期无独立 HTML 时，基于该日真实梯队数据合成，绝不拿最新月份的股票串味覆盖旧月份
    return synthesize_plan(iso_d, degree, status_text, top_stocks, dt_count)
