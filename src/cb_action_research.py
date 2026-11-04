"""Dated, reproducible convertible-bond research; no order execution.

The universe is discovered from a paginated quote feed, not a hand-picked list.
Daily OHLC describes events, never a fictitious T+0 execution backtest. Current
liquidity selection has survivorship bias, explicitly retained in the output.
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd
import requests

MARKET_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
QUOTE_URL = "https://hq.sinajs.cn/list="
BASE_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REDEEM_URL = "https://www.jisilu.cn/data/cbnew/redeem_list/"
HISTORY_URL = "https://finance.sina.com.cn/realstock/company/{}/hisdata/klc_kl.js?d={}"
MAX_HISTORY_BONDS = 30
MIN_DETAIL_AMOUNT = 2e8

# These failures prevent a strong-close setup; they can still describe a
# pullback worth following. Other failed gates remain explicit blockers.
TECHNICAL_GATES = {
    "最近收盘未保持≥1%正收益，强势确认不足",
    "正股收盘下跌，债股共振未成立",
    "转债收盘低于MA5",
    "收盘位于日内区间下40%，抛压未消化",
}


def _num(value: Any) -> float | None:
    try:
        n = float(str(value).replace("%", "").replace(",", ""))
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def _round(value: Any, digits: int = 3) -> float | None:
    n = _num(value)
    return round(n, digits) if n is not None else None


def _date(value: Any) -> str | None:
    text = str(value or "")[:10]
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None


def _plain(value: Any) -> str:
    return re.sub(r"<[^>]*>", "", str(value or ""))


def _session() -> requests.Session:
    session = requests.Session()
    # Local request policy only; never alter the host's proxy environment.
    session.trust_env = False
    session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
    return session


def _save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def _cached(path: Path, fetch, refresh: bool, errors: list[str]):
    if refresh:
        try:
            data = fetch()
            _save(path, data)
            return data
        except Exception as exc:
            errors.append(f"{path.stem}: {type(exc).__name__}: {str(exc)[:140]}")
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    if not refresh:
        errors.append(f"{path.stem}: cache missing; omitted rather than synthesized")
    return None


def _fetch_market() -> dict:
    session = _session()
    rows: dict[str, dict] = {}
    complete = False
    pages = 0
    for page in range(1, 11):
        response = session.get(MARKET_URL, params={"page": page, "num": 100, "sort": "amount", "asc": 0, "node": "hskzz_z"}, timeout=25)
        response.raise_for_status()
        batch = response.json()
        if not isinstance(batch, list):
            raise ValueError("market response is not an array")
        pages = page
        if not batch:
            complete = True
            break
        previous = len(rows)
        rows.update({str(row["code"]): row for row in batch})
        if len(batch) < 100:
            complete = True
            break
        if len(rows) == previous:
            break
    # The list endpoint has only a time-of-day. Dates MUST come from the quote
    # endpoint, independently of the caller's desired as_of date.
    dates = {}
    symbols = [row["symbol"] for row in rows.values()]
    for i in range(0, len(symbols), 80):
        response = session.get(QUOTE_URL + ",".join(symbols[i:i + 80]), timeout=25)
        response.raise_for_status()
        for symbol, data in re.findall(r'hq_str_(\w+)="([^"]*)"', response.text):
            fields = data.split(",")
            if len(fields) > 30:
                dates[symbol] = {"date": _date(fields[30]), "close": _num(fields[3]), "time": fields[31] if len(fields) > 31 else None}
    for row in rows.values():
        q = dates.get(row["symbol"], {})
        row["quote_date"] = q.get("date")
        row["quote_close"] = q.get("close")
    return {"retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(), "complete_pagination": complete,
            "pages": pages, "rows": list(rows.values()), "source": MARKET_URL}


def _fetch_base() -> dict:
    session = _session()
    rows = []
    for page in range(1, 8):
        response = session.get(BASE_URL, params={"reportName": "RPT_BOND_CB_LIST", "columns": "ALL", "pageSize": 500,
                               "pageNumber": page, "sortColumns": "PUBLIC_START_DATE", "sortTypes": "-1"}, timeout=25)
        response.raise_for_status()
        result = response.json().get("result")
        if not result:
            break
        rows.extend(result.get("data", []))
        if page >= int(result.get("pages", page)):
            break
    if not rows:
        raise ValueError("empty bond base info")
    return {"retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(), "rows": rows, "source": BASE_URL}


def _fetch_redeem() -> dict:
    session = _session()
    response = session.post(REDEEM_URL, json={"rp": "1000"}, headers={"Referer": "https://www.jisilu.cn/data/cbnew/"}, timeout=25)
    response.raise_for_status()
    rows = [item["cell"] for item in response.json().get("rows", [])]
    if not rows:
        raise ValueError("empty redemption table")
    return {"retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(), "rows": rows, "source": REDEEM_URL}


def _fetch_daily(symbol: str) -> list[dict]:
    # Use the installed upstream decoder for Sina's proprietary compressed
    # raw-price series; no forward adjustment and no locally invented bars.
    from akshare.bond.bond_zh_cov import hk_js_decode, py_mini_racer
    response = _session().get(HISTORY_URL.format(symbol, dt.date.today().strftime("%Y_%m_%d")), timeout=25)
    response.raise_for_status()
    encoded = response.text.split("=", 1)[1].split(";", 1)[0].replace('"', "")
    decoder = py_mini_racer.MiniRacer()
    decoder.eval(hk_js_decode)
    data = decoder.call("d", encoded)
    if not data:
        raise ValueError(f"empty daily series {symbol}")
    return [{"date": str(bar["date"])[:10], **{key: _num(bar.get(key)) for key in ("open", "high", "low", "close", "volume")}}
            for bar in data[-220:]]


def _frame(bars: list[dict] | None, as_of: str) -> pd.DataFrame:
    frame = pd.DataFrame(bars or [])
    if frame.empty:
        return frame
    frame = frame.loc[frame["date"] <= as_of].drop_duplicates("date").sort_values("date").reset_index(drop=True)
    for field in ("open", "high", "low", "close", "volume"):
        frame[field] = pd.to_numeric(frame[field], errors="coerce")
    return frame.dropna(subset=["close", "high", "low"])


def _pct(new: Any, old: Any) -> float | None:
    a, b = _num(new), _num(old)
    return (a / b - 1) * 100 if a is not None and b and b > 0 else None


def _metrics(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {}
    last = frame.iloc[-1]
    close = float(last["close"])
    span = float(last["high"] - last["low"])
    data = {"date": last["date"], "close": close, "high": float(last["high"]), "low": float(last["low"]),
            "open": float(last["open"]), "bars": len(frame), "close_location": (close - last["low"]) / span if span > 0 else 0.5,
            "day_change_pct": _pct(close, frame.iloc[-2]["close"]) if len(frame) > 1 else None,
            "return_5d_pct": _pct(close, frame.iloc[-6]["close"]) if len(frame) > 5 else None,
            "return_20d_pct": _pct(close, frame.iloc[-21]["close"]) if len(frame) > 20 else None,
            "ma5": float(frame["close"].tail(5).mean()), "ma10": float(frame["close"].tail(10).mean()),
            "prior_20d_high": float(frame.iloc[-21:-1]["high"].max()) if len(frame) > 20 else None,
            "recent_5d_low": float(frame["low"].tail(5).min()),
            "volume_ratio20": float(last["volume"] / frame.iloc[-21:-1]["volume"].mean()) if len(frame) > 20 and frame.iloc[-21:-1]["volume"].mean() > 0 else None}
    return {key: _round(value) if isinstance(value, (float, int)) else value for key, value in data.items()}


def redemption_state(redemption: dict | None, base: dict | None, plan_date: str) -> dict:
    """Stop trading on/after the next session if last trading date has passed.

    ``delist_dt`` in JSL means *last trading day*, not exchange delisting day.
    Blank notices do not mean a verified absence of redemption risk.
    """
    red, base = redemption or {}, base or {}
    last_trade = _date(red.get("delist_dt"))
    maturity = _date(red.get("maturity_dt")) or _date(base.get("EXPIRE_DATE"))
    exchange_delist = _date(base.get("DELIST_DATE"))
    expired = bool((last_trade and last_trade < plan_date) or (maturity and maturity <= plan_date)
                   or (exchange_delist and exchange_delist <= plan_date))
    announced = red.get("redeem_icon") in {"R", "O"}
    return {"expired": expired, "announced": announced, "last_trading_day": last_trade,
            "maturity": maturity, "conversion_start": _date(red.get("convert_dt")) or _date(base.get("TRANSFER_START_DATE")),
            "conversion_price": _num(red.get("convert_price")), "remaining_issue_yi": _num(red.get("curr_iss_amt")),
            "status": "已停止交易/到期，排除" if expired else ("已公告赎回/临近到期，排除" if announced else
                      "供应商标注不提前赎回（开盘前复核公司公告）" if red.get("redeem_icon") == "G" else
                      "未见该表已公告强赎标记，开盘前复核公司公告" if red else "强赎状态缺失"),
            "notice": _plain(red.get("force_redeem")), "count": _plain(red.get("redeem_count")),
            "source": REDEEM_URL}


def _historical_events(code: str, name: str, frame: pd.DataFrame, stock: pd.DataFrame) -> list[dict]:
    """Non-overlapping, outcome-blind event definitions; 5-day daily outcomes."""
    results = []
    last_index = -99
    stock_by_date = stock.set_index("date") if not stock.empty else pd.DataFrame()
    for i in range(21, len(frame)):
        if i - last_index <= 5:
            continue
        row, prior = frame.iloc[i], frame.iloc[i - 1]
        change = _pct(row["close"], prior["close"])
        volume_avg = frame.iloc[i - 20:i]["volume"].mean()
        volume_ratio = row["volume"] / volume_avg if volume_avg > 0 else 0
        old_high = frame.iloc[i - 20:i]["high"].max()
        stock_return = None
        if not stock_by_date.empty and row["date"] in stock_by_date.index:
            position = stock.index[stock["date"] == row["date"]][0]
            if position > 0:
                stock_return = _pct(stock.iloc[position]["close"], stock.iloc[position - 1]["close"])
        setup = None
        if change is not None and change >= 5 and volume_ratio >= 1.5:
            setup = "放量债股共振" if stock_return is not None and stock_return >= 3 else "放量独立债强"
        elif change is not None and change <= -5 and volume_ratio >= 1.2:
            setup = "放量破位"
        if not setup:
            continue
        last_index = i
        future = frame.iloc[i + 1:i + 6]
        mature = len(future) == 5
        ret = _pct(future.iloc[-1]["close"], row["close"]) if mature else None
        mfe = _pct(future["high"].max(), row["close"]) if mature else None
        mae = _pct(future["low"].min(), row["close"]) if mature else None
        results.append({"code": code, "name": name, "date": row["date"], "setup": setup,
                        "close": _round(row["close"]), "bond_change_pct": _round(change, 2), "stock_change_pct": _round(stock_return, 2),
                        "volume_ratio20": _round(volume_ratio, 2), "broke_prior20_high": bool(row["close"] > old_high),
                        "forward_5d_return_pct": _round(ret, 2), "forward_5d_max_up_pct": _round(mfe, 2),
                        "forward_5d_max_down_pct": _round(mae, 2), "mature": mature,
                        "path": "未满5交易日" if not mature else "延续上涨" if ret >= 3 else "回落失败" if ret <= -3 else "震荡",
                        "bars": [{key: (_round(bar[key]) if key != "date" else bar[key]) for key in ("date", "open", "high", "low", "close")}
                                 for _, bar in frame.iloc[max(0, i - 3):i + 6].iterrows()]})
    return results


def _history_summary(events: list[dict]) -> list[dict]:
    results = []
    for setup in sorted({event["setup"] for event in events}):
        group = [event for event in events if event["setup"] == setup and event["mature"]]
        returns = [event["forward_5d_return_pct"] for event in group]
        results.append({"setup": setup, "events": len(group), "bonds": len({x["code"] for x in group}),
                        "positive_outcomes": sum(value > 0 for value in returns), "negative_outcomes": sum(value < 0 for value in returns),
                        "median_5d_return_pct": _round(pd.Series(returns, dtype=float).median(), 2),
                        "p25_5d_return_pct": _round(pd.Series(returns, dtype=float).quantile(.25), 2),
                        "p75_5d_return_pct": _round(pd.Series(returns, dtype=float).quantile(.75), 2),
                        "label": "当前活跃债子样本的日线事件分布；不是T+0胜率"})
    return results


def _recovery_reserves(excluded: list[dict], plan_date: str) -> list[dict]:
    """Expose near misses without relaxing today's entry criteria."""
    reserves = []
    for row in excluded:
        if row.get('reasons') != ['收盘位于日内区间下40%，抛压未消化']:
            continue
        bm, sm = row['bond_metrics'], row['stock_metrics']
        reserves.append({
            'code': row['code'], 'name': row['name'], 'stock_name': row['stock_name'],
            'reference_price': row['price'], 'pct_change': row['pct_change'],
            'stock_change_pct': sm.get('day_change_pct'), 'premium_pct': row['premium_pct'],
            'amount_yi': row['amount_yi'], 'close_location_pct': _round(bm['close_location'] * 100, 1),
            'reason': row['reason'],
            'recheck': f'{plan_date}仅跟踪修复；当日收盘处于当日高低区间40%以上，且债涨≥1%、正股不跌、债价≥MA5和强赎等全部条件仍通过后，再纳入下一交易日筛选。',
            'actual_status': '当前未通过完整筛选；不能沿用澳弘的当日入场计划。',
        })
    return reserves


