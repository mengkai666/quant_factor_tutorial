"""使用同日审计和研究缓存刷新本地抱团预案，不运行全市场流水线。

仅在 output/ 下生成静态页面和刷新清单，不抓行情、不追加交易留痕、
不发送邮件、不部署站点。已有主报告的日期不符时保留原文件。
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
from html import escape
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _date(value):
    value = str(value or "").strip()
    pattern = "%Y%m%d" if re.fullmatch(r"\d{8}", value) else "%Y-%m-%d"
    return datetime.strptime(value, pattern).date().isoformat()


def _report_date(document):
    """核对主报告日期，避免把内嵌研究卡日期当成整份报告日期。"""
    meta = re.search(r'<meta\b(?=[^>]*\bname=["\']report-date["\'])(?=[^>]*\bcontent=["\']([^"\']+)["\'])[^>]*>', document, re.I)
    integrity = re.search(r'<script\b[^>]*\bid=["\']report-integrity["\'][^>]*>(.*?)</script>', document, re.I | re.S)
    clocks = []
    if meta:
        clocks.append(_date(meta.group(1)))
    if integrity:
        payload = json.loads(integrity.group(1))
        clocks.append(_date(payload.get("report_date")))
        if payload.get("market_date"):
            clocks.append(_date(payload["market_date"]))
    if not clocks:
        return None
    if len(set(clocks)) != 1:
        raise ValueError("主报告日期与行情日期不一致，未改写")
    return clocks[0]


def _replace_block(document, marker, replacement):
    start, end = f"<!-- {marker}:start -->", f"<!-- {marker}:end -->"
    starts, ends = document.count(start), document.count(end)
    if starts == ends == 0:
        return document, False
    if starts != 1 or ends != 1 or document.index(start) > document.index(end):
        raise ValueError(f"页面的 {marker} 边界重复或不完整，未改写")
    left, right = document.index(start), document.index(end) + len(end)
    return document[:left] + replacement + document[right:], True


def _heading_ids(fragment, prefix):
    """重建目录前为新标题分配稳定锚点，避免与原章节编号冲突。"""
    number = 0

    def replace(match):
        nonlocal number
        if re.search(r'\bid\s*=', match.group(0), re.I):
            return match.group(0)
        number += 1
        return match.group(0)[:-1] + f' id="{prefix}-{number}">'

    return re.sub(r'<h[2-4]\b[^>]*>', replace, fragment, flags=re.I)


def _refresh_legacy_hub(document, plan):
    """更新本次涉及的预案与抱团入口，移除旧日期结论和失效功能承诺。"""
    marker = "<!-- 卡片 5: 可转债抱团与新周期追踪作战室 -->"
    end = "<!-- war-room-hub:end -->"
    if document.count(marker) == 1 and document.count(end) == 1:
        left, right = document.index(marker), document.index(end)
        card = document[left:right]
        for old, new in [
            ("🤖 机器人转债集群主升", "股债联动与溢价观察"),
            ("澳弘破位出清", "同日证据与失效条件"),
            ("澳弘转债天量破位杀跌出清(-5.5%，正股-8.18%) ➔ 机器人转债集群(奥普/豪美/丰茂/震裕02)全线接力爆发。正股涨停板上溢出、T+0高频做T与量化个股筛选。",
             "比较同日债股表现、交易日区间收益、转股溢价与公告条款。观察条件需要盘中核验；活跃成交不代表已确认的交易机会。"),
        ]:
            card = card.replace(old, new)
        document = document[:left] + card + document[right:]
    marker = "<!-- 卡片 1: 今日复盘与明日预案 -->"
    if plan and document.count(marker) == 1:
        left = document.index(marker)
        next_card = document.find("<!-- 卡片 2:", left)
        if next_card != -1:
            card = document[left:next_card]
            decision = plan.get("decision") or {}
            action = "已通过资格，仍须确认条件" if decision.get("execution_allowed") else "当前不开新仓，等待确认"
            card = re.sub(r'仓位上限\s*[^<]*', escape(action), card, count=1)
            card = card.replace("集合竞价信标 (09:25) · 盘中应变分支 (P0~P3) · 禁买雷区与一键复制晨会卡片。",
                                "板块、股票与转债同日分析；延续、分歧、退潮三种情景；9:25 / 9:35 / 10:00 / 午后逐项核验。")
            document = document[:left] + card + document[next_card:]
    document = document.replace("旧抱团瓦解出清 vs 新周期试错竞争。连板总龙身位晋级、监管周期阶梯、近岸蛋白/善水科技等趋势候选证据核验与证伪。",
                                "核验同日核心、旧团分歧与新候选的证据；跟踪价格延续、板块扩散、监管条款和失效条件。")
    return document


def update_document(document, *, report_date, panel, cb_card, is_site_mode=False, plan=None):
    """先在内存形成可核验的新页面，拒绝更新其他日期的报告。"""
    from research_subpages import add_section_toc

    day = _date(report_date)
    if _report_date(document) != day:
        raise ValueError("主报告不是指定数据日，未改写")
    panel = _heading_ids(panel, "crowding-plan-heading")
    cb_card = _heading_ids(cb_card, "cb-plan-heading")
    if "<!-- crowding-plan-panel:start -->" not in panel:
        raise ValueError("新预案缺少可重复更新的边界")
    updated, replaced = _replace_block(document, "crowding-plan-panel", panel)
    if not replaced:
        placeholder = "<!-- trusted-crowding-plan -->"
        if updated.count(placeholder) == 1:
            updated = updated.replace(placeholder, panel, 1)
        else:
            anchor = "<!-- daily-fupan-continuous:end -->"
            if updated.count(anchor) != 1:
                raise ValueError("主报告缺少唯一的复盘插入位置，未改写")
            updated = updated.replace(anchor, anchor + "\n" + panel, 1)
    updated, replaced = _replace_block(updated, "cb-tracking-teaser", cb_card)
    if not replaced:
        anchor = "<!-- crowding-plan-panel:end -->"
        updated = updated.replace(anchor, anchor + "\n" + cb_card, 1)
    updated = _refresh_legacy_hub(updated, plan)
    updated = add_section_toc(updated)
    if _report_date(updated) != day:
        raise ValueError("刷新后的主报告日期发生变化")
    return updated


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def refresh(report_date, *, output_dir=None, data_dir=None):
    import crowding_next_day_plan as ndp
    import cb_baotuan_report as cb

    day = _date(report_date)
    output = Path(output_dir or ROOT / "output").resolve()
    data = Path(data_dir or ROOT / "data").resolve()
    context = ndp.load_plan_context(day, output_dir=str(output))
    if _date(context.get("report_date")) != day:
        raise ValueError("审计上下文日期不匹配")
    plan = ndp.build_next_day_plan(context, echelon=(context.get("decision_context") or {}).get("echelon"),
                                 output_dir=str(output), data_dir=str(data))
    pending, skipped = [], []
    for relative, site_mode, prefix in [("主线强度追踪.html", False, ""),
                                       ("site/latest.html", True, ""),
                                       (f"site/reports/{day}.html", True, "../")]:
        path = output / relative
        if not path.exists():
            skipped.append({"path": str(path), "reason": "不存在"})
            continue
        original = path.read_bytes()
        document = original.decode("utf-8-sig")
        clock = _report_date(document)
        if clock != day:
            skipped.append({"path": str(path), "reason": "数据日期不同或缺失", "report_date": clock})
            continue
        panel = ndp.render_plan_panel_html(plan, is_site_mode=site_mode)
        # 站点首页与按日归档位于不同层级，导航使用各自的相对路径。
        if relative == "site/latest.html":
            panel = panel.replace('href="../reports/', 'href="reports/').replace('href="../index.html"', 'href="index.html"')
        card = cb.render_cb_teaser_card_html(day, is_site_mode=site_mode, prefix=prefix,
                                            output_dir=str(output), data_dir=str(data))
        updated = update_document(document, report_date=day, panel=panel, cb_card=card,
                                  is_site_mode=site_mode, plan=plan).encode("utf-8")
        pending.append((path, original, updated))

    # 所有既有主报告均已完成渲染与边界检查，随后才写出页面。
    plan_files = ndp.export_plan_standalone_reports(day, output_dir=str(output), site_dir=str(output / "site"), plan_data=plan)
    cb_files = cb.export_cb_tracking_reports(day, output_dir=str(output), site_dir=str(output / "site"), data_dir=str(data))
    backup_dir = output / "plan_refresh_backups" / day
    changed = []
    for path, before, after in pending:
        if before == after:
            continue
        if path.read_bytes() != before:
            raise RuntimeError(f'刷新期间页面被其他进程修改，已停止覆盖：{path}')
        relative = path.relative_to(output)
        backup = backup_dir / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            backup.write_bytes(before)
        temporary = path.with_name(path.name + ".plan-refresh.tmp")
        temporary.write_bytes(after)
        temporary.replace(path)
        changed.append({"path": str(path), "backup": str(backup), "before_sha256": _sha(before), "after_sha256": _sha(after)})
    result = {"report_date": day, "target_trade_date": plan.get("target_date"),
              "execution_allowed": (plan.get("decision") or {}).get("execution_allowed"),
              "mode": "local_static_refresh", "network_requested": False, "journal_appended": False,
              "plan_files": plan_files, "cb_files": cb_files, "changed_main_reports": changed, "skipped": skipped}
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / f"crowding_plan_refresh_{day}.json"
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["manifest"] = str(manifest)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="收盘数据日期 YYYY-MM-DD")
    parser.add_argument("--output-dir")
    parser.add_argument("--data-dir")
    args = parser.parse_args(argv)
    print(json.dumps(refresh(args.date, output_dir=args.output_dir, data_dir=args.data_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
