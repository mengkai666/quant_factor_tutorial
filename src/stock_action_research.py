"""Dated stock-crowding playbooks with reproducible evidence and conditional entries.

This is an independent research export. It never modifies the live admission,
order, tracking, or portfolio state. A next-session plan is not a filled order.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
import math
from pathlib import Path
import re

import numpy as np
import pandas as pd

from data_sources.http_session import get_session

SHANGHAI = timezone(timedelta(hours=8))
SINAK = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_x=/CN_MarketDataService.getKLineData"
QUOTE = "https://qt.gtimg.cn/q="
WATCH_CODES = ["sh603278", "sz002724", "sz301313", "sz300006", "sz001219"]
VERIFIED_ANNOUNCEMENTS = {
    "sz300949": [{"date": "2026-10-27", "title": "奥雅股份股票交易异常波动公告",
                   "url": "https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=12667802&stockid=300949",
                   "review": "本轮原公告全文核验：普通异常波动；前三季度亏损、营业收入同比下降15.65%。",
                   "evidence": "普通异常波动不等于严重异常；价格强势不代表经营改善。"}],
}


def _json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _save(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _round(value):
    return float(Decimal(str(value)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))


def _clean(value):
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def load_market_prices(root: Path, as_of: str) -> pd.DataFrame:
    """Raw close slices; never fill missing exchange days or use future prices."""
    files = [p for p in sorted((root / "data/price_slices").glob("*.csv.gz"))
             if p.name[:10] <= as_of][-130:]
    frames = [pd.read_csv(p, usecols=["date", "code", "close_raw"]) for p in files]
    if not frames:
        return pd.DataFrame()
    frame = pd.concat(frames, ignore_index=True)
    frame = frame[(frame.date <= as_of) & frame.code.str.match(r"(?:sh60|sz00|sz30)")]
    return frame.pivot_table(index="date", columns="code", values="close_raw", aggfunc="last").sort_index()


def screen_prices(prices: pd.DataFrame) -> pd.DataFrame:
    """Predeclared point-in-time strength filter; no future path labels."""
    if len(prices) < 21:
        return pd.DataFrame()
    r1 = prices.pct_change(fill_method=None) * 100
    r5 = prices.pct_change(5, fill_method=None) * 100
    r20 = prices.pct_change(20, fill_method=None) * 100
    persistence = (r5.rank(axis=1, pct=True) >= .85).rolling(5, min_periods=5).sum()
    frame = pd.DataFrame({"close": prices.iloc[-1], "r1": r1.iloc[-1],
                          "r5": r5.iloc[-1], "r20": r20.iloc[-1],
                          "ma5": prices.iloc[-5:].mean(),
                          "dd20": (prices.iloc[-1] / prices.iloc[-20:].max() - 1) * 100,
                          "persistence": persistence.iloc[-1],
                          "complete21": prices.iloc[-21:].notna().all()})
    frame["eligible"] = (frame.complete21 & (frame.r5 > 0) & (frame.r20 >= 15)
                         & frame.r1.between(-5, 5) & (frame.close >= frame.ma5)
                         & (frame.dd20 >= -7) & (frame.persistence >= 3))
    frame["score"] = (frame.r20.clip(upper=100) * .30 + frame.r5.clip(upper=40) * .35
                      + frame.persistence * 4 + frame.dd20 * .7)
    return frame.sort_values(["eligible", "score"], ascending=[False, False])


def parse_sina_daily(text: str, as_of: str) -> list[dict]:
    match = re.search(r"=\((\[.*\])\);?", text, re.S)
    if not match:
        raise ValueError("Sina daily payload missing JSON array")
    result = []
    for item in json.loads(match.group(1)):
        day = str(item.get("day", ""))[:10]
        if day > as_of:
            continue
        row = {"date": day, **{key: _num(item.get(key)) for key in ("open", "high", "low", "close", "volume")}}
        if any(row[k] is None for k in ("open", "high", "low", "close", "volume")):
            raise ValueError("Incomplete OHLCV")
        if not 0 < row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]:
            raise ValueError("Inconsistent daily OHLC")
        result.append(row)
    return sorted({r["date"]: r for r in result}.values(), key=lambda r: r["date"])


def fetch_daily(code: str, as_of: str, out: Path, refresh: bool) -> dict:
    path = out / f"bars_{code}_{as_of}.json"
    cached = _json(path)
    if cached and not refresh:
        return cached
    try:
        response = get_session().get(SINAK, params={"symbol": code, "scale": 240, "ma": "no", "datalen": 160}, timeout=15)
        response.raise_for_status()
        rows = parse_sina_daily(response.text, as_of)
        if not rows or rows[-1]["date"] != as_of:
            raise ValueError(f"Source latest day {rows[-1]['date'] if rows else None}; expected {as_of}")
        record = {"code": code, "rows": rows, "basis": "raw", "source": "新浪日线OHLCV", "url": response.url,
                  "volume_unit": "股", "fetched_at": datetime.now(SHANGHAI).isoformat(), "error": None}
        _save(path, record)
        return record
    except Exception as exc:
        if cached and cached.get("rows", []) and cached["rows"][-1]["date"] == as_of:
            return {**cached, "refresh_error": type(exc).__name__ + ": " + str(exc)[:180]}
        return {"code": code, "rows": [], "basis": "raw", "source": "新浪日线OHLCV", "error": type(exc).__name__ + ": " + str(exc)[:180]}


def fetch_quotes(codes: list[str], as_of: str, out: Path, refresh: bool) -> dict:
    path = out / f"quotes_{as_of}.json"
    if path.exists() and not refresh:
        return _json(path, {})
    result = {}
    try:
        response = get_session().get(QUOTE + ",".join(codes), timeout=15)
        response.raise_for_status()
        for code, text in re.findall(r'v_((?:sh|sz)\d{6})="([^"]+)";', response.text):
            f = text.split("~")
            if len(f) < 39 or not re.fullmatch(r"\d{14}", f[30]):
                continue
            date = f"{f[30][:4]}-{f[30][4:6]}-{f[30][6:8]}"
            if date != as_of:
                continue
            result[code] = {"name": f[1], "close": _num(f[3]), "previous_close": _num(f[4]),
                            "open": _num(f[5]), "high": _num(f[33]), "low": _num(f[34]),
                            "amount_wan": _num(f[37]), "turnover": _num(f[38]), "date": date,
                            "timestamp": f[30], "source": "腾讯证券收盘报价", "url": QUOTE + code}
        _save(path, result)
    except Exception:
        result = _json(path, {})
    return result


def current_metrics(record: dict, expected: list[str], quote: dict | None, local_close: float | None, as_of: str) -> dict:
    rows = [r for r in record.get("rows", []) if r["date"] <= as_of]
    missing = []
    if not rows or rows[-1]["date"] != as_of:
        return {"usable": False, "missing": ["最新完整日线缺失"], "rows": rows}
    by_date = {r["date"]: r for r in rows}
    if any(d not in by_date for d in expected[-21:]):
        missing.append("最近21个市场交易日不完整")
    last = rows[-1]
    quote_agree = (all(abs(last[k] - quote[k]) <= .011 for k in ("close", "high", "low", "open"))
                   if quote and quote.get("date") == as_of and all(quote.get(k) is not None for k in ("close", "high", "low", "open")) else None)
    if quote_agree is not True:
        missing.append("最新OHLC跨源不一致" if quote_agree is False else "最新OHLC跨源核验缺失")
    local_agree = abs(last["close"] - local_close) <= .011 if local_close is not None else None
    if local_agree is not True:
        missing.append("全市场筛选价格与日线收盘不一致" if local_agree is False else "全市场筛选价格缺失")
    closes = [r["close"] for r in rows]
    prior_volume = sum(r["volume"] for r in rows[-6:-1]) / 5 if len(rows) >= 6 else 0
    return {"usable": not missing, "missing": missing, "rows": rows[-35:], "last": last,
            "ma5": sum(closes[-5:]) / 5, "ma10": sum(closes[-10:]) / 10,
            "high20": max(r["high"] for r in rows[-20:]), "low3": min(r["low"] for r in rows[-3:]),
            "low5": min(r["low"] for r in rows[-5:]), "volume_ratio": last["volume"] / prior_volume if prior_volume else None,
            "quote_agree": quote_agree, "local_agree": local_agree}


def _names_and_limit(root: Path, as_of: str):
    limits = pd.read_csv(root / "data/涨停历史缓存.csv", dtype={"日期": str, "代码": str})
    limits["date"] = pd.to_datetime(limits["日期"], errors="coerce").dt.strftime("%Y-%m-%d")
    limits = limits[limits.date <= as_of]
    names = limits.drop_duplicates("代码", keep="last").set_index("代码")["名称"].to_dict()
    return names, limits


def announcement_evidence(root: Path, code: str, known_until: str) -> dict:
    """Cached titles are pointers for verification, never a clean-announcement bill."""
    path = root / "data/cninfo_announcement_cache.csv"
    if not path.exists():
        return {"state": "未核验", "items": [], "limitation": "未获得公告资料"}
    table = pd.read_csv(path, dtype=str).fillna("")
    table["published_date"] = pd.to_datetime(table.ann_date, errors="coerce").dt.strftime("%Y-%m-%d")
    table = table[(table.code == code[-6:]) & (table.published_date <= known_until)]
    table = table.drop_duplicates(["code", "ann_date", "title"], keep="last").sort_values("published_date", ascending=False)
    items = [{"date": r.published_date, "title": r.title, "url": r.url,
              "fetched_at": r.fetched_at, "review": "仅缓存标题；正文未作本轮审阅"} for r in table.head(6).itertuples()]
    verified = [a for a in VERIFIED_ANNOUNCEMENTS.get(code, []) if a["date"] <= known_until]
    return {"state": "部分正文已核验，最新完整性待复核" if verified else ("待全文复核" if items else "未核验"),
            "items": verified + items, "verified_items": verified,
            "limitation": "标题缓存不证明最新公告完整，也不证明没有减持、停牌或监管安排；下一交易日公告检查仍是未通过门槛"}


def _board_path(code: str, rows: list[dict], name: str) -> dict:
    rate = .20 if code.startswith("sz30") else .10
    labels, streak, counts = [], 0, []
    for i, row in enumerate(rows):
        limit = _round(rows[i - 1]["close"] * (1 + rate)) if i else None
        up = abs(row["close"] - limit) < .005 if limit else False
        streak = streak + 1 if up else 0
        counts.append(up)
        labels.append({"date": row["date"], "close": row["close"], "high": row["high"], "low": row["low"],
                       "limit_up": up, "touched": row["high"] >= limit - .005 if limit else None, "streak": streak})
    recent = counts[-10:]
    gap = next((i for i, v in enumerate(reversed(counts)) if v), None)
    return {"last10_limit_count": sum(recent), "last_streak": streak, "sessions_after_limit": gap,
            "text": f"近10日{sum(recent)}板；当前{streak}连板" + (f"；距上次封板{gap}个交易日" if gap is not None else "；近端无封板"),
            "daily": labels[-15:], "method": "非ST常规涨跌幅按分币四舍五入；未结合交易所豁免/ST历史，不用于历史成交模拟"}


def build_candidate(code: str, name: str, screen: dict, metrics: dict, quote: dict | None,
                    *, as_of: str, plan_date: str, market_risk: str, pool_status: str | None = None) -> dict:
    """Price levels are conditional reclaim entries, not limit-at-any-price orders."""
    base = {"code": code, "name": name.strip(), "as_of": as_of, "plan_date": plan_date,
            "rank": None, "tier": "观察", "setup": "强势回踩后收复", "actual_status": "盘中触发未核验：尚无完整分钟与门槛证据",
            "trigger_verification": {"status": "not_verified", "confirmed": None},
            "missing": list(metrics.get("missing", [])), "metrics": screen, "old_pool_status": pool_status,
            "reference": None, "entry_trigger": None, "entry_zone": None, "stop_price": None,
            "target_reference": None, "not_buy": [], "holding": [], "conditions": [], "evidence": []}
    if "ST" in name.upper() or "退" in name:
        base.update(tier="回避", not_buy=["ST/退市风险名称不进入本研究常规涨跌幅策略"], actual_status="排除",
                    trigger_verification={"status": "not_applicable", "confirmed": None})
        return base
    if not metrics.get("usable"):
        base.update(not_buy=["补齐同日OHLC与独立核验前不执行"], actual_status="数据不足")
        return base
    bar = metrics["last"]
    # Only use a price actually traded in the last complete session. The trigger
    # requires a dip and reclaim, so it is not a blind next-open purchase.
    support = bar["low"]
    trigger = _round(support * 1.012)
    max_entry = _round(support * 1.020)
    stop = _round(support - .01)
    target = _round(metrics["high20"])
    reward = (target / max_entry - 1) * 100
    risk = (1 - stop / max_entry) * 100
    rr = (target - max_entry) / (max_entry - stop) if max_entry > stop else None
    path = _board_path(code, metrics["rows"], name)
    base.update(reference={"date": as_of, "close": bar["close"], "open": bar["open"], "high": bar["high"], "low": bar["low"],
                           "ma5": _round(metrics["ma5"]), "ma10": _round(metrics["ma10"]), "basis": "raw",
                           "volume_ratio": metrics["volume_ratio"], "amount_wan": (quote or {}).get("amount_wan"),
                           "turnover": (quote or {}).get("turnover")},
                entry_trigger=f"先回踩{support:.2f}—{trigger:.2f}元区域守住{support:.2f}，再向上收复{trigger:.2f}；连续两根5分钟K线收于触发价上方，成交额不低于前两根5分钟合计",
                entry_zone=[trigger, max_entry], stop_price=stop, target_reference=target,
                reward_space_pct=reward, planned_risk_pct=risk, reference_reward_risk=rr,
                board_path=path,
                trigger={"price": trigger, "max_entry": max_entry, "support": support, "confirmation_bars": 2, "bar_minutes": 5},
                cancellation={"rule_version": "2026-11-03", "intraday_at_or_below": stop,
                              "opening_grace": {"above_price": stop, "below_price": support,
                                                "reclaim_price": support, "deadline": "09:45"},
                              "invalidation_precedes_grace": True, "same_day_restart": False},
                stop={"price": stop, "source": f"{as_of}日低点下一个价位", "t_plus_one": True},
                reward_level={"price": target, "basis": "截至观察日最近20个交易日最高价；仅阻力参考，不是预测目标"},
                conditions=[{"label": "最新21交易日及OHLC核验", "passed": True},
                            {"label": "计划日09:45后上涨家数占比至少35%，且较09:35不下降", "passed": None},
                            {"label": "计划日跌停家数少于上一交易日且未继续扩张", "passed": None},
                            {"label": "同题材至少另2只上涨超过3%，且事先指定的核心未跌破前一日低点", "passed": None},
                            {"label": "先回踩承接再收复触发价，限价不超过进场区上沿", "passed": None},
                            {"label": "计划日无新增停牌、风险警示、重大减持等不利公告", "passed": None}],
                not_buy=[f"买入前含开盘触及/跌破{stop:.2f}即取消，优先于开盘宽限；只有开盘低于{support:.2f}且仍高于{stop:.2f}，才可等到09:45前收回{support:.2f}，逾期取消；任一取消后当日不重启",
                         f"未回踩就直接拉升，或可成交价超过{max_entry:.2f}，放弃追涨",
                         "一字涨停/封死涨停缺乏可成交卖单时，不把封板当成已买到",
                         "大盘未达到修复门槛、题材只有独苗或公告未核完，不开新仓"],
                holding=[f"已有可卖持仓跌破{stop:.2f}且下一根5分钟K线不能收回，按失效退出；低开或跌停可能无法按该价成交",
                         f"接近{target:.2f}旧高先观察承接；不能突破且回落跌破当日VWAP可减仓，突破后以5日线和前一日低点跟踪",
                         "新买股票受T+1约束当天不能卖，止损价是次日起的风险参考；买入当日不加仓摊低",
                         "触发买入后2个交易日仍未走出收复区、收盘转到MA5下方，结束试错"],
                scenario_up=f"守住{support:.2f}并收复{trigger:.2f}后，先看{target:.2f}旧高；只有放量站稳旧高才能讨论延续",
                scenario_flat=f"在{support:.2f}与{trigger:.2f}间横盘说明修复尚未确认，继续等待，不将横盘天数等同于必涨",
                scenario_down=f"买入前含开盘触及/跌破{stop:.2f}立即取消；开盘仅低于{support:.2f}但高于{stop:.2f}的宽限，须09:45前收回支撑，否则取消；取消后当日不重启，已有可卖持仓执行失效规则",
                evidence=[f"{as_of}近5日{screen.get('r5', 0):+.2f}%，近20日{screen.get('r20', 0):+.2f}%；最近5日有{int(screen.get('persistence', 0))}日处于5日收益前15%",
                          path["text"], "新浪不复权OHLC与腾讯同日OHLC四字段一致；收盘与全市场缓存一致"],
                risk_environment=market_risk)
    if rr is None or rr < 1.5 or reward <= 0:
        base["not_buy"].insert(0, "进场上沿至旧高的参考空间不足1.5倍计划价格风险，当前不升级为优先")
    return base


def historical_replay(prices: pd.DataFrame, names: dict, as_of: str, market_snapshots: dict) -> dict:
    """Every-day reconstructed filters, all outcomes retained; no fills assumed."""
    # Enforce the observation cutoff here as well as at the file-loader boundary.
    # A caller may pass a newer/full price frame when reviewing an old report.
    prices = prices.loc[prices.index <= as_of].sort_index()
    events = []
    last_event = {}
    dates = list(prices.index)
    for i in range(21, len(prices)):
        day = dates[i]
        screen = screen_prices(prices.iloc[:i + 1])
        if screen.empty:
            continue
        market = market_snapshots.get(day, {})
        breadth = _num(market.get("breadth_ratio"))
        for code, row in screen[screen.eligible].head(3).iterrows():
            if i - last_event.get(code, -100) <= 10:
                continue
            last_event[code] = i
            event = {"date": day, "code": code, "name": names.get(code, code),
                     "r5_before": float(row.r5), "r20_before": float(row.r20),
                     "market_breadth": breadth, "weak_market": breadth < .25 if breadth is not None else None}
            for horizon in (1, 3, 5, 10):
                end = i + horizon
                complete = end < len(prices) and prices[code].iloc[i:end + 1].notna().all()
                event[f"return_{horizon}d"] = float((prices[code].iloc[end] / prices[code].iloc[i] - 1) * 100) if complete else None
                event[f"end_{horizon}d"] = dates[end] if complete else None
            events.append(event)
    summaries = []
    for label, selected in (("全样本", events), ("当日上涨占比低于25%", [e for e in events if e["weak_market"] is True])):
        for horizon in (3, 5, 10):
            values = [e[f"return_{horizon}d"] for e in selected if e[f"return_{horizon}d"] is not None]
            summaries.append({"group": label, "horizon": horizon, "n": len(values),
                              "median_pct": float(np.median(values)) if values else None,
                              "q25_pct": float(np.percentile(values, 25)) if values else None,
                              "q75_pct": float(np.percentile(values, 75)) if values else None,
                              "positive_path_fraction": sum(v > 0 for v in values) / len(values) if values else None})
    complete = [e for e in events if e["return_10d"] is not None]
    ordered = sorted(complete, key=lambda e: e["return_10d"])
    cases = ordered[:2] + ordered[-2:] if len(ordered) >= 4 else ordered
    return {"method": "逐日以当日及以前原始收盘筛选相同条件、按分数取前3；同股10个交易日内去重；所有成熟正负路径均保留。不是当时实盘选股记录。",
            "outcome_basis": "信号日收盘至未来收盘的描述性路径；未模拟次日回踩收复成交、T+1、费用或滑点，不能称为战法成交胜率。",
            "as_of": as_of, "events": events, "summaries": summaries, "cases": cases,
            "limitations": ["筛选缓存为当前可见样本，存在存活偏差；原始股价遇除权也可能失真", "没有历史ST、退市、停牌和所有公告点时档案，历史筛选只是研究证据", "历史以同一只股票多段事件为单位，彼此及同日市场样本并非独立", "未来不足规定交易日或中途缺价的固定周期收益留空"]}


def build_stock_research(root, out, as_of, plan_date, refresh=True) -> dict:
    root, out = Path(root), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if plan_date <= as_of:
        raise ValueError("plan_date must be after the completed observation session")
    prices = load_market_prices(root, as_of)
    if prices.empty or prices.index[-1] != as_of:
        raise ValueError("全市场收盘切片不足指定观察日")
    screened = screen_prices(prices)
    names, limits = _names_and_limit(root, as_of)
    snapshot = _json(root / f"data/report_daily_snapshots/{as_of}.json", {})
    source_date_ok = snapshot.get("report_date") == as_of
    breadth = _num(snapshot.get("breadth_ratio")) if source_date_ok else None
    market_risk = "防守：市场修复后才启动" if breadth is not None and breadth < .25 else "条件观察"
    baseline = _json(root / f"output/抱团周期分析_{as_of}.json", {})
    tracking = next((d for d in reversed(baseline.get("tracking", {}).get("daily", [])) if d.get("date") == as_of), {})
    status = {r["code"]: r.get("status") for r in tracking.get("rows", [])}
    pool = screened[screened.eligible].head(14)
    codes = list(dict.fromkeys(list(pool.index) + WATCH_CODES))
    quotes = fetch_quotes(codes, as_of, out, refresh)
    with ThreadPoolExecutor(max_workers=4) as executor:
        records = dict(zip(codes, executor.map(lambda c: fetch_daily(c, as_of, out, refresh), codes)))
    candidates, excluded = [], []
    for code in codes:
        s = screened.loc[code].to_dict() if code in screened.index else {}
        q = quotes.get(code)
        name = (q or {}).get("name") or names.get(code, code)
        metrics = current_metrics(records[code], list(prices.index), q, _num(s.get("close")), as_of)
        candidate = build_candidate(code, name, s, metrics, q, as_of=as_of, plan_date=plan_date,
                                    market_risk=market_risk, pool_status=status.get(code))
        candidate["price_source"] = records[code].get("url")
        candidate["source_timestamp"] = records[code].get("fetched_at")
        candidate["price_data_usable"] = metrics.get("usable") is True
        candidate["announcements"] = announcement_evidence(root, code, (datetime.fromisoformat(plan_date) - timedelta(days=1)).date().isoformat())
        candidate["executable"] = False
        candidate["approval_state"] = "研究名单；所有计划日门槛待验证"
        candidate["theme_confirmation"] = {"passed": None, "peers": [],
                                            "reason": "本轮未预先指定并核验同题材同伴名单，无法自动判断板块共振；该门槛阻断买入确认"}
        candidate["missing"].append("同题材同伴名单尚未指定和核验，共振门槛无法自动通过")
        candidate["not_buy"].append("同题材同伴名单未核定前，不得把个股上涨或模糊题材标签当作共振通过，当前仍不可确认买入")
        if candidate["announcements"]["items"]:
            first = candidate["announcements"]["items"][0]
            candidate["evidence"].append(f"最新可见公告标题：{first['date']} {first['title']}（正文待复核）")
        for announcement in candidate["announcements"].get("verified_items", []):
            candidate["evidence"].append(f"{announcement['date']}已核验公告：{announcement['review']}")
            candidate["not_buy"].append(announcement["review"] + " 若仅以业绩转好作为买入理由，取消该理由。")
        candidate["why"] = candidate["evidence"][:2]
        if not s.get("eligible"):
            candidate["tier"] = "回避" if s.get("r1", 0) < -5 or s.get("close", 0) < s.get("ma5", 0) else "观察"
            candidate["not_buy"].insert(0, "未达到本轮强势回踩筛选条件；仅作原关注股去留复核")
            candidate["actual_status"] = "不在本轮入场名单"
            candidate["trigger_verification"] = {"status": "not_applicable", "confirmed": None}
            excluded.append(candidate)
        else:
            candidates.append(candidate)
    # Point-in-time shape strength then actual reference reward/risk. No observed
    # future outcome contributes to current ranking.
    candidates.sort(key=lambda c: (c.get("reference_reward_risk") is not None and c["reference_reward_risk"] >= 1.5,
                                    c["metrics"].get("score", 0)), reverse=True)
    priority = 0
    for i, c in enumerate(candidates, 1):
        c["rank"] = i
        price_verified = c.get("price_data_usable") is True
        if priority < 3 and c.get("reference_reward_risk", 0) is not None and c.get("reference_reward_risk", 0) >= 1.5 and price_verified:
            priority += 1
            c["tier"] = "优先"
        c["missing"].extend(["计划日盘中承接和成交尚未观察", "计划日公告与停复牌状态仍需开盘前复核", "同题材盘中强度尚未确认"])
    all_snapshots = {p.stem: _json(p, {}) for p in (root / "data/report_daily_snapshots").glob("*.json") if p.stem <= as_of}
    history = historical_replay(prices, names, as_of, all_snapshots)
    ten_day = next((s for s in history["summaries"] if s["group"] == "全样本" and s["horizon"] == 10), {})
    _save(out / "history_events.json", _clean(history["events"]))
    _save(out / "history_research.json", _clean(history))
    _save(out / "all_candidate_details.json", _clean(candidates + excluded))
    top_names = "、".join(c["name"] for c in candidates if c["tier"] == "优先")
    report = {"kind": "stock", "title": "股票抱团：先看哪几只、何时买、何时放弃", "as_of": as_of,
              "plan_date": plan_date, "generated_at": datetime.now(SHANGHAI).isoformat(),
              "scope": "沪深主板与创业板当前原始收盘全市场筛选，再以单一新浪日线OHLCV定价；科创板/北交所不在本轮执行样本",
              "summary": {"headline": f"优先观察{top_names or '暂缺'}；市场修复且个股回踩收复后才考虑条件进场。",
                          "action_now": "买入是否触发尚未核验。这里只形成条件预案，缺少开盘前与盘中门槛的完整证据。",
                          "historical_verdict": (f"机械买入强势筛选前3没有显示历史优势：{ten_day.get('n', 0)}个完整10日路径中位{ten_day.get('median_pct', 0):+.2f}%。回踩收复规则仍属待验证假设，不提供已证实胜率。"),
                          "selected_count": len(candidates), "priority_count": priority, "triggered_count": None,
                          "market_risk": market_risk, "market_breadth_pct": breadth * 100 if breadth is not None else None,
                          "limit_up": snapshot.get("limit_up") if source_date_ok else None,
                          "limit_down": snapshot.get("limit_down") if source_date_ok else None,
                          "max_height": snapshot.get("max_height") if source_date_ok else None,
                          "market_date": snapshot.get("report_date"), "market_snapshot_source": snapshot.get("snapshot_source"),
                          "market_scope": "全市场快照口径；与股票筛选范围不同。涨停池题材映射仅作背景，不据此宣布主线共振",
                          "price_universe": len(prices.columns), "screen_passed": int(screened.eligible.sum())},
              "candidates": candidates, "excluded": excluded, "history": history,
              "rules": ["先检查市场是否从弱势修复，再按优先顺序看最多3只；全不触发就不新开仓", "只做回踩到观察日低点附近后收复的条件单，不把强势股名单当开盘无条件买入表", "观察价由单一不复权日线计算；成交不得超过进场上沿，否则原盈亏空间不成立", "候选个股计划日若有除权、停牌、ST或新公告，旧价位作废并重新计算", "T+1、跌停卖不出和跳空风险必须计入试错预算；本研究不替代现有报告交易资格门控"],
              "sources": [{"title": "全市场收盘缓存", "path": str(root / "data/price_slices"), "as_of": as_of},
                          {"title": "同日市场快照", "path": str(root / f"data/report_daily_snapshots/{as_of}.json"), "as_of": snapshot.get("report_date")},
                          {"title": "新浪日线OHLCV（原始价）", "url": SINAK, "as_of": as_of},
                          {"title": "腾讯同日OHLC交叉核验", "url": QUOTE, "as_of": as_of}],
              "limitations": ["条件阈值是研究规则，尚未完成可成交分时回测；历史上涨频率不是该入场规则胜率", "抱团指价格持续强势的代理，不证明资金一致行动", "旧高只是阻力参考，不能保证抵达或突破", "没有计划日实时行情时，市场修复和个股触发均为未知，不当作已通过", "取消优先级于2026-11-03澄清：触及失效价优先取消，09:45宽限只在失效价以上适用，取消后当日不重启；不能回填为此前冻结预案的原始规则。现支撑与失效价仅差一分钱，宽限区间通常没有可成交价位；结构失效线的重设须另行回测"]}
    # The published report is a decision brief, while the full research evidence
    # remains in separate replay files. Never put 19 full cards on the landing view.
    market_summary = report.pop("summary")
    report["summary"] = [market_summary["headline"], market_summary["action_now"],
                         f"{as_of}上涨家数占比{market_summary['market_breadth_pct']:.2f}%，涨停{market_summary['limit_up']}只、跌停{market_summary['limit_down']}只：先等市场修复。"
                         if market_summary["market_breadth_pct"] is not None else "当日市场广度缺失，市场门槛未通过。",
                         market_summary["historical_verdict"],
                         "同题材同伴尚未指定并核验，板块共振不能自动通过；以下3只是优先核验顺序，均不是已确认买入。"]
    report["market_summary"] = market_summary
    report["decision_summary"] = "先看" + (top_names or "暂无") + "。盘中触发尚未核验；市场修复、回踩收复、同题材及公告门槛全部通过才考虑入场。"
    full_candidates = candidates
    short_candidates = [c for c in full_candidates if c["tier"] == "优先"][:3]
    others = [c for c in full_candidates if c not in short_candidates] + excluded
    report["candidates"] = short_candidates
    report["excluded"] = [{"code": c["code"], "name": c["name"],
                           "reason": (("本轮优先队列已满3只；" if c in full_candidates else "")
                                      + (c.get("not_buy", ["等待新的价格结构"])[0])),
                           "tier": c["tier"]} for c in others]
    report["decision_counts"] = {"priority": len(short_candidates),
                                 "watch": sum(c["tier"] != "回避" for c in others),
                                 "avoid": sum(c["tier"] == "回避" for c in others), "triggered": None}
    report["trigger_verification"] = {"status": "not_verified", "triggered_count": None,
                                      "reason": "尚无计划日完整分钟、市场、同题材与公告门槛证据"}
    brief_history = []
    for h in history["summaries"]:
        result = (f"中位{h['median_pct']:+.2f}%；25%—75%分位{h['q25_pct']:+.2f}%—{h['q75_pct']:+.2f}%；上涨路径占比{h['positive_path_fraction']:.1%}"
                  if h["n"] else "未成熟/无样本")
        brief_history.append({"类别": f"统计·后{h['horizon']}日", "样本/日期": f"n={h['n']}",
                              "个股/范围": h["group"], "结果": result,
                              "解释": "信号收盘到未来收盘路径，未模拟回踩成交；不是买入规则胜率"})
    for h in history["cases"]:
        brief_history.append({"类别": "历史完整案例", "样本/日期": h["date"],
                              "个股/范围": f"{h['name']} {h['code']}", "结果": f"后10日{h['return_10d']:+.2f}%",
                              "解释": "最差/最好各2个代表案例；用于展示分化，不能代表下一只预测"})
    report["history"] = brief_history
    report["history_method"] = history["method"] + history["outcome_basis"]
    report["limitations"].extend(history["limitations"])
    for source in report["sources"]:
        source["date"] = source.pop("as_of", as_of)
        if "path" in source:
            source["url"] = str(Path(source["path"]).resolve())
    report["sources"].extend(a for c in short_candidates for a in c["announcements"].get("verified_items", []))
    report = _clean(report)
    _save(out / "stock_research.json", report)
    return report