def _detail_universe(current: list[dict]) -> list[dict]:
    """Cover liquid pullbacks and every quote that can pass the gain gate.

    The original top-30 history cohort is retained separately. Selection uses
    only the dated snapshot, without names or any following-session result.
    """
    detailed = []
    ordered = sorted(current, key=lambda row: -(_num(row.get("amount")) or 0))
    for rank, row in enumerate(ordered, 1):
        selection = []
        if rank <= MAX_HISTORY_BONDS:
            selection.append("原成交额前30研究样本")
        if (_num(row.get("amount")) or 0) >= MIN_DETAIL_AMOUNT:
            selection.append("成交额≥2亿元，覆盖活跃债回撤")
        if (_num(row.get("changepercent")) or 0) >= 1:
            selection.append("债涨≥1%，补齐强势筛选核查")
        if selection:
            detailed.append({**row, "amount_rank": rank, "coverage_reasons": selection})
    return detailed


def _repair_watchlist(excluded: list[dict], plan_date: str) -> list[dict]:
    """A research-only pullback list, never a second route into candidates.

    Liquid names with a >=3% down day or >=8% retreat from their prior 20-day
    high are described even if multiple strong-close gates fail. Fresh bars,
    lifecycle data and non-ST underlying are required to describe the setup.
    Valuation and short-history blocks are retained visibly, not waived.
    """
    watch = []
    for row in excluded:
        bm, sm = row.get("bond_metrics", {}), row.get("stock_metrics", {})
        reasons = row.get("reasons", [])
        red = row.get("redemption", {})
        amount = _num(row.get("amount"))
        if amount is None:
            amount = (_num(row.get("amount_yi")) or 0) * 1e8
        if not bm or not sm or amount < MIN_DETAIL_AMOUNT:
            continue
        if (red.get("expired") or red.get("announced") or "ST" in row.get("stock_name", "").upper()
                or any(reason in reasons for reason in (
                    "债或正股日线日期缺失/不一致", "市场快照与日线收盘不一致", "强赎状态和当前转股价缺失"))):
            continue
        drawdown = _pct(bm.get("close"), bm.get("prior_20d_high"))
        down_day = (bm.get("day_change_pct") or 0) <= -3
        below_ma5 = (bm.get("close") or 0) < (bm.get("ma5") or 0)
        if not (set(reasons) & TECHNICAL_GATES) or not (down_day or (below_ma5 and drawdown is not None and drawdown <= -8)):
            continue
        blockers = [reason for reason in reasons if reason not in TECHNICAL_GATES]
        watch.append({
            "code": row["code"], "name": row["name"], "stock_code": row.get("stock_code"), "stock_name": row.get("stock_name"),
            "as_of": row.get("as_of"), "plan_date": plan_date, "tier": "修复研究",
            "setup": "活跃债回撤，等待重新确认", "amount_rank": row.get("amount_rank"),
            "amount_yi": row["amount_yi"], "reference_price": row.get("price"), "pct_change": row.get("pct_change"),
            "stock_change_pct": sm.get("day_change_pct"), "premium_pct": row.get("premium_pct"),
            "bars": bm.get("bars"), "drawdown_from_prior20_high_pct": _round(drawdown, 2),
            "ma5": bm.get("ma5"), "close_location_pct": _round((bm.get("close_location") or 0) * 100, 1),
            "failed_gates": reasons, "blocking_gates": blockers, "reason": row.get("reason"),
            "actual_status": "仅作回撤研究，交易资格仍被拦截" if blockers else "仅作回撤观察，当前未通过强势确认",
            "recheck": (f"{plan_date}跟踪债股修复；收盘债涨≥1%、正股不跌、债价≥MA5、收盘位置≥40%，"
                        "并重新通过日线≥40根、溢价≤50%、债价≤350元及全部强赎/停牌核验后，才可进入下一交易日条件观察。"),
            "invalidation": "继续低于MA5或刷新近期低点，修复仍未成立；强赎/停牌或数据异常即停止跟踪。",
            "execution_eligible": False,
        })
    return sorted(watch, key=lambda row: (row.get("amount_rank") or 10**9, row["code"]))


