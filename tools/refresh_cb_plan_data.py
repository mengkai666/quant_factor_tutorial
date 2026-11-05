"""显式导入或刷新同日转债快照；不会发送消息或下单。

用法：
  python tools/refresh_cb_plan_data.py --date YYYY-MM-DD --import-summary PATH
  python tools/refresh_cb_plan_data.py --date YYYY-MM-DD --refresh
未指定 --refresh 时，只导入字段齐全且日期匹配的本地研究摘要。
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from cb_plan_data import (DATA_DIR, OUTPUT_DIR, SCHEMA, find_research_snapshot, normalize_date,
                          is_closing_quote_time, number, research_to_snapshot, validate_snapshot)


def _write_snapshot(payload: dict, report_date: str, data_dir: str | Path | None = None) -> Path:
    result = validate_snapshot(payload, report_date)
    if result["status"] not in {"ready", "partial"}:
        raise ValueError("；".join(result["warnings"]))
    root = Path(data_dir) if data_dir is not None else Path(DATA_DIR)
    path = root / "cb_daily_snapshots" / f"{result['as_of']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    # imported_at 仅描述本次写入，不替换来源采集时间。
    document = {**payload, "imported_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)
    return path


def import_research_summary(path: str | Path, report_date: str, *, data_dir=None) -> Path:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    snapshot = research_to_snapshot(payload, report_date, source_path=str(source.resolve()))
    checked = validate_snapshot(snapshot, report_date)
    if checked["status"] != "ready":
        raise ValueError("摘要不是日期与必要字段齐全的同日记录：" + "；".join(checked["warnings"]))
    return _write_snapshot(snapshot, report_date, data_dir)


def _fetch_stock_quotes(symbols: list[str]) -> dict:
    import cb_action_research as cb
    session, rows = cb._session(), []
    for offset in range(0, len(symbols), 80):
        response = session.get(cb.QUOTE_URL + ",".join(symbols[offset:offset + 80]), timeout=25)
        response.raise_for_status()
        for symbol, data in re.findall(r'hq_str_(\w+)="([^"]*)"', response.text):
            fields = data.split(",")
            if len(fields) < 32:
                continue
            rows.append({"symbol": symbol, "date": fields[30], "time": fields[31], "name": fields[0],
                         "close": number(fields[3]), "previous_close": number(fields[2])})
    return {"rows": rows, "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source": cb.QUOTE_URL}


def _series_returns(bars: list, day: str, close: float | None, previous_close: float | None) -> dict:
    result = {f"r{n}": None for n in (5, 10, 20)}
    valid = {bar["date"]: number(bar.get("close")) for bar in bars or []
             if isinstance(bar, dict) and normalize_date(bar.get("date")) and bar["date"] <= day
             and number(bar.get("close")) is not None and number(bar.get("close")) > 0}
    if not close or not valid:
        return result
    if day in valid and abs(valid[day] - close) > .002:
        return result
    before = sorted((date, value) for date, value in valid.items() if date < day)
    if not before or previous_close is None or abs(before[-1][1] - previous_close) > .002:
        return result
    closes = before + [(day, close)]
    for n in (5, 10, 20):
        if len(closes) > n:
            result[f"r{n}"] = round((close / closes[-n - 1][1] - 1) * 100, 3)
    return result


def fetch_snapshot(report_date: str, *, limit: int = 12, fetchers: dict | None = None) -> tuple[dict, dict]:
    """仅显式调用时访问公开源；实时接口不用于伪造历史日期。"""
    day = normalize_date(report_date)
    if not day:
        raise ValueError("无效的数据基准日")
    if not 1 <= limit <= 50:
        raise ValueError("逐债样本数量须在1至50之间")
    if fetchers is None:
        import cb_action_research as cb
        fetchers = {"market": cb._fetch_market, "base": cb._fetch_base, "redemption": cb._fetch_redeem,
                    "daily": cb._fetch_daily, "stock_quotes": _fetch_stock_quotes}
    raw, warnings = {}, []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        pending = {pool.submit(fetchers[key]): key for key in ("market", "base", "redemption")}
        for future in concurrent.futures.as_completed(pending):
            key = pending[future]
            try:
                raw[key] = future.result()
            except Exception as exc:
                if key == "market":
                    raise ValueError("无法获得有日期的转债行情") from exc
                warnings.append(f"{key}来源未取得，相关字段保持缺失。")
                raw[key] = {"rows": []}
    market = raw["market"]
    active = [row for row in market.get("rows", []) if isinstance(row, dict)
              and re.fullmatch(r"\d{6}", str(row.get("code") or ""))
              and re.fullmatch(r"(?:sh|sz)\d{6}", str(row.get("symbol") or ""))
              and normalize_date(row.get("quote_date")) == day
              and (number(row.get("amount")) or 0) > 0 and (number(row.get("trade")) or 0) > 0
              and (number(row.get("quote_close")) is not None)
              and abs(number(row["trade"]) - number(row["quote_close"])) <= .002]
    if not active:
        raise ValueError(f"行情服务未返回 {day} 的一致报价，未用其他交易日代替")
    # 盘中报价带有交易日期，不代表该日已收盘。
    if any(not is_closing_quote_time(row.get("ticktime")) for row in active):
        raise ValueError("该日尚无完整收盘报价；盘中快照不写入日收盘缓存")
    active.sort(key=lambda row: number(row["amount"]), reverse=True)
    base = {str(row.get("SECURITY_CODE")): row for row in raw["base"].get("rows", [])}
    red = {str(row.get("bond_id")): row for row in raw["redemption"].get("rows", [])}
    chosen, symbols = active[:limit], set()
    for row in chosen:
        code = str(row["code"])
        stock_code = str(base.get(code, {}).get("CONVERT_STOCK_CODE") or red.get(code, {}).get("stock_id") or "")
        if re.fullmatch(r"\d{6}", stock_code):
            symbols.add(("sh" if stock_code.startswith("6") else "sz") + stock_code)
    try:
        raw["stock_quotes"] = fetchers["stock_quotes"](sorted(symbols))
    except Exception:
        raw["stock_quotes"] = {"rows": []}
        warnings.append("正股同日收盘报价未取得，联动与溢价字段待补。")
    stocks = {row.get("symbol"): row for row in raw["stock_quotes"].get("rows", [])}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        pending = {pool.submit(fetchers["daily"], row["symbol"]): row["symbol"] for row in chosen}
        for future in concurrent.futures.as_completed(pending):
            symbol = pending[future]
            try:
                raw["daily_" + symbol] = future.result()
            except Exception:
                raw["daily_" + symbol] = []
                warnings.append(f"{symbol}历史收盘序列未取得，区间收益待补。")
    rows = []
    for quote in chosen:
        code, close = str(quote["code"]), number(quote["trade"])
        b, r = base.get(code, {}), red.get(code, {})
        stock_code = str(b.get("CONVERT_STOCK_CODE") or r.get("stock_id") or "")
        symbol = ("sh" if stock_code.startswith("6") else "sz") + stock_code
        stock = stocks.get(symbol, {})
        stock_valid = normalize_date(stock.get("date")) == day and is_closing_quote_time(stock.get("time"))
        stock_close = number(stock.get("close")) if stock_valid else None
        stock_previous = number(stock.get("previous_close")) if stock_valid else None
        stock_r1 = round((stock_close / stock_previous - 1) * 100, 3) if stock_close and stock_previous else None
        conversion_price = number(r.get("convert_price"))
        red_matches = (number(r.get("price")) is not None and abs(number(r["price"]) - close) <= .002
                       and stock_close is not None and number(r.get("sprice")) is not None
                       and abs(number(r["sprice"]) - stock_close) <= .002)
        premium = round((close * conversion_price / stock_close - 100), 3) if red_matches and conversion_price and stock_close else None
        conversion_start = normalize_date(r.get("convert_dt") or str(b.get("TRANSFER_START_DATE") or "")[:10])
        from cb_plan_data import _redemption
        redemption_status, blocked = _redemption(r, day, conversion_start)
        rows.append({"code": code, "name": quote.get("name") or code, "stock_code": stock_code,
                     "stock_name": b.get("SECURITY_SHORT_NAME") or r.get("stock_nm") or stock.get("name") or "",
                     "close": close, "r1": number(quote.get("changepercent")),
                     **_series_returns(raw.get("daily_" + quote["symbol"], []), day, close, number(quote.get("settlement"))),
                     "amount_yi": round(number(quote["amount"]) / 1e8, 4), "premium_pct": premium,
                     "remaining_issue_yi": number(r.get("curr_iss_amt")), "stock_r1": stock_r1, "stock_close": stock_close,
                     "conversion_price": conversion_price, "conversion_start": conversion_start,
                     "redemption_status": redemption_status, "lifecycle_blocked": blocked,
                     "quote_time": quote.get("ticktime"), "source_phase": "close",
                     "source_date": day, "stock_source_date": day if stock_valid else None,
                     "source_timestamp": market.get("retrieved_at")})
    total = sum(number(row["amount"]) for row in active)
    summary = {"returned": len(market.get("rows", [])), "dated_active_nonzero": len(active),
               "total_amount_yi": round(total / 1e8, 3), "top15_share_pct": round(sum(number(row["amount"]) for row in active[:15]) / total * 100, 3),
               "up": sum((number(row.get("changepercent")) or 0) > 0 for row in active),
               "down": sum((number(row.get("changepercent")) or 0) < 0 for row in active)}
    if not market.get("complete_pagination"):
        warnings.append("行情分页未确认完整；市场汇总仅覆盖已返回的同日样本。")
    sources = [{"name": key, "source": value.get("source"), "source_timestamp": value.get("retrieved_at"),
                "source_date": day if key in {"market", "stock_quotes"} else None}
               for key, value in raw.items() if isinstance(value, dict)]
    return ({"schema_version": SCHEMA, "as_of": day, "source_timestamp": market.get("retrieved_at"),
             "summary": summary, "rows": rows, "sources": sources, "warnings": warnings,
             "limitations": ["价格来自同日收盘报价；历史收盘未更新时以该报价追加计算区间收益。",
                              "转股价与余额来自赎回资料源；发行人公告仍需单独复核。"]}, raw)


def refresh_cb_snapshot(report_date: str, *, data_dir=None, limit: int = 12, fetchers: dict | None = None) -> Path:
    payload, raw = fetch_snapshot(report_date, limit=limit, fetchers=fetchers)
    root = Path(data_dir) if data_dir is not None else Path(DATA_DIR)
    evidence = root / "cb_daily_snapshots" / "evidence" / payload["as_of"]
    evidence.mkdir(parents=True, exist_ok=True)
    for key, value in raw.items():
        (evidence / f"{key}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    payload["sources"].append({"name": "本次公开源原始记录", "path": str(evidence.resolve())})
    return _write_snapshot(payload, report_date, root)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="数据基准日 YYYY-MM-DD")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--import-summary", type=Path)
    mode.add_argument("--refresh", action="store_true", help="显式连接公开行情源；只接受所请求日期的收盘数据")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args(argv)
    try:
        if args.refresh:
            path = refresh_cb_snapshot(args.date, data_dir=args.data_dir, limit=args.limit)
        else:
            source = args.import_summary
            if source is None:
                found = find_research_snapshot(args.date, args.output_dir)
                if not found:
                    raise ValueError("未找到同日完整研究摘要；指定 --import-summary，或使用 --refresh 显式联网刷新")
                source = found[1]
            path = import_research_summary(source, args.date, data_dir=args.data_dir)
        checked = validate_snapshot(json.loads(path.read_text(encoding="utf-8")), args.date)
        print(json.dumps({"path": str(path), "status": checked["status"], "as_of": checked["as_of"],
                          "rows": len(checked["rows"]), "source_timestamp": checked.get("source_timestamp"),
                          "warnings": checked["warnings"]}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
