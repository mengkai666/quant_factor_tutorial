"""Auditable, as-of-only OHLC case studies for Fantuo and Laimei.

Company announcements are evidence of regulatory status; reconstructed prices never
promote an ordinary abnormal-move announcement to a serious one.  Downloaded daily
bars are unadjusted and kept in the report output, leaving the source cache intact.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from abnormal_case_math import rounded_limit


SHANGHAI = timezone(timedelta(hours=8))
QUOTE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
START_DATE = "2026-08-01"
NAMES = {"sz301313": "凡拓数创", "sz300006": "莱美药业"}
SOURCE_VERIFIED_AT = "2026-10-29"
SOURCES = {
    "sz301313": [
        {
            "title": "凡拓数创2026-10-23公司严重异常波动公告（新浪转载公告全文）",
            "url": "https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=12658399&stockid=301313",
            "verified_at": SOURCE_VERIFIED_AT,
            "published_date": "2026-10-23",
            "evidence": "公告确认10月13日至23日连续9个交易日涨幅偏离值118.77%，10月21日至23日三日偏离值32.66%。",
            "verification_method": "HTTP 200，公司公告全文核对",
        },
        {
            "title": "川财证券2026-10-26重点监控风险提示",
            "url": "https://www.cczq.com/home/a/edu/fxts/st/20261026/27567.html",
            "verified_at": SOURCE_VERIFIED_AT,
            "published_date": "2026-10-26",
            "evidence": "券商提示深交所对凡拓数创重点监控期间为2026年10月26日至11月6日；属于券商转述的交易所措施。",
            "verification_method": "HTTP 200，页面正文核对（浏览器User-Agent）",
        },
    ],
    "sz300006": [
        {
            "title": "莱美药业2026-10-14股票交易异常波动公告，编号2026-050",
            "url": "https://static.cninfo.com.cn/finalpage/2026-10-14/1225605365.PDF",
            "verified_at": SOURCE_VERIFIED_AT,
            "published_date": "2026-10-14",
            "evidence": "公司确认10月12日至14日连续三个交易日收盘价涨幅偏离值累计超过30%，为普通异常波动；本条公告未认定严重异常波动。",
            "verification_method": "HTTP 200，巨潮PDF全部页面文字核对",
        }
    ],
}


def _number(value: Any) -> float | None:
    return None if pd.isna(value) else float(value)


def _closed_cutoff(as_of: str, now: datetime | None = None) -> str:
    """Exclude today's intraday row, even if a provider returns one in daily data."""
    now = now or datetime.now(SHANGHAI)
    requested = datetime.fromisoformat(str(as_of)[:10]).date()
    latest_possible = now.date() if now.time() >= time(15, 5) else now.date() - timedelta(days=1)
    return min(requested, latest_possible).isoformat()


def _fetch_rows(code: str, cutoff: str) -> dict:
    session = requests.Session()
    session.trust_env = False
    response = session.get(
        QUOTE_URL,
        params={"symbol": code, "scale": 240, "ma": "no", "datalen": 500},
        timeout=25,
    )
    response.raise_for_status()
    data = response.json()
    rows = []
    for values in data:
        if not START_DATE <= str(values.get("day", ""))[:10] <= cutoff:
            continue
        day, op, close, high, low, volume = (values[k] for k in ("day", "open", "close", "high", "low", "volume"))
        bar = {"date": str(day)[:10], "open": float(op), "close": float(close),
               "high": float(high), "low": float(low), "volume": float(volume) / 100}
        if not (0 < bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]):
            raise ValueError(f"{code} {day}: inconsistent OHLC")
        rows.append(bar)
    if not rows:
        raise ValueError(f"{code}: no unadjusted closed daily bars returned")
    return {"rows": rows, "fetched_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
            "source_url": response.url, "price_basis": "unadjusted", "volume_unit": "手"}