def _candidate(row: dict, bond: dict, stock: dict, red: dict, rank: int, plan_date: str) -> dict:
    price = bond["close"]
    previous_high, previous_low = bond["high"], bond["low"]
    stock_change = stock.get("day_change_pct")
    stock_code, stock_name = row.get("stock_code", ""), row.get("stock_name", "")
    conversion = red.get("conversion_price")
    premium = (price / (stock["close"] * 100 / conversion) - 1) * 100 if conversion and stock.get("close") else None
    # Fixed levels are only setup landmarks. Execution is an opening-range
    # retest with an intraday invalidation and a no-chase distance constraint.
    overhead = [level for level in (previous_high, bond.get("ma10")) if level is not None and level > price]
    first_target = min(overhead) if overhead else previous_high
    setup = ("破位后债股共振修复" if bond.get("ma10", 0) > price else "债股共振回踩") if stock_change is not None and stock_change >= 1 else "转债独强待正股确认"
    return {
        "code": row["code"], "name": row["name"], "stock_code": stock_code, "stock_name": stock_name,
        "rank": rank, "tier": "观察", "setup": setup, "entry_type": "T+0盘中条件单研究，非开盘无条件买入",
        "reason": (f"成交额{(_num(row.get('amount')) or 0) / 1e8:.2f}亿元，债涨{bond.get('day_change_pct', 0):.2f}%、正股涨{stock_change or 0:.2f}%，债价站回MA5；"
                   + (f"仍低于MA10 {bond['ma10']:.3f}元，按破位后修复处理，主升未确认。" if bond.get('ma10', 0) > price else "保留债股共振盘中确认，昨日上涨不等于今日已触发。")),
        "price": price, "reference_price": price, "pct_change": bond.get("day_change_pct"), "amount_yi": _round(_num(row.get("amount")) / 1e8, 2),
        "premium_pct": _round(premium, 2), "conversion_price": conversion,
        "remaining_issue_yi": red.get("remaining_issue_yi"), "score": _round(row.get("score"), 2),
        "reference": {"close": price, "high": previous_high, "low": previous_low, "ma5": bond["ma5"],
                      "stock_close": stock.get("close"), "stock_high": stock.get("high"), "unit": "元", "date": bond["date"]},
        "entry_trigger": f"09:45后，正股高于前收{stock.get('close', 0):.2f}元且较前收涨幅≥1%；转债突破当日前15分钟高点后回踩站稳，同时在日内成交均价线上方。",
        "entry_zone": f"基准收盘{price:.3f}元；实际只在开盘区间高点回踩后0—0.5%内观察，离确认位超过1%放弃；高开≥5%不开盘追入。",
        "trigger": {"type": "opening_range_retest", "earliest_time": "09:45", "max_chase_pct": 1, "max_open_gap_pct": 5,
                    "requires_vwap": True, "requires_stock_change_pct": 1},
        "stop_price": f"实际入场价下方1.5%或回踩低点（选更靠近入场的一条）为退出线；债跌破日内均价且正股跌回{stock.get('close', 0):.2f}元，立即取消/退出。",
        "stop": {"max_loss_pct": 1.5, "structure": "retest_low", "gap_slippage_possible": True},
        "target_reference": f"首个压力参考{first_target:.3f}元，再看前日高点{previous_high:.3f}元；入场已高于首压或到首压空间不足止损距离2倍则不买。盈利达1R减半、余量跟踪日内均价，14:50退出。",
        "reward_level": _round(first_target),
        "not_buy": "正股不跟涨、仅债拉升；高开≥5%；突破位偏离>1%；强赎/停牌公告新变化；盘中数据缺失，任一项成立即不买。",
        "conditions": ["债与正股同步转强且回踩确认", "成交均价线之上，追价和盈亏比合格", "交易/强赎公告核验通过"],
        "failure": "未突破则继续等；跌破回踩低点或正股翻绿时取消。触发后的亏损退出可能受流动性与滑点影响。",
        "holding": "T+0日内；最多一个试错回合，亏损后不补仓；14:50前按可成交价格退出，不隔夜。",
        "scenario_up": f"债股同步上攻并回踩确认→条件成立才试错，先观察{first_target:.3f}元压力、再看{previous_high:.3f}元；不是预言一定触及。",
        "scenario_flat": "横盘但未触发→空仓等，不因为成交额排名高就买；午后仍未确认则取消当日计划。",
        "scenario_down": f"转债失守日内均价或正股跌回{stock.get('close', 0):.2f}元→不参与；已持仓按上面的止损线退出。",
        "actual_status": f"仅{bond['date']}收盘已核验；{plan_date}盘中触发尚未核验，当前=等待。",
        "missing": ["下一交易日逐笔/分钟量价和VWAP", "开盘前最新公司赎回及停复牌公告"],
        "evidence": [
            f"{bond['date']}新浪raw日线：转债收盘{price:.3f}元、涨{bond.get('day_change_pct', 0):.2f}%，正股收盘{stock.get('close', 0):.2f}元、涨{stock_change or 0:.2f}%。",
            f"成交额{(_num(row.get('amount')) or 0) / 1e8:.2f}亿元；MA5 {bond['ma5']:.3f}元，MA10 {bond.get('ma10', bond['ma5']):.3f}元，前日高点{previous_high:.3f}元。",
            f"当前转股价{conversion:.2f}元，同日重算溢价{premium:.2f}%；集思录剩余规模{red.get('remaining_issue_yi') or 0:.3f}亿元。" if conversion and premium is not None else "转股价值资料不完整，盘前必须补核。",
            red.get("status", "强赎状态待核") + ("：" + red["notice"] if red.get("notice") else ""),
        ],
        "raw_evidence": {"bond": bond, "stock": stock, "redemption": red, "premium_formula": "债价/(正股价/当前转股价*100)-1", "as_of": bond["date"]},
    }


