"""Public API observations, isolated from price qualification and historical replay.

Default report builds read an explicit local cache only. Every record retains the
provider date (or None), observation time, route and code-level match basis.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
CACHE_NAME = "crowding_external_evidence.json"
LIMITATION = (
    "外部信息只作当前报告的补充观察，不改变价格资格、评分、入池/退出或历史推演。"
    "抓取时间不是历史已知时间；无日期榜单仅代表抓取时所见，板块成员不等于龙头。"
    "接口仅覆盖返回的榜单及首屏电报/异动，不代表全市场完整消息。"
)

ROUTES = [
    ("cls_mainline", "财联社", "content", "/v2/dingPan/mainline", {}),
    ("cls_tuyere", "财联社", "content", "/v2/todayTuyere", {}),
    ("cls_roll", "财联社", "content", "/v1/roll/get_roll_list", {}),
    ("cls_ranking", "财联社", "quote", "/v2/quote/a/stock/ranking",
     {"topic": "all", "style": "TOPIC-STYLE-A-240223", "index": 1, "way": "change",
      "reverse": 1, "deviation_num": 60, "include_st": 1, "fields": "change,last_px"}),
    ("kpl_resumption", "开盘啦", "kpl", "DailyLimitResumption.GetPlateInfo_w38", {"st": 100, "Index": 0}),
    ("kpl_tianti", "开盘啦", "kpl", "FuPanLa.GetZhangTingTianTi", {}),
    ("kpl_timeline", "开盘啦", "kpl", "FuPanLa.GetPMSL_PMLD", {"st": 30, "Index": 0}),
]


def stock_code(value):
    """Accept explicit equity identifiers only, never names or plate/index codes."""
    text = str(value or "").strip().lower()
    if re.fullmatch(r"\d{6}\.(sh|sz|bj)", text):
        text = text[-2:] + text[:6]
    if re.fullmatch(r"\d{6}", text):
        prefix = "sh" if text.startswith(("60", "68")) else ("bj" if text.startswith(("4", "8", "9")) else "sz")
        text = prefix + text
    if not re.fullmatch(r"(sh|sz|bj)\d{6}", text):
        return None
    prefix, digits = text[:2], text[2:]
    allowed = {"sh": ("60", "68"), "sz": ("00", "30"), "bj": ("4", "8", "9")}
    return text if digits.startswith(allowed[prefix]) else None


def source_date(value):
    if value is None or value == "" or value == 0:
        return None
    try:
        text = str(value).strip()
        if re.fullmatch(r"\d{8}", text):
            return datetime.strptime(text, "%Y%m%d").date().isoformat()
        if re.match(r"^\d{4}-\d{2}-\d{2}", text):
            return date.fromisoformat(text[:10]).isoformat()
        if re.fullmatch(r"\d{10}(\d{3})?", text):
            timestamp = float(text) / (1000 if len(text) == 13 else 1)
            return datetime.fromtimestamp(timestamp, TZ).date().isoformat()
    except (ValueError, OverflowError, OSError):
        pass
    return None


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _text(value, limit=240):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _ok(raw, provider):
    if not isinstance(raw, dict) or raw.get("error"):
        return False
    status = raw.get("errcode") if provider == "开盘啦" else raw.get("code", raw.get("errno"))
    return str(status) in (("0",) if provider == "开盘啦" else ("0", "200"))


def _request(route, timeout):
    key, provider, host, path, params = route
    from data_sources.cls_client import CLSClient
    from data_sources.kaipanla_client import KaiPanLaClient
    client = KaiPanLaClient(timeout=timeout) if host == "kpl" else CLSClient(timeout=timeout)
    try:
        if host == "kpl":
            raw = client._call(*path.split(".", 1), **params)
            url = client.BASE_URL
        else:
            base = client.CONTENT_BASE if host == "content" else client.QUOTE_BASE
            raw = client._get(base, path, **params)
            url = base + path
        return dict(key=key, provider=provider, route=path, url=url, params=params, raw=raw)
    finally:
        client.session.close()


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def refresh_external_evidence(as_of, data_dir, *, timeout=12, now=None, fetcher=None):
    """Explicit network refresh; immutable raw snapshots and visible failure state."""
    as_of = date.fromisoformat(as_of).isoformat()
    now = now or datetime.now(TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ)
    fetcher = fetcher or _request
    def safe_fetch(route):
        try:
            return fetcher(route, timeout)
        except Exception as exc:
            key, provider, _, path, params = route
            return dict(key=key, provider=provider, route=path, url=None, params=params,
                        raw={"error": f"{type(exc).__name__}: {_text(exc, 160)}"})
    with ThreadPoolExecutor(max_workers=4) as pool:
        endpoints = list(pool.map(safe_fetch, ROUTES))
    main = next((x for x in endpoints if x["key"] == "cls_mainline"), {})
    tuyere = next((x for x in endpoints if x["key"] == "cls_tuyere"), {})
    plates = set()
    if _ok(main.get("raw"), "财联社"):
        plates.update(w.get("plate_code") for w in _list(_dict(main["raw"].get("data")).get("winds")) if isinstance(w, dict))
    if _ok(tuyere.get("raw"), "财联社"):
        plates.update(w.get("plate_code") for w in _list(_dict(tuyere["raw"].get("data")).get("today_tuyere")) if isinstance(w, dict))
    # Bounded follow-up: use explicit plate member codes, never text-only sector guesses.
    extra = [(f"cls_members:{p}", "财联社", "quote", "/v2/quote/a/plate/tuyere/stocks", {"plate_code": p})
             for p in sorted(str(p) for p in plates if p)[:8]]
    if extra:
        with ThreadPoolExecutor(max_workers=4) as pool:
            endpoints.extend(pool.map(safe_fetch, extra))
    snapshot = dict(schema="crowding-external/v1", requested_as_of=as_of,
                    fetched_at=now.astimezone(TZ).isoformat(), endpoints=endpoints)
    root = Path(data_dir)
    archive = root / "crowding_external_snapshots" / (now.astimezone(TZ).strftime("%Y%m%dT%H%M%S%f") + ".json")
    _write_json(archive, snapshot)
    if not any(_ok(x.get("raw"), x["provider"]) for x in endpoints):
        _write_json(root / "crowding_external_last_failure.json", snapshot)
        raise RuntimeError("两家外部接口均未成功；原成功缓存保留，本次失败已单独留档。")
    _write_json(root / CACHE_NAME, snapshot)
    return snapshot


def normalize_snapshot(snapshot):
    """Parse raw provider fields with no invented timestamps or semantic upgrades."""
    records, topics, statuses = [], [], []
    endpoints = {x["key"]: x for x in snapshot.get("endpoints", []) if isinstance(x, dict) and x.get("key")}
    observed = snapshot.get("fetched_at")

    def add(endpoint, category, symbol, name, *, theme="", detail="", timestamp=None,
            basis="接口个股代码", url=None, risk=False, reference_id=None):
        symbol = stock_code(symbol)
        if not symbol:
            return
        item = dict(provider=endpoint["provider"], category=category, code=symbol,
                    name=_text(name, 40), theme=_text(theme, 80), detail=_text(detail),
                    source_date=source_date(timestamp), observed_at=observed,
                    match_basis=basis, route=endpoint["route"], url=url or endpoint.get("url"),
                    risk=risk, reference_id=reference_id, endpoint_key=endpoint["key"])
        identity = json.dumps([item[k] for k in ("provider", "category", "code", "theme", "source_date", "detail", "reference_id")], ensure_ascii=False)
        item["id"] = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        records.append(item)

    for endpoint in endpoints.values():
        raw = _dict(endpoint.get("raw"))
        status = dict(key=endpoint["key"], provider=endpoint["provider"], route=endpoint["route"],
                      url=endpoint.get("url"), success=_ok(raw, endpoint["provider"]),
                      source_date=source_date(raw.get("date") or raw.get("Date")),
                      observed_at=observed, error=_text(raw.get("error") or raw.get("msg") or raw.get("errmsg"), 160))
        if status["success"]:
            status["error"] = ""
        statuses.append(status)
        if not status["success"]:
            continue
        key = endpoint["key"]
        data = _dict(raw.get("data"))
        if key == "cls_mainline":
            for chance in _list(data.get("chances")):
                for plate in _list(_dict(chance).get("plates")):
                    plate = _dict(plate)
                    article = _dict(plate.get("article"))
                    topic = dict(provider="财联社", category="主线", title=_text(plate.get("name")),
                                 detail=_text(plate.get("reason")), source_date=source_date(plate.get("time")),
                                 observed_at=observed, plate_code=plate.get("code"))
                    topics.append(topic)
                    for field in ("faucet_1", "faucet_2", "faucet_3"):
                        stock = _dict(plate.get(field))
                        add(endpoint, "主线龙头", stock.get("stock_code"), stock.get("stock_name"),
                            theme=plate.get("name"), detail=stock.get("reason"), timestamp=plate.get("time"),
                            url=f"https://www.cls.cn/detail/{article['id']}" if str(article.get("id", "")).isdigit() else None,
                            reference_id=article.get("id"))
            for wind in _list(data.get("winds")):
                wind = _dict(wind)
                topics.append(dict(provider="财联社", category="风口", title=_text(wind.get("title")),
                                   detail=_text(wind.get("interpret")), source_date=source_date(wind.get("time")),
                                   observed_at=observed, plate_code=wind.get("plate_code")))
                for stock in _list(wind.get("faucet_stocks")):
                    stock = _dict(stock)
                    add(endpoint, "风口龙头", stock.get("stock_code"), stock.get("stock_name"),
                        theme=wind.get("title"), detail=wind.get("interpret"), timestamp=wind.get("time"))
            styles = _dict(data.get("styles"))
            for field in ("index_1", "index_2"):
                for stock in _list(_dict(styles.get(field)).get("stacks")):
                    stock = _dict(stock)
                    add(endpoint, "区间强势", stock.get("stock_code"), stock.get("stock_name"),
                        theme=styles.get("title"), detail=f"接口区间涨幅 {stock.get('rise_range', '未标注')}（不是本地收益）",
                        timestamp=styles.get("time"))
        elif key == "cls_tuyere":
            for wind in _list(data.get("today_tuyere")):
                wind = _dict(wind)
                if not any(t.get("plate_code") == wind.get("plate_code") and t["category"] == "风口" for t in topics):
                    topics.append(dict(provider="财联社", category="风口", title=_text(wind.get("title")),
                                       detail=_text(wind.get("interpret")), source_date=source_date(wind.get("time")),
                                       observed_at=observed, plate_code=wind.get("plate_code")))
        elif key.startswith("cls_members:"):
            plate_code = key.split(":", 1)[1]
            theme = next((t["title"] for t in topics if t.get("plate_code") == plate_code), plate_code)
            for stock in _list(raw.get("data")):
                stock = _dict(stock)
                add(endpoint, "风口板块成员", stock.get("secu_code"), stock.get("secu_name"),
                    theme=theme, basis="接口板块成员代码", timestamp=stock.get("date"))
        elif key == "cls_ranking":
            # One bounded first screen; do not claim complete market coverage or a multi-day ranking.
            for position, stock in enumerate(_list(data.get("items"))[:60], start=1):
                stock = _dict(stock)
                add(endpoint, "当日涨幅榜", stock.get("secu_code"), stock.get("secu_name"),
                    detail=f"首屏序位 {position}；接口当日涨跌幅 {_text(stock.get('change') or '未标注', 24)}（非两月/半年收益）",
                    timestamp=stock.get("date") or raw.get("date") or data.get("date"),
                    basis="接口涨幅榜个股代码")
        elif key == "cls_roll":
            for article in _list(data.get("roll_data")):
                article = _dict(article)
                if article.get("is_ad") or not article.get("id"):
                    continue
                for stock in _list(article.get("stock_list")):
                    stock = _dict(stock)
                    add(endpoint, "关联电报", stock.get("StockID") or stock.get("code"), stock.get("Name") or stock.get("name"),
                        detail=article.get("title") or article.get("content"), timestamp=article.get("ctime"),
                        url=f"https://www.cls.cn/detail/{article['id']}" if str(article.get("id", "")).isdigit() else None,
                        reference_id=article.get("id"))
        elif key == "kpl_resumption":
            for plate in _list(raw.get("list")):
                plate = _dict(plate)
                topics.append(dict(provider="开盘啦", category="涨停题材", title=_text(plate.get("ZSName")),
                                   detail="涨停复盘分组，不等于市场主线确认", source_date=status["source_date"],
                                   observed_at=observed, plate_code=plate.get("ZSCode")))
                for stock in _list(plate.get("StockList")):
                    if isinstance(stock, list) and len(stock) >= 18:
                        add(endpoint, "涨停题材", stock[0], stock[1], theme=plate.get("ZSName"),
                            detail=stock[17], timestamp=raw.get("date"), basis="接口复盘成员代码")
        elif key == "kpl_tianti":
            for stock in _list(raw.get("StockList")):
                if isinstance(stock, list) and len(stock) >= 6:
                    add(endpoint, "连板梯队", stock[0], stock[1], theme=stock[5],
                        detail=f"接口连板高度 {stock[2]}；连板不等于资金抱团确认", timestamp=raw.get("date") or raw.get("Date"))
        elif key == "kpl_timeline":
            for event in _list(raw.get("List")):
                event = _dict(event)
                for stock in _list(event.get("StockList")):
                    if isinstance(stock, list) and len(stock) >= 2:
                        add(endpoint, "盘面异动", stock[0], stock[1], theme=event.get("ZSName"), detail=event.get("Detail"),
                            timestamp=event.get("TimeMin") or raw.get("date"), risk=str(event.get("TagShuXing")) == "0")
    records = list({x["id"]: x for x in records}.values())
    for status in statuses:
        status["record_count"] = sum(x["endpoint_key"] == status["key"] for x in records)
    return dict(records=records, topics=topics, endpoints=statuses)


def build_external_evidence(report, *, data_dir=None, snapshot=None):
    """Attach current observations without mutating any underlying price pools."""
    failure = None
    if snapshot is None and data_dir is not None:
        path = Path(data_dir) / CACHE_NAME
        if path.exists():
            try:
                snapshot = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                failure = "外部缓存无法读取；价格筛选仍独立运行，请显式刷新外部数据。"
        failed_path = Path(data_dir) / "crowding_external_last_failure.json"
        if failed_path.exists():
            try:
                failed = json.loads(failed_path.read_text(encoding="utf-8"))
                if not snapshot or str(failed.get("fetched_at", "")) > str(snapshot.get("fetched_at", "")):
                    failure = "最近外部刷新全部失败；以下如有旧缓存均为保留数据，不是本次成功抓取。"
            except (ValueError, OSError):
                failure = "失败留档不可读；无法确认最近一次外部刷新结果。"
    result = dict(schema="crowding-external-view/v1", requested_date=report["requested_date"],
                  market_as_of=report.get("market_as_of"), fetched_at=None, mode="missing",
                  limitation=LIMITATION, warnings=[failure] if failure else [],
                  endpoints=[], topics=[], matches=[], external_only=[], excluded_count=0)
    if not snapshot:
        result["warnings"].append("尚无外部缓存；使用 --refresh-external-evidence 显式获取两家接口。")
        return result
    if snapshot.get("schema") != "crowding-external/v1":
        result["warnings"].append("外部缓存版本不受支持；请重新刷新。")
        return result
    result["fetched_at"] = snapshot.get("fetched_at")
    observed_date = source_date(snapshot.get("fetched_at"))
    requested = report["requested_date"]
    if not observed_date:
        result["mode"] = "invalid_time"
        result["warnings"].append("外部缓存缺少有效抓取时点，禁止用于当前或历史匹配。")
        return result
    parsed = normalize_snapshot(snapshot)
    if observed_date > requested or str(snapshot.get("requested_as_of", "")) > requested:
        result["mode"] = "future_snapshot"
        result["excluded_count"] = len(parsed["records"])
        result["warnings"].append(f"缓存抓取于{observed_date}，晚于研究截止{requested}；全部外部消息隔离，不回填历史。")
        return result
    age = (date.fromisoformat(requested) - date.fromisoformat(observed_date)).days
    result["mode"] = "stale" if age > 3 or failure else "supplement"
    if age > 3:
        result["warnings"].append(f"外部缓存距报告日已{age}个自然日；仅旧消息留档，不表示当前仍强势。")
    result["endpoints"] = parsed["endpoints"]
    result["topics"] = [x for x in parsed["topics"] if not x["source_date"] or x["source_date"] <= requested]
    result["warnings"].extend(f"{x['provider']} {x['route']} 获取失败：{x['error'] or '接口返回非成功状态'}" for x in parsed["endpoints"] if not x["success"])
    tracking = report.get("tracking") or {}
    archive = {x["code"]: x for x in tracking.get("stocks", [])}
    daily = tracking.get("daily") or []
    latest = {x["code"]: x for x in daily[-1].get("rows", [])} if daily else {}
    window = (report.get("screening_replay") or {}).get("windows", {}).get("2") or {}
    days = window.get("days") or []
    selected = set(days[-1].get("selected", [])) if days else set()
    ever = {c for d in days for c in d.get("selected", [])}
    half_days = (report.get("screening_replay") or {}).get("windows", {}).get("6", {}).get("days") or []
    half_selected = set(half_days[-1].get("selected", [])) if half_days else set()
    half_ever = {c for d in half_days for c in d.get("selected", [])}
    def admission_dates(history):
        dates = {}
        for daily_record in history:
            for symbol in daily_record.get("selected", []):
                pair = dates.setdefault(symbol, [daily_record["date"], daily_record["date"]])
                pair[1] = daily_record["date"]
        return dates
    two_dates, half_dates = admission_dates(days), admission_dates(half_days)
    names = (report.get("screening_replay") or {}).get("names", {})
    grouped = {}
    for record in parsed["records"]:
        sd = record["source_date"]
        if sd and sd > requested:
            result["excluded_count"] += 1
            continue
        record["date_status"] = "未标注日期" if sd is None else ("与价格日一致" if sd == report.get("market_as_of") else "非价格当日")
        record["current_dated_context"] = result["mode"] == "supplement" and sd is not None and sd == report.get("market_as_of")
        grouped.setdefault(record["code"], []).append(record)
    for symbol, records in grouped.items():
        current = latest.get(symbol, archive.get(symbol, {}))
        name = current.get("name") or names.get(symbol) or records[0]["name"] or symbol
        row = dict(code=symbol, name=name, external_names=sorted({x["name"] for x in records if x["name"]}),
                   price_selected=symbol in selected, ever_selected_2m=symbol in ever,
                   price_selected_6m=symbol in half_selected, ever_selected_6m=symbol in half_ever,
                   first_selected_2m=two_dates.get(symbol, [None, None])[0],
                   last_selected_2m=two_dates.get(symbol, [None, None])[1],
                   first_selected_6m=half_dates.get(symbol, [None, None])[0],
                   last_selected_6m=half_dates.get(symbol, [None, None])[1],
                   formally_tracked=symbol in archive, tracking_status=current.get("status"),
                   identity=current.get("identity"), sources=sorted({x["provider"] for x in records}),
                   categories=sorted({x["category"] for x in records}), themes=sorted({x["theme"] for x in records if x["theme"]}),
                   risk_count=sum(x["risk"] for x in records), records=records)
        if symbol in selected or symbol in archive or symbol in ever or symbol in half_ever:
            row["pool_context"] = "当前价格入选" if symbol in selected else ("正式追踪留档" if symbol in archive else ("仅两月研究历史入选" if symbol in ever else "仅半年研究历史入选"))
            result["matches"].append(row)
        else:
            row["pool_context"] = "仅外部提及，未取得本地价格资格"
            result["external_only"].append(row)
    for key in ("matches", "external_only"):
        result[key].sort(key=lambda x: (not x["price_selected"], -len(x["sources"]), x["code"]))
    result["summary"] = dict(matched_stocks=len(result["matches"]), current_selected=sum(x["price_selected"] for x in result["matches"]),
                             current_selected_6m=sum(x["price_selected_6m"] for x in result["matches"]),
                             historical_6m_only=sum(x["ever_selected_6m"] and not x["ever_selected_2m"] and not x["formally_tracked"] for x in result["matches"]),
                             two_source_mentions=sum(len(x["sources"]) > 1 for x in result["matches"]),
                             external_only=len(result["external_only"]), undated_records=sum(x["source_date"] is None for rows in (result["matches"], result["external_only"]) for row in rows for x in row["records"]))
    return result
