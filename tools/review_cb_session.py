"""Fetch dated bond/stock evidence and audit an already-saved daily plan.

This writes an isolated review directory. It never refreshes production data,
rewrites the original plan, or infers an intraday fill from daily OHLC.
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
sys.path.insert(0, str(ROOT))
from src.cb_action_research import (  # noqa: E402
    QUOTE_URL, _date, _fetch_daily, _frame, _metrics, _num, _pct, _round,
    _save, _session,
)


def collect_review(report_dir: Path, review_date: str, codes: list[str]) -> dict:
    report_dir = Path(report_dir)
    review = report_dir / f"review_{review_date}" / "cb_evidence"
    raw = review / "raw"
    prior = json.loads((report_dir / "cb_baotuan_playbook.json").read_text(encoding="utf-8"))
    market = json.loads((report_dir / "cb_evidence/raw/market.json").read_text(encoding="utf-8"))
    redemption = json.loads((report_dir / "cb_evidence/raw/redemption.json").read_text(encoding="utf-8"))
    red_map = {str(row["bond_id"]): row for row in redemption["rows"]}
    market_rows = sorted(market["rows"], key=lambda row: -(_num(row.get("amount")) or 0))
    market_map = {row["code"]: {**row, "amount_rank": rank} for rank, row in enumerate(market_rows, 1)}
    saved = {row["code"]: row for row in prior.get("excluded", []) + prior.get("candidates", [])}
    selected = {row["code"] for row in prior.get("candidates", [])}
    if prior["plan_date"] != review_date:
        raise ValueError(f"saved plan is for {prior['plan_date']}, not {review_date}")
    pairs = {}
    for code in codes:
        row, red = market_map[code], red_map[code]
        stock_code = str(red["stock_id"])
        pairs[code] = (row["symbol"], ("sh" if stock_code.startswith(("6", "9")) else "sz") + stock_code)
    symbols = sorted({symbol for pair in pairs.values() for symbol in pair})
    errors = []

    def fetch(symbol):
        try:
            bars = _fetch_daily(symbol)
            _save(raw / f"daily_{symbol}.json", bars)
            return symbol, bars, None
        except Exception as exc:
            return symbol, [], f"{symbol}: {type(exc).__name__}: {str(exc)[:180]}"

    daily = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for symbol, bars, error in pool.map(fetch, symbols):
            daily[symbol] = bars
            if error:
                errors.append(error)
    quotes = {}
    quote_payload = {"source": QUOTE_URL, "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(), "rows": quotes}
    try:
        response = _session().get(QUOTE_URL + ",".join(symbols), timeout=25)
        response.raise_for_status()
        quote_payload["raw_text"] = response.text
        for symbol, values in re.findall(r'hq_str_(\w+)="([^"]*)"', response.text):
            fields = values.split(",")
            if len(fields) > 31:
                quotes[symbol] = {"name": fields[0], "date": _date(fields[30]), "time": fields[31],
                                  **{key: _num(fields[index]) for key, index in
                                     (("open", 1), ("previous_close", 2), ("close", 3),
                                      ("high", 4), ("low", 5), ("volume", 8), ("amount", 9))}}
    except Exception as exc:
        errors.append(f"quotes: {type(exc).__name__}: {str(exc)[:180]}")
    _save(raw / "quotes.json", quote_payload)

    rows = []
    for code, (bond_symbol, stock_symbol) in pairs.items():
        old = saved.get(code, {})
        current = {}
        for kind, symbol in (("bond", bond_symbol), ("stock", stock_symbol)):
            metrics = _metrics(_frame(daily[symbol], review_date))
            quote = quotes.get(symbol, {})
            date_ok = metrics.get("date") == quote.get("date") == review_date
            after_close = bool(re.fullmatch(r"\d{2}:\d{2}:\d{2}", quote.get("time", ""))
                               and quote["time"] >= "15:00:00")
            close_ok = (date_ok and _num(quote.get("close")) is not None
                        and abs(metrics["close"] - quote["close"]) <= .011)
            current[kind] = {"symbol": symbol, "metrics": metrics, "quote": quote,
                             "verified_review_date": bool(date_ok and close_ok and after_close),
                             "data_checks": {"dates_match_review": bool(date_ok), "quote_after_close": after_close,
                                             "daily_quote_close_match": bool(close_ok)},
                             "observed_latest_daily_date": max((bar["date"] for bar in daily[symbol]), default=None)}
        verified = all(item["verified_review_date"] for item in current.values())
        previous = _num(market_map[code].get("trade"))
        bm = current["bond"]["metrics"]
        gap = _round(_pct(bm.get("open"), previous), 3) if verified else None
        rows.append({
            "code": code, "name": market_map[code]["name"], "stock_name": red_map[code]["stock_nm"],
            "prior_as_of": prior["as_of"], "prior_amount_rank": market_map[code]["amount_rank"],
            "prior_status": "条件观察，尚未触发" if code in selected else "已细核未入选" if old else "未覆盖细核，不能判为不合格",
            "prior_failed_gates": old.get("reasons", []), "prior_reference_close": previous,
            "prior_entry_trigger": old.get("entry_trigger"), "prior_reward_level": old.get("reward_level"),
            "review_date": review_date, "verified_review_date": verified, **current,
            "open_gap_pct": gap,
            "close_vs_prior_close_pct": _round(_pct(bm.get("close"), previous), 3) if verified else None,
            "daily_no_entry_evidence": ("高开≥5%，原预案明确放弃" if code in selected and gap is not None and gap >= 5 else None),
            "intraday_execution_confirmed": False,
            "execution_conclusion": "缺少分钟突破回踩、VWAP及实际成交证据，不能判断买点触发或交易盈亏" if verified else
                                    f"未获得完整的{review_date}债股日线及同日收盘报价，不能当作当日复盘结果",
        })
    payload = {"prior_report": str(report_dir / "cb_baotuan_playbook.json"), "prior_as_of": prior["as_of"],
               "review_date": review_date, "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
               "purpose": "用户事先点名标的的逐只核验，不作为策略回测样本或事后选股依据",
               "verified_pairs": sum(row["verified_review_date"] for row in rows), "rows": rows, "errors": errors,
               "execution_backtest": False, "intraday_confirmed": False}
    _save(review / "session_review.json", payload)
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=ROOT / "output/trading_playbooks")
    parser.add_argument("--review-date", required=True)
    parser.add_argument("--codes", nargs="+", required=True)
    args = parser.parse_args()
    result = collect_review(args.report_dir, args.review_date, args.codes)
    print(json.dumps({"verified_pairs": result["verified_pairs"], "errors": result["errors"],
                      "rows": [{key: row[key] for key in ("code", "name", "prior_status", "verified_review_date", "close_vs_prior_close_pct")}
                               for row in result["rows"]]}, ensure_ascii=False, indent=2))