def build_cb_research(root: Path, out: Path, as_of: str, plan_date: str, refresh: bool = True) -> dict:
    """Build a dated evidence payload and cached raw source snapshots.

    `out` is the common report directory; this module writes only its own
    `cb_evidence/` child. Refresh failures fall back transparently to dated
    snapshots; future/wrong-day quotes are never used as as_of prices.
    """
    root, out = Path(root), Path(out)
    evidence = out / "cb_evidence"
    cache = evidence / "raw"
    errors: list[str] = []
    market = _cached(cache / "market.json", _fetch_market, refresh, errors) or {"rows": [], "complete_pagination": False}
    base = _cached(cache / "base.json", _fetch_base, refresh, errors) or {"rows": []}
    redemption = _cached(cache / "redemption.json", _fetch_redeem, refresh, errors) or {"rows": []}
    base_map = {str(row["SECURITY_CODE"]): row for row in base["rows"]}
    red_map = {str(row["bond_id"]): row for row in redemption["rows"]}
    current = [row.copy() for row in market["rows"] if row.get("quote_date") == as_of and (_num(row.get("amount")) or 0) > 0]
    current.sort(key=lambda row: -(_num(row.get("amount")) or 0))
    total_amount = sum(_num(row.get("amount")) or 0 for row in current)
    history_codes = {row["code"] for row in current[:MAX_HISTORY_BONDS]}
    detailed = _detail_universe(current)
    for row in detailed:
        meta, red = base_map.get(row["code"], {}), red_map.get(row["code"], {})
        row["stock_code"] = str(red.get("stock_id") or meta.get("CONVERT_STOCK_CODE") or "")
        row["stock_name"] = str(red.get("stock_nm") or meta.get("SECURITY_SHORT_NAME") or "")
    symbols = {row["symbol"] for row in detailed}
    symbols.update(("sh" if row["stock_code"].startswith(("6", "9")) else "sz") + row["stock_code"] for row in detailed if row["stock_code"])
    bars_by_symbol = {}
    def load(symbol):
        local_errors = []
        result = _cached(cache / f"daily_{symbol}.json", lambda: _fetch_daily(symbol), refresh, local_errors)
        return symbol, result, local_errors
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for symbol, bars, problem in pool.map(load, sorted(symbols)):
            bars_by_symbol[symbol] = bars
            errors.extend(problem)
    records, excluded, events = [], [], []
    for row in detailed:
        stock_symbol = ("sh" if row["stock_code"].startswith(("6", "9")) else "sz") + row["stock_code"]
        bond_df, stock_df = _frame(bars_by_symbol.get(row["symbol"]), as_of), _frame(bars_by_symbol.get(stock_symbol), as_of)
        bm, sm = _metrics(bond_df), _metrics(stock_df)
        red = redemption_state(red_map.get(row["code"]), base_map.get(row["code"]), plan_date)
        reasons = []
        if not bm or bm.get("date") != as_of or not sm or sm.get("date") != as_of:
            reasons.append("债或正股日线日期缺失/不一致")
        if bm and abs(bm["close"] - (_num(row.get("trade")) or 0)) > .011:
            reasons.append("市场快照与日线收盘不一致")
        if red["expired"] or red["announced"]:
            reasons.append(red["status"])
        if not red_map.get(row["code"]):
            reasons.append("强赎状态和当前转股价缺失")
        if "ST" in row["stock_name"].upper():
            reasons.append("正股风险警示")
        if bm and bm.get("bars", 0) < 40:
            reasons.append("上市/可得日线不足40根，暂不纳入成熟形态策略")
        if bm and (bm.get("day_change_pct") or 0) < 1:
            reasons.append("最近收盘未保持≥1%正收益，强势确认不足")
        if sm and (sm.get("day_change_pct") or 0) < 0:
            reasons.append("正股收盘下跌，债股共振未成立")
        if bm and bm["close"] < bm["ma5"]:
            reasons.append("转债收盘低于MA5")
        if bm and bm["close"] > 350:
            reasons.append("研究方案排除350元以上极端高价债（非交易所限制）")
        premium = (bm["close"] / (sm["close"] * 100 / red["conversion_price"]) - 1) * 100 if bm and sm and red.get("conversion_price") else None
        if premium is None or premium > 50:
            reasons.append("转股溢价率缺失或超过研究阈值50%")
        if bm and bm["close_location"] < .4:
            reasons.append("收盘位于日内区间下40%，抛压未消化")
        # Broader current coverage must not silently change the legacy history
        # cohort, and must never use a following-session winner list.
        if row["code"] in history_codes:
            events.extend(_historical_events(row["code"], row["name"], bond_df, stock_df))
        summary_row = {"code": row["code"], "name": row["name"], "stock_code": row["stock_code"], "stock_name": row["stock_name"],
                       "price": bm.get("close"), "pct_change": bm.get("day_change_pct"), "amount": _num(row.get("amount")),
                       "amount_yi": _round((_num(row.get("amount")) or 0) / 1e8, 2),
                       "premium_pct": _round(premium, 2), "redemption": red, "bars": bm.get("bars", 0), "as_of": bm.get("date"),
                       "bond_metrics": bm, "stock_metrics": sm, "amount_rank": row["amount_rank"],
                       "coverage_reasons": row["coverage_reasons"]}
        if reasons:
            excluded.append({**summary_row, "tier": "回避", "reason": "；".join(reasons), "reasons": reasons})
        else:
            row["score"] = (bm.get("day_change_pct") or 0) + .5 * (sm.get("day_change_pct") or 0) + min(5, (_num(row.get("amount")) or 0) / 1e9)
            records.append((row, bm, sm, red))
    records.sort(key=lambda item: -(item[0].get("score") or 0))
    candidates = [_candidate(*item, rank=i + 1, plan_date=plan_date) for i, item in enumerate(records[:3])]
    for item in records[3:]:
        excluded.append({"code": item[0]["code"], "name": item[0]["name"], "tier": "观察", "reason": "通过基础筛选，但每日观察名额上限3只，顺位靠后"})
    reserves = _recovery_reserves(excluded, plan_date)
    repair_watch = _repair_watchlist(excluded, plan_date)
    quote_eligible = [row for row in current if (_num(row.get('changepercent')) or 0) >= 1]
    detailed_codes = {row['code'] for row in detailed}
    # Include terminated names even when absent from current quotes, instead of
    # accidentally reusing the old static report's historical universe.
    lifecycle_excluded = []
    for code, red in red_map.items():
        state = redemption_state(red, base_map.get(code), plan_date)
        if state["expired"] or state["announced"]:
            lifecycle_excluded.append({"code": code, "name": red.get("bond_nm"), "reason": state["status"], "redemption": state})
    stats = _history_summary(events)
    mature = [event for event in events if event["mature"]]
    # Show latest successes and failures side by side, not just best outcomes.
    successes = sorted([x for x in mature if x["path"] == "延续上涨"], key=lambda x: x["date"], reverse=True)[:3]
    failures = sorted([x for x in mature if x["path"] == "回落失败"], key=lambda x: x["date"], reverse=True)[:3]
    top5 = sum(_num(row.get("amount")) or 0 for row in current[:5])
    decline = sum((_num(row.get("changepercent")) or 0) < 0 for row in current)
    limitation = ["当期成交额前30只的历史复盘具有存续/流动性选择偏差；不代表历史全市场，也不提供可交易胜率。",
                  "日线没有买卖先后顺序、盘口、分钟VWAP和成交滑点；T+0入场规则未做分钟级成交回测。",
                  "溢价率按同日债价、正股价和集思录当前转股价重算；历史事件不回填当前转股价。",
                  "抱团仅表示成交集中与债股涨跌共振；数据不能证明有组织资金抱团或主力意图。",
                  "公司赎回公告、停复牌与最后交易日仍需开盘前复核；集思录状态不是公司公告原文。",
                  "前一日高点是压力参考，不是盈利承诺；不到触发价、追价过远或盈亏比不足就不交易。"]
    if not market.get("complete_pagination"):
        limitation.append("新浪列表分页未确认完整，覆盖范围仅为已返回数据，不能称全市场排名。")
    if len(current) < len(market["rows"]):
        limitation.append(f"已排除{len(market['rows']) - len(current)}条报价日期非{as_of}或无成交的记录。")
    payload = {
        "schema_version": 1, "kind": "cb", "title": "可转债抱团：日内共振与退出预案", "as_of": as_of, "plan_date": plan_date,
        "scope": f"新浪分页返回{len(market['rows'])}只；核验{as_of}有效成交{len(current)}只；对成交额前30、成交额≥2亿元或债涨≥1%的并集{len(detailed)}只取债/股日线，最终保留{len(candidates)}只盘中条件观察。",
        "summary": [f"{as_of}可核验转债成交额前5占比{top5 / total_amount * 100 if total_amount else 0:.1f}%，下跌{decline}/{len(current)}只。",
                    f"筛出{len(candidates)}只条件观察、{len(repair_watch)}只活跃债回撤研究；后者未入选。{plan_date}的盘中触发状态未核验，旧收盘价仅作预案基准。"],
        "decision_counts": {"priority": 0, "watch": len(candidates), "avoid": len(excluded), "triggered": None},
        "decision_summary": (f"先盯{'、'.join(x['name'] for x in candidates)}。只做正股同步、突破回踩、VWAP之上三项共振；未触发就不买，14:50前退出。"
                             if candidates else "本次没有通过完整筛选的转债，不为凑名单放宽条件。等待新数据与新信号。"),
        "candidates": candidates, "reserve_candidates": reserves, "repair_watchlist": repair_watch,
        "excluded": excluded, "lifecycle_excluded": lifecycle_excluded,
        "screening_audit": {
            'dated_active': len(current), 'quote_gain_ge_1': len(quote_eligible),
            'quote_gain_ge_1_checked': sum(row['code'] in detailed_codes for row in quote_eligible),
            'quote_gain_ge_1_unchecked': [row['code'] for row in quote_eligible if row['code'] not in detailed_codes],
            'qualified': len(records), 'recovery_reserves': len(reserves), 'repair_watchlist': len(repair_watch),
            'detailed': len(detailed), 'detail_min_amount_yi': MIN_DETAIL_AMOUNT / 1e8,
            'history_cohort_size': len(history_codes),
            'unchecked': [{'code': row['code'], 'name': row['name'], 'reason': '未达本次细核范围；未细核不等于不合格'}
                          for row in current if row['code'] not in detailed_codes],
            'note': '细核范围为成交额前30、成交额≥2亿元或债涨≥1%的并集；强势门槛不变。回撤研究和候补均不计为买入候选，历史统计仍只用原成交额前30。',
        },
        "history": successes + failures, "history_stats": stats,
        "market_summary": {"quote_date": as_of, "returned": len(market["rows"]), "dated_active": len(current),
                           "complete_pagination": market.get("complete_pagination"), "amount_yi": _round(total_amount / 1e8, 2),
                           "top5_amount_share_pct": _round(top5 / total_amount * 100 if total_amount else None, 2),
                           "declining": decline, "rising": sum((_num(row.get("changepercent")) or 0) > 0 for row in current),
                           "history_bonds": len(history_codes), "detailed_bonds": len(detailed), "history_events": len(events), "mature_events": len(mature)},
        "rules": ["细核覆盖：最新收盘日成交额前30、成交额≥2亿元或债涨≥1%的并集；完整筛选仍要求债上涨≥1%、正股不下跌、债价在MA5上方、收盘位置≥40%、日线≥40根、非ST正股、非已公告强赎/到期、溢价≤50%、债价≤350元。阈值为研究规则而非监管界线。",
                  "回撤研究：成交额≥2亿元，且符合以下任一项：①债单日跌≥3%；②债价低于MA5且较前20日高点回撤≥8%。列明全部未过门槛，不给入场指令、不计为通过筛选，也不据此扩充历史回测样本。",
                  "执行：09:45后看前15分钟高点突破回踩、债价在VWAP上、正股涨幅≥1%；偏离确认位>1%或高开≥5%直接放弃。",
                  "退出：回踩低点与入场价-1.5%取较近者；1R减半、余量跟踪VWAP；14:50全部退出，不隔夜，不亏损加仓。R为实际入场到止损距离。",
                  "无触发=不买；历史涨幅分布不等于买入后收益，不能拿日线最高价当能成交的目标。"],
        "limitations": limitation,
        "sources": [
            {"title": "新浪可转债全市场列表（分页）", "url": MARKET_URL, "date": as_of, "retrieved_at": market.get("retrieved_at")},
            {"title": "新浪即时行情日期交叉核验", "url": QUOTE_URL, "date": as_of},
            {"title": "新浪raw日线：债券及正股", "url": "https://finance.sina.com.cn/realstock/", "date": as_of},
            {"title": "东财可转债发行/映射/摘牌数据 RPT_BOND_CB_LIST", "url": BASE_URL, "date": _date(base.get("retrieved_at")), "retrieved_at": base.get("retrieved_at")},
            {"title": "集思录强赎表：转股价、余额、最后交易日", "url": REDEEM_URL, "date": _date(redemption.get("retrieved_at")), "retrieved_at": redemption.get("retrieved_at")},
            {"title": "上交所可转换公司债券交易实施细则（2025修订）：当日回转、次日起20%涨跌幅", "url": "https://www.sse.com.cn/lawandrules/sselawsrules2025/bond/convertible/trading/c/c_20250606_10782995.shtml", "date": "2025-06-06", "verified_by": "parent_agent_official_source_lookup"},
            {"title": "深交所可转换公司债券交易实施细则：第5条回转、第15条20%", "url": "https://docs.static.szse.cn/www/lawrules/rule/bond/W020250327514604502531.pdf", "date": "2025-03-27", "verified_by": "parent_agent_official_source_lookup"},
        ],
        "data_quality": {"refresh_requested": refresh, "errors": errors, "raw_dir": str(cache),
                         "history_range": [min((x["date"] for x in events), default=None), max((x["date"] for x in events), default=None)],
                         "execution_backtest": False, "intraday_confirmed": False},
    }
    _save(evidence / "research.json", payload)
    _save(evidence / "historical_events.json", events)
    _save(evidence / "market_ranked.json", current)
    return payload
