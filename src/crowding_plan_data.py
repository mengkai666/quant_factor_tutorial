"""按报告日读取股票与题材抱团证据，供明日预案展示。

只读取已有 JSON，不联网、不跑筛选、不写缓存，也不导入有自愈副作用的 paths。
股票身份沿用当日正式跟踪；日线价格只能说明价格强势，不能确认机构资金抱团。
"""
from __future__ import annotations

import csv
import json
import math
import os
from datetime import date, datetime
from pathlib import Path
import re
import sys
from typing import Any


_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
_STOCK_SCOPE = "价格强势代理；新旧身份来自当日正式跟踪，不代表机构资金或可成交确认。"
_PRICE_SCOPE = "前复权当前缓存研究价；存在复权修订，不能当作保证成交价。"
_SECTOR_SCOPE = "项目题材归类的涨停样本，非全板块资金流；连板家数不是连续强势天数。"


def _day(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        return None
    raw = value.strip()
    try:
        if re.fullmatch(r"\d{8}", raw):
            return datetime.strptime(raw, "%Y%m%d").date().isoformat()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return date.fromisoformat(raw).isoformat()
    except ValueError:
        pass
    return None


def _obj(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _records(value: Any) -> list[dict]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any, *, signed: bool = False) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number.is_integer() and (signed or number >= 0) else None


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _block(status: str, as_of: str | None, summary: str, warnings: list[str]) -> dict:
    return {"status": status, "as_of": as_of, "summary": summary, "rows": [], "warnings": _unique(warnings)}


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("JSON 顶层不是对象")
    return value


def _dated_paths(directory: Path, prefix: str) -> dict[str, Path]:
    result = {}
    for path in directory.glob(prefix + "*.json"):
        day = _day(path.stem[len(prefix):])
        if day:
            result[day] = path
    return result


def _load_report(directory: Path, prefix: str, day: str, kind: str, date_key: str) -> tuple[dict | None, dict]:
    """精确文件优先；旧文件只留出处，未来文件连内容也不读取。"""
    paths = _dated_paths(directory, prefix)
    selected = day if day in paths else max((d for d in paths if d < day), default=None)
    if selected is None:
        future = min((d for d in paths if d > day), default=None)
        return None, {"kind": kind, "path": str(paths[future].resolve()) if future else str((directory / f"{prefix}{day}.json").resolve()),
                      "as_of": future, "status": "invalid" if future else "missing",
                      "warning": f"仅发现未来报告 {future}，不能回填 {day}。" if future else f"缺少 {day} 的{kind}报告。"}
    path = paths[selected]
    source = {"kind": kind, "path": str(path.resolve()), "as_of": selected, "status": "ok"}
    try:
        payload = _read(path)
    except (OSError, UnicodeError, ValueError) as exc:
        source.update(status="invalid", warning=f"{path.name} 无法读取（{type(exc).__name__}）。")
        return None, source
    observed = _day(payload.get(date_key))
    source["as_of"] = observed
    if observed != selected:
        source.update(status="invalid", warning=f"{path.name} 的内容日期与文件日期不一致，已拒绝。")
        return None, source
    if observed != day:
        source.update(status="stale", warning=f"{kind}最新可用报告截至 {observed}，不能作为 {day} 的当前事实。")
        return None, source
    return payload, source


def _stock_clock_error(payload: dict, day: str) -> tuple[str, str | None, str] | None:
    """报告日期与实际价格时钟同时检查，不能只相信文件名。"""
    clocks = _obj(payload.get("sources_as_of"))
    observed = _day(payload.get("market_as_of")) or _day(clocks.get("prices"))
    if observed is None:
        return "invalid", None, "股票报告缺少可核对的价格日期，暂停当前观察名单。"
    for label, raw in (("全市场行情", payload.get("market_as_of")), ("价格证据", clocks.get("prices"))):
        if raw is not None and _day(raw) != day:
            got = _day(raw)
            status = "stale" if got and got < day else "invalid"
            return status, got, f"{label}日期为 {got or '未知'}，与报告日 {day} 不一致。"
    for key in ("tracking", "screening_replay"):
        requested = _obj(payload.get(key)).get("requested_date")
        if requested is not None and _day(requested) != day:
            return "invalid", observed, f"{key} 请求日期不匹配，已拒绝混合日期报告。"
    return None


def _on_day(rows: Any, day: str) -> dict:
    matching = [r for r in _records(rows) if _day(r.get("date")) == day]
    return matching[-1] if matching else {}


def _ranking(snapshot: dict) -> dict[str, dict]:
    if snapshot.get("coverage_ok") is False:
        return {}
    rows = [r for r in _records(snapshot.get("candidate_scores")) if r.get("code") and _number(r.get("score")) is not None]
    if not rows:
        # top 是源报告已展示的有序子集，明确限定排名范围。
        return {str(r["code"]): {"ranking": i, "score": _number(r.get("score")), "ranking_scope": "报告展示的价格候选"}
                for i, r in enumerate(_records(snapshot.get("top")), 1) if r.get("code")}
    rows.sort(key=lambda r: -float(r["score"]))
    result, previous, rank = {}, None, None
    for position, row in enumerate(rows, 1):
        score = float(row["score"])
        if score != previous:
            rank = position
        result[str(row["code"])] = {"ranking": rank, "score": score, "ranking_scope": "当日价格候选内排名"}
        previous = score
    return result


def _replay_prices(replay: dict, day: str) -> tuple[list[str], int | None, dict, list[str]]:
    dates = [_day(d) for d in replay.get("dates", [])] if isinstance(replay.get("dates"), list) else []
    if not dates or any(d is None for d in dates) or dates != sorted(set(dates)):
        return [], None, {}, ["回放价格日期缺失或顺序异常，未提供观察价。"]
    if any(d > day for d in dates):
        return [], None, {}, ["回放价格含未来日期，已拒绝该价格层，未提供观察价。"]
    if day not in dates:
        return [], None, {}, [f"回放价格未覆盖 {day}，未提供观察价。"]
    return dates, dates.index(day), _obj(replay.get("prices")), []


def _price_metrics(values: Any, index: int | None, length: int) -> dict:
    empty = {key: None for key in ("close", "r1", "r5", "r10", "r20", "ma5", "dd20", "previous20_close_high")}
    if index is None or not isinstance(values, list) or len(values) != length:
        return empty
    history = [_number(v) if _number(v) is not None and float(v) > 0 else None for v in values[:index + 1]]
    if not history or history[-1] is None:
        return empty
    result = dict(empty, close=history[-1])
    for period in (1, 5, 10, 20):
        # 中间缺价同样阻断收益计算，不能跳过停牌/缺失后假装连续窗口。
        window = history[-period - 1:]
        if len(window) == period + 1 and all(v is not None for v in window):
            result[f"r{period}"] = round((window[-1] / window[0] - 1) * 100, 4)
    if len(history) >= 5 and all(v is not None for v in history[-5:]):
        result["ma5"] = round(sum(history[-5:]) / 5, 4)
    if len(history) >= 20 and all(v is not None for v in history[-20:]):
        result["dd20"] = round((history[-1] / max(history[-20:]) - 1) * 100, 4)
    if len(history) >= 21 and all(v is not None for v in history[-21:-1]):
        result["previous20_close_high"] = max(history[-21:-1])
    return result


def _old_core_codes(payload: dict, day: str, current: str | None) -> list[str]:
    observations = []
    for row in _records(payload.get("daily")):
        observed = _day(row.get("date"))
        code = row.get("confirmed_proxy")
        if observed and observed < day and code and code != current:
            observations.append((observed, str(code)))
    for cycle in _records(_obj(payload.get("tracking")).get("cycles")):
        confirmed = _day(cycle.get("confirmed_on"))
        code = cycle.get("anchor_code")
        if confirmed and confirmed < day and code and code != current:
            observations.append((confirmed, str(code)))
    return _unique([code for _, code in sorted(observations, reverse=True)])


def _stock_notes(row: dict, day: str) -> None:
    """条件性观察说明，结合个股精确均线偏离度、10日异动安全垫与角色给出针对性判断。"""
    pieces = [row["status"] or "跟踪身份未标注"]
    if row["support_streak"] is not None:
        pieces.append(f"连续支持 {row['support_streak']} 日")
    if row["ranking"] is not None:
        pieces.append(f"{row['ranking_scope']}第 {row['ranking']} 位")
    close = row.get("close")
    ma5 = row.get("ma5")
    r1 = row.get("r1")
    r10 = row.get("r10")
    ma5_gap = ((close / ma5 - 1.0) * 100.0) if (close is not None and ma5 is not None and ma5 > 0) else None
    safety10 = (100.0 - r10) if r10 is not None else None

    if r10 is not None and r10 >= 80.0:
        pieces.append(f"10日累计涨幅达 {r10:+.2f}%（距100%严重异动红线仅余 {safety10:.2f}%，处监管触线敏感区）")
    elif ma5_gap is not None and abs(ma5_gap) <= 2.5:
        pieces.append(f"收盘距 MA5 仅 {ma5_gap:+.2f}%，处 5 日均线强弱变盘临界位")
    elif r1 is not None and r1 >= 9.5 and safety10 is not None:
        pieces.append(f"当日涨停突破（距100%异动红线尚余 {safety10:.2f}% 安全垫）")

    if r1 is not None and r1 < 0:
        pieces.append("本日收跌，多日排名靠前不等于当天承接确认")
    if row["role"] == "risk":
        pieces.append("保留旧核心/分歧样本作风险对照，非当前进攻名单")
    row["reason"] = "；".join(pieces) + "。"
    if row["price_status"] != "ok":
        row["watch"] = "先补齐并核对同日价格证据，再定义观察条件；当前不提供观察价。"
        row["invalidation"] = "日期或价格口径不能核实时，暂停该条观察。"
        return

    watch = f"以 {day} 收盘 {close:.2f} 元作前复权研究参照"
    if ma5 is not None:
        watch += f"，对照 MA5 {ma5:.2f} 元（偏离 {ma5_gap:+.2f}%）" if ma5_gap is not None else f"，对照 MA5 {ma5:.2f} 元"

    if row["role"] == "risk" or (ma5_gap is not None and ma5_gap < -2.0):
        watch += f"；当前处于均线下方弱势区，未放量收复 {ma5:.2f} 元前仅作负反馈止跌观察，严禁盲目抄底。" if ma5 is not None else "；下一交易时点仍须验证分时承接、量能及同类跟随。"
        row["watch"] = watch
        row["invalidation"] = (
            f"反抽受阻于 MA5（{ma5:.2f} 元）或再次跌破 {close * 0.96:.2f} 元走弱，结束修复假说，不以补仓替代验证。"
            if ma5 is not None
            else "反抽不能收复破位结构或再次走弱，结束修复假说，不以补仓替代验证。"
        )
        return

    if r10 is not None and r10 >= 85.0:
        trigger_ceil = close * (1.0 + max(0.01, safety10 / 200.0)) if safety10 is not None else close * 1.03
        watch += (
            f"；⚠️10日涨幅已达 {r10:+.2f}%（距100%严重异动红线仅剩 {safety10:.2f}%），"
            f"次日盘中拉升触及 {trigger_ceil:.2f} 元附近极易引发兑现避险，严禁追高打板，仅观察回踩 {ma5:.2f} 元承接。"
            if ma5 is not None
            else f"；⚠️10日涨幅已达 {r10:+.2f}%，严防触及100%异动红线冲高回落，不追高。"
        )
        row["invalidation"] = (
            f"冲高异动临界位滞涨破分时均线，或放量跌破 {close * 0.95:.2f} 元 / MA5（{ma5:.2f} 元）时停止升级。"
            if ma5 is not None
            else "相对强势或事先记录的承接结构失效时停止升级；缺分时证据不确认买点。"
        )
    elif ma5 is not None and abs(ma5_gap or 0.0) <= 2.5:
        watch += (
            f"；当前精准回踩 MA5 支撑带（{ma5 * 0.992:.2f}~{ma5 * 1.008:.2f} 元），"
            f"次日重点核验早盘回踩 {ma5:.2f} 元不破且分时放量拐头站稳均线的低吸反包信号。"
        )
        row["invalidation"] = f"放量跌破 {ma5 * 0.98:.2f} 元（有效跌破 MA5 超 2%）且 15 分钟不收回时确认均线破位失效。"
    elif r1 is not None and r1 >= 9.5:
        watch += (
            f"；当日涨停加速，次日核验竞价高开 +2%~+6%（{close * 1.02:.2f}~{close * 1.06:.2f} 元）"
            f"及分时回踩 {close * 0.975:.2f} 元不破的换手晋级动能。"
        )
        row["invalidation"] = (
            f"竞价核按钮低开或盘中炸板跌破 {close * 0.95:.2f} 元（中期防守 MA5 {ma5:.2f} 元）时停止升级。"
            if ma5 is not None
            else "相对强势或事先记录的承接结构失效时停止升级；缺分时证据不确认买点。"
        )
    else:
        watch += "；下一交易时点仍须验证分时承接、量能及同类跟随。"
        row["invalidation"] = (
            f"跌破前复权支撑 {ma5:.2f} 元（MA5）或相对强势承接结构失效时停止升级；缺分时证据不确认买点。"
            if ma5 is not None
            else "相对强势或事先记录的承接结构失效时停止升级；缺分时证据不确认买点。"
        )
    row["watch"] = watch


def _stocks(payload: dict | None, source: dict, day: str) -> dict:
    if payload is None:
        return _block(source["status"], source.get("as_of"), "暂无日期一致的股票抱团证据。", [source["warning"], _STOCK_SCOPE])
    clock_error = _stock_clock_error(payload, day)
    if clock_error:
        status, observed, warning = clock_error
        source.update(status=status, as_of=observed, warning=warning)
        return _block(status, observed, "股票证据日期不一致，暂停当前名单。", [warning, _STOCK_SCOPE])
    tracking = _obj(payload.get("tracking"))
    snapshot = _on_day(tracking.get("daily"), day)
    if not snapshot or snapshot.get("coverage_ok") is not True:
        return _block("partial", day, "当日完整跟踪截面缺失，暂停当前名单。", ["没有日期一致且覆盖完整的跟踪截面。", _STOCK_SCOPE])
    daily = _on_day(payload.get("daily"), day)
    phase = _on_day(_obj(payload.get("automatic_phases")).get("daily"), day)
    if not phase:
        latest = _obj(_obj(payload.get("automatic_phases")).get("latest"))
        phase = latest if _day(latest.get("date")) == day else {}
    current = daily.get("confirmed_proxy") if daily.get("proxy_state") == "有效" else None
    if not daily and phase.get("confirmed_proxy"):
        current = phase["confirmed_proxy"]
    ranks = _ranking(daily)
    dates, index, prices, problems = _replay_prices(_obj(payload.get("screening_replay")), day)
    valid = {}
    for raw in _records(snapshot.get("rows")):
        code = str(raw.get("code") or "").strip()
        if not code:
            continue
        if _day(raw.get("metrics_as_of")) != day or raw.get("evidence_ok") is not True:
            problems.append("部分个股证据缺失或日期不一致，已从当前名单排除。")
            continue
        valid[code] = raw
    old_cores = _old_core_codes(payload, day, current)
    risk = next((valid[c] for c in old_cores if c in valid and
                 (valid[c].get("active") is not True or valid[c].get("qualified_today") is not True)), None)
    candidates = [r for r in valid.values() if r.get("active") is True or r.get("qualified_today") is True or r.get("code") == current]

    def priority(raw: dict) -> tuple:
        code = str(raw.get("code"))
        supported = raw.get("active") is True and raw.get("qualified_today") is True
        strong = supported and ((_integer(raw.get("support_streak")) or 0) >= 5 or "旧" in str(raw.get("identity")))
        rank = _obj(ranks.get(code)).get("ranking")
        return (code != current, not strong, not supported, rank is None, rank or math.inf,
                -(_integer(raw.get("support_streak")) or 0), code)

    candidates.sort(key=priority)
    # 风险锚即使仍在 active 分歧期也只占一个名额，避免重复截断少展示一只。
    selected = [r for r in candidates if risk is None or r.get("code") != risk.get("code")][:7 if risk else 8]
    if risk:
        selected.append(risk)
    rows = []
    for raw in selected[:8]:
        code = str(raw["code"])
        metrics = _obj(raw.get("metrics"))
        calculated = _price_metrics(prices.get(code), index, len(dates))
        row_warnings = []
        conflicts = [key for key in ("r1", "r5", "r20", "dd20")
                     if _number(metrics.get(key)) is not None and calculated[key] is not None
                     and abs(float(metrics[key]) - calculated[key]) > .08]
        price_status = "ok" if calculated["close"] is not None else "missing"
        if conflicts:
            row_warnings.append("同日价格重算与报告指标冲突（" + "、".join(conflicts) + "），暂停观察价。")
            price_status = "conflict"
            calculated.update(close=None, ma5=None, previous20_close_high=None)
        elif price_status == "missing":
            row_warnings.append("缺少日期一致的完整价格序列，观察价留空。")
        for key in ("r1", "r5", "r20", "dd20"):
            if calculated[key] is None:
                calculated[key] = _number(metrics.get(key))
        active = raw.get("active") if isinstance(raw.get("active"), bool) else None
        qualified = raw.get("qualified_today") if isinstance(raw.get("qualified_today"), bool) else None
        role = "core" if code == current else "risk" if raw is risk or (active and not qualified) else "continuation" if active else "candidate"
        row = {"code": code, "name": str(raw.get("name") or code), "as_of": day,
               "identity": raw.get("identity"), "status": raw.get("status"), "role": role,
               "active": active, "qualified_today": qualified, **calculated,
               "ranking": None, "score": None, "ranking_scope": None, **ranks.get(code, {}),
               "support_streak": _integer(raw.get("support_streak")), "failed_streak": _integer(raw.get("failed_streak")),
               "price_basis": "qfq", "price_as_of": day if price_status == "ok" else None,
               "price_status": price_status, "source_reason": str(raw.get("reason") or ""), "warnings": row_warnings}
        row["above_ma5"] = row["close"] >= row["ma5"] if row["close"] is not None and row["ma5"] is not None else None
        _stock_notes(row, day)
        rows.append(row)
    problems += [warning for row in rows for warning in row["warnings"]]
    summary = f"截至 {day}，从当日正式跟踪中选取 {len(rows)} 只作价格观察。"
    if phase.get("stage"):
        summary += f"价格阶段：{phase['stage']}。"
    if current and current in valid:
        summary += f"有效价格代理：{valid[current].get('name') or current}。"
    result = _block("partial" if problems else "ok", day, summary, problems + [_STOCK_SCOPE, _PRICE_SCOPE])
    result.update(rows=rows, selection_scope="当日有效价格代理、强延续及候选；最多8只，保留最近失效旧核心作风险对照。")
    return result


def _history_days(output: Path, data: Path, day: str) -> list[str]:
    """有交易日历时保留缺报日，否则只比较最近三份已存在的报告。"""
    try:
        with (data / "trading_calendar_cache.csv").open(encoding="utf-8-sig", newline="") as handle:
            calendar = sorted({d for row in csv.DictReader(handle) if (d := _day(row.get("trade_date") or row.get("date"))) and d <= day})
        if day in calendar:
            return calendar[-3:]
    except (OSError, UnicodeError, csv.Error):
        pass
    prior = [d for d in _dated_paths(output / "research_briefs", "research_brief_") if d < day]
    return sorted(prior + [day])[-3:]


def _sector_date_matches(row: dict, day: str) -> bool:
    return all(_day(row[key]) == day for key in ("as_of", "source_date", "date") if row.get(key) is not None)


def _sectors(payload: dict | None, source: dict, day: str, output: Path, data: Path, sources: list[dict]) -> dict:
    if payload is None:
        return _block(source["status"], source.get("as_of"), "暂无日期一致的题材涨停样本。", [source["warning"], _SECTOR_SCOPE])
    warnings, history = [], {day: payload}
    days = _history_days(output, data, day)
    for previous in days:
        if previous == day:
            continue
        loaded, historical_source = _load_report(output / "research_briefs", "research_brief_", previous, "题材历史", "report_date")
        sources.append(historical_source)
        if loaded is not None:
            history[previous] = loaded
        else:
            warnings.append(f"缺少日期一致的 {previous} 题材报告，历史值留空。")
    if len(days) < 3:
        warnings.append("不足三份可比的题材报告，未补造历史变化。")
    candidates = []
    for raw in _records(payload.get("sectors")):
        if not raw.get("name"):
            continue
        if not _sector_date_matches(raw, day):
            warnings.append("部分题材条目日期不一致，已排除。")
            continue
        candidates.append(raw)
    candidates.sort(key=lambda r: tuple(-v if v is not None else math.inf for v in
                                       (_integer(r.get("limit_up_count")), _integer(r.get("streak_count")), _integer(r.get("max_height")))) + (str(r["name"]),))
    rows = []
    for raw in candidates[:5]:
        name = str(raw["name"])
        reps, row_warnings = [], []
        for stock in _records(raw.get("stocks")):
            if not stock.get("code") or _day(stock.get("source_date")) != day:
                row_warnings.append("部分代表股缺少同日证据，已排除。")
                continue
            reps.append({"code": str(stock["code"]), "name": str(stock.get("name") or stock["code"]),
                         "sub_sector": stock.get("sub_sector"), "as_of": day,
                         "current_height": _integer(stock.get("current_height")), "board_type": stock.get("board_type")})
        values = {key: _integer(raw.get(key), signed=key == "change") for key in ("limit_up_count", "streak_count", "max_height", "change")}
        if any(values[key] is None for key in ("limit_up_count", "streak_count", "max_height")):
            row_warnings.append("部分题材统计缺失，保留空值，不按零处理。")
        series = []
        for observed in days:
            old = next((r for r in _records(_obj(history.get(observed)).get("sectors"))
                        if r.get("name") == name and _sector_date_matches(r, observed)), None)
            series.append({"date": observed, "present": old is not None,
                           "status": "ok" if old is not None else "not_listed" if observed in history else "missing",
                           **{key: _integer(_obj(old).get(key), signed=key == "change") for key in values}})
        facts = [f"涨停 {values['limit_up_count']} 家" if values["limit_up_count"] is not None else "涨停数量缺失",
                 f"连板 {values['streak_count']} 家" if values["streak_count"] is not None else "连板家数缺失",
                 f"最高 {values['max_height']} 板" if values["max_height"] is not None else "高度缺失"]
        if values["change"] is not None:
            facts.append(f"来源记录较前一交易日 {values['change']:+d} 家")
        rep_with_h = [
            f"{r['name']}({r['current_height']}板)" if isinstance(r.get("current_height"), int) and r["current_height"] > 0 else r["name"]
            for r in reps[:3]
        ]
        reference = "、".join(rep_with_h) or "有同日证据的代表股"
        zt_cnt = values.get("limit_up_count") or 0
        st_cnt = values.get("streak_count") or 0
        chg_val = values.get("change") or 0
        lead_rep = rep_with_h[0] if rep_with_h else "代表高标"
        sub_reps = "、".join(rep_with_h[1:]) if len(rep_with_h) > 1 else ""

        if zt_cnt >= 10 and st_cnt >= 3:
            facts.append(f"板块梯队结构完整（高标 {lead_rep} 领衔）")
            watch_txt = (
                f"核心盯盘 {lead_rep} 的竞价承接与空间突破，同时核验中位梯队 {sub_reps} 的换手卡位及首板扩散；一字封板须另验可成交性。"
                if sub_reps
                else f"重点盯盘 {reference} 的竞价承接、连板晋级和新增涨停扩散；一字封板须另验可成交性。"
            )
            inv_txt = f"若龙头 {lead_rep} 炸板大面且中位梯队（{sub_reps or '同题材跟风'}）批量掉队、涨停样本收缩至 5 家以下时降低优先级。"
        elif chg_val <= -6 and st_cnt <= 1:
            facts.append(f"涨停大幅退潮收缩（连板仅剩 {lead_rep} 独苗）")
            watch_txt = f"板块后排大幅收缩，仅观察独苗高标 {lead_rep} 能否吸引题材资金回流及 {sub_reps or '前排股'} 止跌承接，严禁盲目接力后排跟风。"
            inv_txt = f"若高标 {lead_rep} 竞价转弱或冲高断板、板块无首板助攻回流，确认退潮延续并放弃该方向。"
        else:
            watch_txt = f"比较 {reference} 的分时换手承接、同身位卡位晋级和新增首板扩散；一字封板须另验可成交性。"
            inv_txt = f"代表股（{reference}）同步走弱且板块涨停样本继续收缩时降低观察优先级；单股脉冲拉升不能确认整个方向。"

        rows.append({"name": name, "as_of": day, "rank": _integer(raw.get("rank")), **values,
                     "streak_count_label": "连板家数", "scope": "项目题材归类/涨停样本",
                     "history": series, "stocks": reps[:3],
                     "subsectors": _unique([str(r["sub_sector"]) for r in reps if r.get("sub_sector")]),
                     "reason": "；".join(facts) + "。", "watch": watch_txt,
                     "invalidation": inv_txt,
                     "warnings": _unique(row_warnings)})
        warnings.extend(row_warnings)
    if not candidates:
        warnings.append("当日报告没有日期一致的题材条目。")
    result = _block("partial" if warnings else "ok", day,
                    f"截至 {day}，观察 {len(rows)} 个项目题材的涨停样本；比较最近 {len(days)} 个报告日。", warnings + [_SECTOR_SCOPE])
    result.update(rows=rows, history_dates=days)
    return result


def build_crowding_plan_data(report_date, *, output_dir=None, data_dir=None, report_context=None) -> dict:
    """读取指定报告日的股票/题材研究证据；缺失、过期与未来输入不降级成当天事实。"""
    day = _day(report_date)
    if day is None:
        raise ValueError("report_date 必须是有效报告日期")
    output = Path(output_dir) if output_dir is not None else _ROOT / "output"
    data = Path(data_dir) if data_dir is not None else Path(os.environ.get("QF_DATA_DIR") or _ROOT / "data")
    stock_payload, stock_source = _load_report(output, "抱团周期分析_", day, "股票抱团", "requested_date")
    sources = [stock_source]
    stocks = _stocks(stock_payload, stock_source, day)
    context = _obj(report_context)
    brief, sector_source = None, None
    if isinstance(context.get("research_brief"), dict):
        proposed = context["research_brief"]
        valid = _day(proposed.get("report_date")) == day and (context.get("report_date") is None or _day(context["report_date"]) == day)
        sector_source = {"kind": "题材抱团", "path": "report_context.research_brief", "as_of": _day(proposed.get("report_date")),
                         "status": "ok" if valid else "invalid"}
        if valid:
            brief = proposed
        else:
            sector_source["warning"] = "内存题材简报日期不匹配，已拒绝；只允许使用独立核对的同日文件。"
            sources.append(sector_source)
            sector_source = None
    if sector_source is None:
        brief, sector_source = _load_report(output / "research_briefs", "research_brief_", day, "题材抱团", "report_date")
    sources.append(sector_source)
    sectors = _sectors(brief, sector_source, day, output, data, sources)
    return {"report_date": day, "stocks": stocks, "sectors": sectors, "sources": sources,
            "warnings": _unique([s["warning"] for s in sources if s.get("warning")])}