def enrich_rows(rows: list[dict], local: dict[str, float] | None = None) -> list[dict]:
    """Compute penny-rounded limits and trailing-only indicators from OHLC."""
    frame = pd.DataFrame(rows).drop_duplicates("date").sort_values("date").reset_index(drop=True)
    frame["previous_close"] = frame.close.shift(1)
    frame["pct_change"] = (frame.close / frame.previous_close - 1) * 100
    frame["ma5"] = frame.close.rolling(5, min_periods=5).mean()
    frame["ma10"] = frame.close.rolling(10, min_periods=10).mean()
    frame["volume_ratio5"] = frame.volume / frame.volume.shift(1).rolling(5, min_periods=5).mean()
    out = []
    streak = 0
    for row in frame.to_dict("records"):
        row = {key: _number(value) if key != "date" else value for key, value in row.items()}
        previous = row["previous_close"]
        limit = rounded_limit(previous) if previous else None
        is_limit = limit is not None and abs(row["close"] - limit) < 1e-7
        touched = limit is not None and row["high"] >= limit - 1e-7
        streak = streak + 1 if is_limit else 0
        row.update({"limit_price": limit, "is_limit": is_limit, "is_limit_up": is_limit,
                    "touched_limit": touched, "streak": streak,
                    "limit_label": ("前收缺失，无法判定" if limit is None else
                                    ("收盘涨停" if is_limit else ("触板未封" if touched else "非涨停"))),
                    "annotation": ""})
        local_close = (local or {}).get(row["date"])
        row["local_close"] = local_close
        row["local_close_delta"] = row["close"] - local_close if local_close is not None else None
        row["close_crosscheck"] = ("一致" if abs(row["local_close_delta"]) < 0.005 else "不一致") if local_close is not None else "本地缺失"
        out.append(row)
    return out


def announcement_returns(rows: list[dict], event_date: str) -> list[dict]:
    """Fixed horizons are null until complete; observed returns get their own field."""
    start = next((i for i, r in enumerate(rows) if r["date"] == event_date), None)
    if start is None:
        return []
    future = rows[start + 1:]
    entry = rows[start]["close"]
    running_high = entry
    drawdown = 0.0
    for row in future:
        running_high = max(running_high, row["close"])
        drawdown = min(drawdown, (row["close"] / running_high - 1) * 100)
    observed = (future[-1]["close"] / entry - 1) * 100 if future else 0.0
    return [{"event_date": event_date, "horizon": h, "complete": len(future) >= h,
             "available_sessions": len(future),
             "return_pct": (future[h - 1]["close"] / entry - 1) * 100 if len(future) >= h else None,
             "end_date": future[h - 1]["date"] if len(future) >= h else None,
             "observed_end_date": rows[-1]["date"], "observed_return_pct": observed,
             "observed_max_close_drawdown_pct": drawdown,
             "basis": "公告日收盘至后续个股交易日收盘，仅描述路径，非可成交策略收益"}
            for h in (3, 5, 10)]


def _official_events(code: str, as_of: str) -> list[dict]:
    if code == "sz301313":
        records = [
            {"date": "2026-10-23", "type": "严重异常波动（公司公告）", "title": "严重异常确认",
             "description": "10月13日至23日9个交易日偏离值118.77%；10月21日至23日3日偏离值32.66%。",
             "deviation_pct": 118.77, "window_sessions": 9},
            {"date": "2026-10-26", "type": "重点监控（券商转述）", "title": "重点监控开始",
             "description": "川财证券提示重点监控期为10月26日至11月6日。监控不等于禁止上涨。",
             "monitor_start": "2026-10-26", "monitor_end": "2026-11-06"},
        ]
    else:
        records = [{"date": "2026-10-14", "type": "普通异常波动（公司公告）", "title": "普通异动确认",
                    "description": "10月12日至14日连续3个交易日偏离值累计超过30%；本次已核验公告不构成严重异动认定。"}]
    return [{**r, "status_as_of": as_of} for r in records if r["date"] <= as_of]


