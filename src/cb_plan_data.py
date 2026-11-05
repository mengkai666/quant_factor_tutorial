"""只读、绑定基准日期的可转债预案数据。

ready 仅代表展示字段齐全，不代表交易许可。
联网与写入快照只由显式刷新工具执行。
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("QF_DATA_DIR") or ROOT / "data")
OUTPUT_DIR = ROOT / "output"
SCHEMA = "cb-daily-snapshot/v1"
NUMERIC_FIELDS = ("close", "r1", "r5", "r10", "r20", "amount_yi", "premium_pct", "remaining_issue_yi", "stock_r1")


def normalize_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        return dt.date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def is_closing_quote_time(value: Any) -> bool:
    """仅接受明确且有效的收盘时段报价时间。"""
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", text):
        return False
    try:
        return dt.time.fromisoformat(text) >= dt.time(15)
    except ValueError:
        return False


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _code(value: Any) -> str:
    text = re.sub(r"^(sh|sz|bj)", "", str(value or ""), flags=re.I)
    return text if re.fullmatch(r"\d{6}", text) else ""


def _text(value: Any) -> str:
    return re.sub(r"<[^>]*>", "", str(value or "")).strip()


def _empty(day: str | None, warning: str, status: str = "unavailable") -> dict:
    return {"status": status, "as_of": day, "summary": {}, "rows": [], "warnings": [warning], "sources": []}


def describe_row(row: dict) -> dict:
    """从债股价格表现归纳观察条件，成交额排名不决定风险等级。"""
    r1, stock_r1 = row.get("r1"), row.get("stock_r1")
    if row.get("lifecycle_blocked"):
        group = "交易安排核查"
        reason = "存在已公告赎回、最后交易日或到期安排，先核对交易资格。"
        watch = "核对发行人公告中的最后交易日、转股期限和赎回安排。"
        invalidation = "未完成交易资格核查，不列入新增机会观察。"
    elif r1 is None or stock_r1 is None:
        group = "数据待补"
        reason = "缺少同日债股涨跌幅，暂不能判断联动。"
        watch = "先补齐同日正股和转债行情，再判断价格变化。"
        invalidation = "日期或价格未核对一致，条件保持待确认。"
    elif r1 < 0 and stock_r1 < 0:
        group = "股债同弱"
        reason = "本日转债与正股均下跌，先观察负反馈是否收敛。"
        watch = "正股与转债共同止跌后，再观察承接是否持续。"
        invalidation = "正股继续下探、转债反弹无法维持，撤销修复观察。"
    elif r1 == 0 and stock_r1 == 0:
        group = "平盘观察"
        reason = "本日转债与正股均平盘，方向信号不足。"
        watch = "等待债股出现共同方向，再核对成交与承接。"
        invalidation = "方向尚未明确，不把平盘记录作为触发依据。"
    elif r1 > 0 and stock_r1 > 0:
        group = "股债共振观察"
        reason = "本日转债与正股同涨。"
        if row.get("r20") is not None and row["r20"] < 0:
            reason += "近20个交易日转债仍为负收益，先按修复观察。"
        else:
            reason += "后续需核对共同承接与持续性。"
        watch = "观察正股维持强势、转债回落有承接，且溢价未单独快速扩张。"
        invalidation = "正股转弱、转债跟随中断或回落失去承接，撤销共振观察。"
    else:
        group = "债股分化观察"
        reason = "本日债股方向或强度分化，不能仅凭转债上涨认定共振。"
        watch = "观察正股能否补强，以及转债溢价变化能否获得正股表现支持。"
        invalidation = "正股继续转弱、债端独立拉升后无法维持，撤销观察。"
    if row.get("conversion_start") and row["conversion_start"] > row["source_date"]:
        reason += f"尚未进入转股期（{row['conversion_start']}起），理论转股价值不能立即兑现。"
    return {**row, "classification": group, "reason": reason, "watch": watch, "invalidation": invalidation}


def _redemption(red: dict, day: str, conversion_start: str | None) -> tuple[str, bool]:
    last_trade = normalize_date(red.get("delist_dt"))
    maturity = normalize_date(red.get("maturity_dt"))
    if (last_trade and last_trade <= day) or (maturity and maturity <= day):
        return "已到最后交易日或到期日；核对公告与转股安排", True
    if red.get("redeem_icon") in {"R", "O"}:
        return "供应商标注已公告赎回或临近到期；核对发行人公告", True
    if conversion_start and conversion_start > day:
        return f"未到转股期（{conversion_start}）；其他赎回条款仍须核对", False
    if red.get("redeem_icon") == "G":
        notice = _text(red.get("force_redeem"))
        return "供应商标注本次不提前赎回；" + (notice or "具体期间待公告核对"), False
    return "未核验发行人强赎公告", False


def research_to_snapshot(payload: dict, report_date: str, *, source_path: str = "") -> dict:
    """转换同日研究摘要，保留原始来源时间。"""
    day = normalize_date(report_date)
    if not isinstance(payload, dict) or not day or normalize_date(payload.get("as_of")) != day:
        raise ValueError("研究摘要日期与请求日期不一致")
    bonds = payload.get("bonds")
    if not isinstance(bonds, list) or not bonds:
        raise ValueError("研究摘要缺少逐债数据")
    rows, warnings = [], []
    for item in bonds:
        if not isinstance(item, dict):
            raise ValueError("研究摘要逐债记录格式无效")
        bond, stock = item.get("bond") or {}, item.get("stock") or {}
        if not isinstance(bond, dict) or not isinstance(stock, dict) or not isinstance(item.get("redemption") or {}, dict):
            raise ValueError("研究摘要的债股行情或赎回资料格式无效")
        if any(normalize_date(value) != day for value in (item.get("quote_date"), bond.get("date"), stock.get("date"))):
            raise ValueError(f"{item.get('code', '转债')}的报价、债日线、股日线日期未全部对齐")
        if not is_closing_quote_time(item.get("quote_time")):
            raise ValueError(f"{item.get('code', '转债')}缺少有效收盘报价时间；盘中摘要不作为日收盘缓存")
        conversion_start = normalize_date(item.get("conversion_start"))
        redemption, blocked = _redemption(item.get("redemption") or {}, day, conversion_start)
        premium = number(item.get("conversion_premium_pct"))
        if item.get("jsl_close_matches_sina") is False:
            premium = None
            warnings.append(f"{item.get('code')}的债股报价核对不一致，转股溢价不展示。")
        row = {"code": item.get("code"), "name": item.get("name"), "stock_code": item.get("stock_code"),
               "stock_name": item.get("stock_name"), "close": bond.get("close"), "r1": bond.get("day_change_pct"),
               "r5": bond.get("return_5d_pct"), "r10": bond.get("return_10d_pct"), "r20": bond.get("return_20d_pct"),
               "amount_yi": item.get("amount_yi"), "premium_pct": premium,
               "remaining_issue_yi": item.get("remaining_issue_yi"), "stock_r1": stock.get("day_change_pct"),
               "stock_close": stock.get("close"), "conversion_start": conversion_start,
               "redemption_status": redemption, "lifecycle_blocked": blocked, "source_date": day,
               "quote_time": item.get("quote_time"), "source_phase": "close",
               "stock_source_date": day, "source_timestamp": payload.get("retrieved_at"),
               "redemption_source": item.get("redemption_source"), "conversion_price": item.get("conversion_price_current")}
        # 即使本日价格一致，昨收不一致仍会影响区间收益；保留报价，省略相关收益。
        if item.get("quote_previous_close_matches_history") is False:
            for field in ("r5", "r10", "r20"):
                row[field] = None
            warnings.append(f"{item.get('code')}的昨收与历史序列不一致，区间收益不展示。")
        rows.append(row)
    sources = [{"name": "同日独立研究摘要", "path": source_path, "source_date": day,
                "source_timestamp": payload.get("retrieved_at")}]
    for item in bonds:
        evidence = item.get("redemption_source")
        if evidence and not any(source.get("description") == evidence for source in sources):
            sources.append({"name": "转股与赎回资料", "description": evidence})
    return {"schema_version": SCHEMA, "as_of": day, "source_timestamp": payload.get("retrieved_at"),
            "summary": payload.get("market") or {}, "rows": rows, "warnings": warnings,
            "sources": sources, "limitations": payload.get("limitations") or []}


def validate_snapshot(payload: dict, report_date: str, *, source_path: str = "") -> dict:
    day = normalize_date(report_date)
    if not day:
        return _empty(None, "请提供有效的数据基准日（YYYY-MM-DD）。", "invalid")
    if not isinstance(payload, dict) or normalize_date(payload.get("as_of")) != day:
        return _empty(day, "缓存日期与请求日期不一致，未使用其他日期的数据。", "invalid")
    if payload.get("schema_version") != SCHEMA:
        return _empty(day, "缓存格式版本不受支持，需显式重新导入或刷新。", "invalid")
    records = payload.get("rows")
    if not isinstance(records, list):
        return _empty(day, "缓存缺少逐债数据。", "invalid")
    raw_warnings = payload.get("warnings")
    warnings = [str(w) for w in raw_warnings if w] if isinstance(raw_warnings, list) else []
    rows, seen = [], set()
    partial = False
    for record in records:
        if not isinstance(record, dict) or normalize_date(record.get("source_date")) != day:
            return _empty(day, "逐债来源日期缺失或与请求日期不一致；整份缓存未采用。", "invalid")
        quote_time = record.get("quote_time")
        if not (is_closing_quote_time(quote_time) or (not quote_time and record.get("source_phase") == "close")):
            return _empty(day, "逐债数据缺少有效收盘证据；盘中数据未作为收盘采用。", "invalid")
        if record.get("stock_source_date") and normalize_date(record["stock_source_date"]) != day:
            return _empty(day, "正股数据日期与转债不同；整份缓存未采用。", "invalid")
        code = _code(record.get("code"))
        if not code or code in seen:
            return _empty(day, "缓存含无效或重复转债代码。", "invalid")
        seen.add(code)
        row = {**record, "code": code, "stock_code": _code(record.get("stock_code")),
               "name": _text(record.get("name")) or code, "stock_name": _text(record.get("stock_name")),
               "source_date": day, "conversion_start": normalize_date(record.get("conversion_start"))}
        row.update({field: number(record.get(field)) for field in NUMERIC_FIELDS})
        if not record.get("stock_source_date"):
            for field in ("stock_r1", "stock_close", "premium_pct"):
                row[field] = None
            warnings.append(f"{row['name']}缺少正股同日来源日期，联动与转股溢价不展示。")
        for field in ("close", "amount_yi", "remaining_issue_yi"):
            if row[field] is not None and row[field] <= 0:
                row[field] = None
        missing = [field for field in NUMERIC_FIELDS if row[field] is None]
        if not record.get("stock_source_date"):
            missing.append("stock_source_date")
        if not row["stock_code"] or not row["stock_name"]:
            missing.append("stock_identity")
        if not row["conversion_start"]:
            missing.append("conversion_start")
        row["redemption_status"] = _text(record.get("redemption_status")) or "强赎资料缺失，待核验公告"
        if not record.get("redemption_status"):
            missing.append("redemption_status")
        row["missing_fields"] = missing
        if missing:
            partial = True
            warnings.append(f"{row['name']}部分字段待补齐；缺失值以“—”显示。")
        rows.append(describe_row(row))
    raw_sources = payload.get("sources")
    sources = [s for s in raw_sources if isinstance(s, dict)] if isinstance(raw_sources, list) else []
    if not sources:
        sources = [{"name": "同日转债缓存", "path": source_path, "source_date": day,
                    "source_timestamp": payload.get("source_timestamp")}]
    if not payload.get("source_timestamp"):
        partial = True
        warnings.append("来源采集时间缺失；未用文件修改时间代替。")
    rows.sort(key=lambda row: row.get("amount_yi") or -1, reverse=True)
    return {"status": "partial" if partial and rows else "ready" if rows else "unavailable", "as_of": day,
            "summary": payload.get("summary") if isinstance(payload.get("summary"), dict) else {},
            "rows": rows, "warnings": list(dict.fromkeys(warnings)) or ([] if rows else ["该日没有可用转债记录。"]),
            "sources": sources, "source_timestamp": payload.get("source_timestamp"),
            "limitations": payload.get("limitations") or []}


def find_research_snapshot(report_date: str, output_dir: str | Path | None = None) -> tuple[dict, Path] | None:
    """发现同日研究摘要；以内容日期为准，不凭目录名认定日期。"""
    root = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    paths = set(root.glob("research*/cb_latest_summary.json"))
    paths.update(root.glob("research_briefs/*/cb_latest_summary.json"))
    matches = []
    for path in sorted(paths):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if normalize_date(payload.get("as_of")) != normalize_date(report_date):
                continue
            snapshot = research_to_snapshot(payload, report_date, source_path=str(path))
            result = validate_snapshot(snapshot, report_date, source_path=str(path))
            if result["status"] in {"ready", "partial"}:
                matches.append((snapshot, path))
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return max(matches, key=lambda pair: (pair[0].get("source_timestamp") or "", str(pair[1]))) if matches else None


def load_cb_plan_data(report_date: str, *, output_dir: str | Path | None = None,
                      data_dir: str | Path | None = None) -> dict:
    """仅读取请求日的数据；不联网、不写入、不回退到其他交易日。"""
    day = normalize_date(report_date)
    if not day:
        return _empty(None, "请提供有效的数据基准日（YYYY-MM-DD）。", "invalid")
    root = Path(data_dir) if data_dir is not None else DATA_DIR
    path = root / "cb_daily_snapshots" / f"{day}.json"
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return _empty(day, "该日转债缓存无法读取；未回退到其他日期。", "invalid")
        return validate_snapshot(payload, day, source_path=str(path))
    found = find_research_snapshot(day, output_dir)
    if found:
        payload, path = found
        result = validate_snapshot(payload, day, source_path=str(path))
        result["warnings"].append("读取同日独立研究摘要；尚未导入标准日缓存。")
        return result
    return _empty(day, f"缺少 {day} 的同日转债快照；需显式导入研究摘要或刷新数据。")