def _narrative(code: str, rows: list[dict], as_of: str) -> dict:
    latest = rows[-1]
    by_date = {r["date"]: r for r in rows}
    recent = rows[-5:]
    levels = [
        {"label": "MA5", "price": latest["ma5"], "meaning": "短线收盘强度参考，非必然支撑"},
        {"label": "MA10", "price": latest["ma10"], "meaning": "中短线收盘强度参考"},
        {"label": "近5日最低价", "price": min(r["low"] for r in recent), "meaning": "近期波动下沿，跌破说明近期结构受损"},
        {"label": "近5日最高价", "price": max(r["high"] for r in recent), "meaning": "近期供给压力/突破核验位，不能当作确定目标价"},
    ]
    if code == "sz301313":
        event = by_date.get("2026-10-23")
        peak_day = by_date.get("2026-10-26")
        retreat = [r for r in rows if r["date"] >= "2026-10-26"]
        retreat_low = min((r["low"] for r in retreat), default=latest["low"])
        if retreat:
            levels.append({"label": "10/26后回撤段最低价", "price": retreat_low,
                           "meaning": "冲高后回撤段的近端失效参考，区别于更早涨停日低点"})
        for label, row, field, meaning in [
            ("10/23确认日收盘", event, "close", "衡量严重异动确认后是否保留涨幅"),
            ("10/26冲高日收盘", peak_day, "close", "回撤后的第一阶段修复参考"),
            ("10/26冲高日最高", peak_day, "high", "二次上攻需要消化的历史日高"),
        ]:
            if row:
                levels.append({"label": label, "price": row[field], "meaning": meaning})
        status = "已确认严重异动" if event else "公告前观察"
        if event and peak_day and "2026-10-26" <= as_of <= "2026-11-06":
            status += " / 重点监控期内"
        if event and peak_day and latest["date"] >= "2026-10-28":
            if latest["close"] < peak_day["close"]:
                headline = "严重异动后继续冲高，随后回撤；尚需修复确认"
            else:
                headline = "严重异动后维持强势，观察冲高区承接"
            conclusion = (f"10/19—20两连板，10/21—22断板2日，10/23再封板并于收盘后公告严重异动；"
                          f"10/26收盘{peak_day['close']:.2f}元、最高{peak_day['high']:.2f}元。"
                          f"截至{latest['date']}收盘{latest['close']:.2f}元，较10/23收盘{(latest['close']/event['close']-1)*100:+.2f}%。"
                          "重点监控与回撤同时出现只能建立时间关联，题材退潮、业绩、换手和市场波动也可能影响走势。")
            ma_action = "保持" if latest["close"] >= latest["ma5"] else "先收复"
            buy_condition = (f"研究观察：{ma_action}MA5 {latest['ma5']:.2f}元上方并守住回撤段低点{retreat_low:.2f}元，再看能否收复"
                             f"10/26收盘{peak_day['close']:.2f}元；放量突破{peak_day['high']:.2f}元且次日不跌回，才属于更强的再启动证据。")
            sell_condition = (f"已有仓位的结构失效参考：收盘跌破回撤段低点{retreat_low:.2f}元，"
                              f"或持续失守确认日收盘{event['close']:.2f}元，转入降风险观察；成交/仓位须另行约束。")
            old_anchor = by_date.get('2026-10-28')
            if old_anchor and latest['date'] > old_anchor['date'] and latest['close'] < old_anchor['low']:
                headline = '旧支撑已破，不再归类为强势横盘；先降低风险'
                conclusion += (f" 当前已低于10/28低点{old_anchor['low']:.2f}元，"
                               f"最新低点{retreat_low:.2f}元不是下移原失效线的理由。")
                buy_condition = (f"暂不执行买入；先收复确认日收盘{event['close']:.2f}及旧支撑{old_anchor['low']:.2f}元，"
                                 '再观察稳定整理与突破，不能押注监控结束必涨。')
                sell_condition = '旧锚已失守，原持仓转入退出/降风险评估；新买仓位受T+1约束，跌停和跳空可能无法按参考价成交。'
        else:
            headline = "按当日已形成价格和已披露公告观察"
            conclusion = "当前截点尚不足以观察完整的公告后路径。"
            buy_condition = "等待已披露公告与后续完整收盘，结合实际前高和近期低点判断。"
            sell_condition = "价格跌破已形成支撑区时重新评估。"
        annotations = {"2026-10-19": "20%涨停，首板", "2026-10-20": "20%涨停，2连板",
                       "2026-10-21": "连板中断，但收盘继续上涨", "2026-10-22": "断板第2日",
                       "2026-10-23": "断板2日后再板；收盘后严重异动公告",
                       "2026-10-26": "重点监控首日，继续冲高", "2026-10-27": "冲高后收盘回撤",
                       "2026-10-28": "第二个回撤日；公告后仅完成3个交易日"}
        missing = ["无逐笔委托/撤单与资金持仓证据，不能将回撤归因为主力出货或监管压制。",
                   "缺同题材全样本对照和实际交易行为监控记录，不能测出监管的独立因果效应。"]
    else:
        old = by_date.get("2026-10-16")
        status = "已核验普通异动 / 严重异动未确认"
        if old:
            levels.extend([
                {"label": "10/16旧收盘高", "price": old["close"], "meaning": "再启动后的旧平台观察位"},
                {"label": "10/16旧日高", "price": old["high"], "meaning": "收盘站回代表已收复前高，不等于成功二波"},
            ])
        if old and latest["date"] >= "2026-10-28" and latest["close"] > old["high"]:
            headline = "深回撤后再次涨停收复旧高，升级为二波突破候选"
            conclusion = (f"10/16前收8.13元，按20%四舍五入涨停价9.76元，当日最高9.76元、收盘9.75元，属于触板未封。"
                          f"10/20回落至7.75元，10/21单板反抽后再调整；10/28再涨停，收盘9.92元已超过旧收盘高9.75元和旧日高9.76元。"
                          f"截至{latest['date']}的证据应归为‘深回撤后收复前高’，后续能否连续站稳仍待观察。")
            buy_condition = (f"研究观察：次日/随后两日能否守住9.75—9.76元旧高区，回踩缩量、再次上攻放量，"
                             f"并保持MA5 {latest['ma5']:.2f}元上方；新高后尚无可核验的上方历史压力，不给定目标涨幅。")
            sell_condition = "若突破后收盘重新跌回9.75元下方且无法迅速收复，按突破失败降级；继续跌破近期回踩低点则进一步降低风险。"
            if latest['date'] > '2026-10-28':
                headline = '10/28再板后回踩旧高，二波尚待确认'
                conclusion += f" 最新收盘{latest['close']:.2f}元、日涨跌{latest['pct_change']:+.2f}%；不能沿用10/28的涨停结论描述今天。"
        else:
            headline = "普通异动后的高波动修复观察"
            conclusion = "按截至日完整OHLC观察涨停、回撤与前高关系；已核验的10/14公告为普通异动，不能据此认定进入严重监管。"
            buy_condition = "观察是否以收盘价收复已形成前高，并在后续交易日保持。"
            sell_condition = "跌回突破位置下方或跌破回踩低点，降级处理。"
        annotations = {"2026-10-12": "20%涨停，第一段启动", "2026-10-14": "间隔1日再板；普通异动公告",
                       "2026-10-16": "最高9.76触板，收盘9.75未封住（不是收盘涨停）",
                       "2026-10-20": "收盘7.75，较10/16收盘回撤20.51%", "2026-10-21": "20%单板反抽",
                       "2026-10-23": "反抽后再次回落", "2026-10-26": "盘中低至7.02后修复，波动仍大",
                       "2026-10-28": "断板4个交易日后再板，收盘9.92收复10/16旧高"}
        missing = ["已核验公告范围有限；未确认严重异动/重点监控并不等于证明从未被纳入。",
                   "两次冲高间回撤较大，不能直接称为低波动横盘；是否二波成功仍需后续完整交易日。",
                   "题材共振和量价条件为待检验条件，尚非经成交约束验证的获利策略。"]
    for row in rows:
        row["annotation"] = annotations.get(row["date"], "")
    return {"status": status, "regulation_status": status, "headline": headline, "conclusion": conclusion,
            "case_summary": conclusion, "action": "条件观察；按已形成收盘证据升级或降级",
            "buy_condition": buy_condition, "sell_condition": sell_condition, "levels": levels,
            "key_dates": [r for r in rows if r["annotation"]], "missing": missing}


def build_target_cases(root: Path, out: Path, as_of: str, refresh: bool = True) -> dict:
    """Fetch isolated raw-price snapshots or replay them offline, then build cases."""
    root, out = Path(root), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    cutoff = _closed_cutoff(as_of)
    snapshot_path = out / "target_ohlc_snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8")) if snapshot_path.exists() else {"by_code": {}}
    by_code = dict(snapshot.get("by_code", {}))
    errors = []
    if refresh:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {code: pool.submit(_fetch_rows, code, cutoff) for code in NAMES}
            for code, future in futures.items():
                try:
                    by_code[code] = future.result()
                except (ValueError, requests.RequestException) as exc:
                    errors.append(f"{code}刷新失败，若存在快照则回放：{type(exc).__name__}: {exc}")
        snapshot = {"schema_version": 1, "as_of": cutoff, "by_code": by_code,
                    "source_notes": "新浪未复权OHLC日线；原始成交量股已换算手；实时字段未写入历史研究。"}
        snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

    local_by_code: dict[str, dict[str, float]] = {}
    local_path = root / "data" / "price_history_cache.csv"
    if local_path.exists():
        local = pd.read_csv(local_path, usecols=["code", "date", "close_raw"], dtype={"code": str})
        for code in NAMES:
            subset = local[local.code.eq(code)].dropna(subset=["close_raw"])
            local_by_code[code] = dict(zip(subset.date.astype(str).str[:10], subset.close_raw.astype(float)))

    cases = []
    for code, name in NAMES.items():
        stored = by_code.get(code, {})
        raw = [r for r in stored.get("rows", []) if START_DATE <= r["date"] <= cutoff]
        if not raw:
            errors.append(f"{code}没有可用的截至{cutoff}已收盘OHLC，未生成该个股案例。")
            continue
        rows = enrich_rows(raw, local_by_code.get(code))
        narrative = _narrative(code, rows, cutoff)
        last = rows[-1]
        sources = [dict(s) for s in SOURCES[code] if s["published_date"] <= cutoff]
        event_date = "2026-10-23" if code == "sz301313" else "2026-10-14"
        quality = {"row_count": len(rows), "first_date": rows[0]["date"], "latest_date": last["date"],
                   "local_close_matched": sum(r["close_crosscheck"] == "一致" for r in rows),
                   "local_close_mismatched": sum(r["close_crosscheck"] == "不一致" for r in rows),
                   "local_close_missing": sum(r["close_crosscheck"] == "本地缺失" for r in rows),
                   "price_basis": "未复权", "limit_basis": "前收盘×1.20，Decimal四舍五入至0.01元",
                   "latest_matches_requested_as_of": last["date"] == cutoff,
                   "quote_fetched_at": stored.get("fetched_at"), "volume_unit": "手",
                   "source_url": stored.get("source_url"), "observed_only": True}
        case = {"code": code, "name": name, "board": "创业板", "as_of": cutoff,
                "latest": {**last, "current_consecutive_limits": last["streak"]},
                "official_events": _official_events(code, cutoff),
                "announcement_returns": announcement_returns(rows, event_date),
                "sources": sources, "evidence": [s["evidence"] for s in sources],
                "quality": quality, "rows": rows, "path": rows, **narrative}
        if quality["local_close_mismatched"]:
            case["missing"].append(f"源OHLC收盘与本地缓存有{quality['local_close_mismatched']}日不一致；本案例按源OHLC重建，差异逐日可查。")
        cases.append(case)
    return {"as_of": cutoff, "requested_as_of": str(as_of)[:10],
            "fetched_at": max((x.get("fetched_at", "") for x in by_code.values()), default=None),
            "snapshot_path": str(snapshot_path), "source_notes": snapshot.get("source_notes", ""),
            "warnings": errors, "cases": cases}
