# -*- coding: utf-8 -*-
"""个股催化归因 (独立模块)。

用途:
    强势股为什么走强? 复盘时如果只看"3板"这个结果, 没消息面很容易口水化。
    本模块拉三路真实数据源, 让归因不再靠猜:
      1. 东财个股新闻 (search-api-web)   → 最近相关新闻
      2. 巨潮公告      (cninfo.com.cn)   → 官方公告 (订单/重组/业绩预告)
      3. 东财龙虎榜    (datacenter-web)  → 上榜原因 + 席位 (机构 or 游资)

设计原则:
  - 全部走 em_get() 限流, 遵守东财 1s 最小间隔, 不会被封
  - 单只股一次归因约 3-4 秒 (三路串行, 每路 ~1s 请求 + 1s 节流)
  - 无数据静默返回空 dict, 上层看到空就说"无数据源可查", 不编造原因
  - 用于**复盘时人工触发**, 不入盘中批量流程

用法:
    from catalyst_attribution import attribute_stock
    info = attribute_stock('sh601606', '长城军工', trade_date='2026-07-27')
    # info = {
    #     'news': [{title, time, source, url}, ...],
    #     'announcements': [{title, type, date, url}, ...],
    #     'dragon_tiger': {records: [...], seats: {...}, institution: {...}}
    # }
"""
import re
import json
import time
import random
import hashlib
import requests
from datetime import datetime, timedelta

# 与 em_stock_plates 一致的东财会话/UA
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_SESSION = requests.Session()
_SESSION.trust_env = False
_SESSION.proxies = {'http': None, 'https': None}
_SESSION.headers.update({"User-Agent": UA})

# 东财限流: 1s 最小间隔 + 随机抖动 (与 SKILL 中 em_get 同规格)
_EM_MIN_INTERVAL = 1.0
_em_last_call = [0.0]


def _em_get(url, params=None, headers=None, timeout=15):
    """东财统一请求入口: 节流 + 会话复用 + 绕系统代理。"""
    wait = _EM_MIN_INTERVAL - (time.time() - _em_last_call[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.3))
    try:
        return _SESSION.get(url, params=params, headers=headers, timeout=timeout)
    finally:
        _em_last_call[0] = time.time()


# ─────────────────────────────────────────────────────────────
# 1. 东财个股新闻
# ─────────────────────────────────────────────────────────────
_NEWS_URL = "https://search-api-web.eastmoney.com/search/jsonp"


def _strip_code(code):
    """sh601606 / 601606.SH / 601606 → 601606"""
    c = str(code).lower().strip()
    if c.startswith(('sh', 'sz', 'bj')):
        c = c[2:]
    if '.' in c:
        c = c.split('.')[0]
    return c


def fetch_news(code, page_size=10):
    """东财个股新闻 (JSONP)。返回列表, 失败或无数据返回 []。

    坑 (SKILL 已记录):
      - 部分大陆住宅 IP 会只返回 passportWeb (股民资料) 无 cmsArticleWebOld → []
      - result.cmsArticleWebOld 本身就是文章列表, 不是 {list:[...]}
    """
    code = _strip_code(code)
    inner = json.dumps({
        "uid": "", "keyword": code, "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "default",
                  "pageIndex": 1, "pageSize": page_size, "preTag": "", "postTag": ""}},
    }, separators=(',', ':'))
    params = {"cb": "jQuery_news", "param": inner}
    headers = {"Referer": "https://so.eastmoney.com/"}
    try:
        r = _em_get(_NEWS_URL, params=params, headers=headers, timeout=15)
        text = r.text
        json_str = text[text.index("(") + 1: text.rindex(")")]
        d = json.loads(json_str)
    except Exception:
        return []

    articles = d.get("result", {}).get("cmsArticleWebOld", []) or []
    rows = []
    for a in articles:
        rows.append({
            "title": re.sub(r'<[^>]+>', '', a.get("title", "")),
            "content": re.sub(r'<[^>]+>', '', a.get("content", ""))[:200],
            "time": a.get("date", ""),
            "source": a.get("mediaName", ""),
            "url": a.get("url", ""),
        })
    return rows


# ─────────────────────────────────────────────────────────────
# 2. 巨潮公告
# ─────────────────────────────────────────────────────────────
_CNINFO_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
_CNINFO_ORGID_MAP = {}


def _cninfo_orgid(code):
    """动态查真实 orgId。硬编码 gssx0{code} 对 601xxx 大面积失效, 必须查官方映射表。"""
    global _CNINFO_ORGID_MAP
    if not _CNINFO_ORGID_MAP:
        try:
            r = _SESSION.get("http://www.cninfo.com.cn/new/data/szse_stock.json",
                             timeout=15)
            _CNINFO_ORGID_MAP = {s["code"]: s["orgId"]
                                 for s in r.json().get("stockList", [])}
        except Exception:
            pass
    org = _CNINFO_ORGID_MAP.get(code)
    if org:
        return org
    if code.startswith("6"):
        return f"gssh0{code}"
    if code.startswith(("8", "4")):
        return f"gsbj0{code}"
    return f"gssz0{code}"


def _cninfo_ts_to_date(ts):
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
    return str(ts)[:10] if ts else ""


def fetch_announcements(code, page_size=20):
    """巨潮公告全文检索。失败返回 []。"""
    code = _strip_code(code)
    org_id = _cninfo_orgid(code)
    payload = {
        "stock": f"{code},{org_id}", "tabName": "fulltext",
        "pageSize": str(page_size), "pageNum": "1",
        "column": "", "category": "", "plate": "", "seDate": "",
        "searchkey": "", "secid": "", "sortName": "", "sortType": "",
        "isHLtitle": "true",
    }
    headers = {
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://www.cninfo.com.cn/new/disclosure",
        "Origin": "https://www.cninfo.com.cn",
    }
    try:
        r = _SESSION.post(_CNINFO_URL, data=payload, headers=headers, timeout=15)
        d = r.json()
    except Exception:
        return []

    rows = []
    for item in d.get("announcements", []) or []:
        rows.append({
            "title": item.get("announcementTitle", ""),
            "type": item.get("announcementTypeName", ""),
            "date": _cninfo_ts_to_date(item.get("announcementTime")),
            "url": f"https://www.cninfo.com.cn/new/disclosure/detail?"
                   f"annoId={item.get('announcementId', '')}",
        })
    return rows


# ─────────────────────────────────────────────────────────────
# 3. 东财龙虎榜
# ─────────────────────────────────────────────────────────────
_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def _em_datacenter(report_name, filter_str, page_size=50,
                   sort_columns="", sort_types="-1"):
    """东财 datacenter 统一入口。失败返回 []。"""
    params = {
        "reportName": report_name, "columns": "ALL",
        "filter": filter_str, "pageNumber": "1", "pageSize": str(page_size),
        "sortColumns": sort_columns, "sortTypes": sort_types,
        "source": "WEB", "client": "WEB",
    }
    try:
        r = _em_get(_DATACENTER_URL, params=params, timeout=15)
        d = r.json()
    except Exception:
        return []
    if d.get("result") and d["result"].get("data"):
        return d["result"]["data"]
    return []


def fetch_dragon_tiger(code, trade_date, look_back=30):
    """龙虎榜聚合: 近 look_back 天上榜记录 + 最近一次买卖席位 TOP5 + 机构统计。
    trade_date: 'YYYY-MM-DD'。失败返回空结构。"""
    code = _strip_code(code)
    try:
        start = datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=look_back)
    except Exception:
        return {"records": [], "seats": {"buy": [], "sell": []},
                "institution": {"buy_amt": 0, "sell_amt": 0, "net_amt": 0}}
    start_str = start.strftime("%Y-%m-%d")

    # 1. 上榜记录
    data = _em_datacenter(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        filter_str=f"(TRADE_DATE>='{start_str}')(TRADE_DATE<='{trade_date}')"
                   f"(SECURITY_CODE=\"{code}\")",
        page_size=50, sort_columns="TRADE_DATE", sort_types="-1",
    )
    records = []
    for row in data:
        records.append({
            "date": str(row.get("TRADE_DATE", ""))[:10],
            "reason": row.get("EXPLANATION", ""),
            "net_buy": round((row.get("BILLBOARD_NET_AMT") or 0) / 10000, 1),
            "turnover": round(float(row.get("TURNOVERRATE") or 0), 2),
        })

    # 2. 最近一次的席位
    seats = {"buy": [], "sell": []}
    buy_data = sell_data = []
    if records:
        latest = records[0]["date"]
        buy_data = _em_datacenter(
            "RPT_BILLBOARD_DAILYDETAILSBUY",
            filter_str=f"(TRADE_DATE='{latest}')(SECURITY_CODE=\"{code}\")",
            page_size=10, sort_columns="BUY", sort_types="-1",
        )
        for row in buy_data[:5]:
            seats["buy"].append({
                "name": row.get("OPERATEDEPT_NAME", ""),
                "buy_amt": round((row.get("BUY") or 0) / 10000, 1),
                "sell_amt": round((row.get("SELL") or 0) / 10000, 1),
                "net": round((row.get("NET") or 0) / 10000, 1),
            })
        sell_data = _em_datacenter(
            "RPT_BILLBOARD_DAILYDETAILSSELL",
            filter_str=f"(TRADE_DATE='{latest}')(SECURITY_CODE=\"{code}\")",
            page_size=10, sort_columns="SELL", sort_types="-1",
        )
        for row in sell_data[:5]:
            seats["sell"].append({
                "name": row.get("OPERATEDEPT_NAME", ""),
                "buy_amt": round((row.get("BUY") or 0) / 10000, 1),
                "sell_amt": round((row.get("SELL") or 0) / 10000, 1),
                "net": round((row.get("NET") or 0) / 10000, 1),
            })

    # 3. 机构席位 (OPERATEDEPT_CODE == "0")
    institution = {"buy_amt": 0.0, "sell_amt": 0.0, "net_amt": 0.0}
    for detail, side in [(buy_data, "buy"), (sell_data, "sell")]:
        for row in detail:
            if str(row.get("OPERATEDEPT_CODE", "")) == "0":
                amt = (row.get("BUY") if side == "buy" else row.get("SELL")) or 0
                institution[f"{side}_amt"] += amt
    institution["buy_amt"] = round(institution["buy_amt"] / 10000, 1)
    institution["sell_amt"] = round(institution["sell_amt"] / 10000, 1)
    institution["net_amt"] = round(institution["buy_amt"] - institution["sell_amt"], 1)

    return {"records": records, "seats": seats, "institution": institution}


# ─────────────────────────────────────────────────────────────
# 4. 一体化归因
# ─────────────────────────────────────────────────────────────
def attribute_stock(code, name="", trade_date=None, news_days=7,
                    ann_days=15, need_dragon_tiger=True):
    """一站式催化归因。返回 dict, 各字段都是列表/结构, 无数据即空。

    Args:
        code:  sh601606 / 601606 / 601606.SH 都行
        name:  股票简称 (仅用于打印)
        trade_date: 'YYYY-MM-DD' 或 'YYYYMMDD'。默认今日。
        news_days: 新闻只保留最近 N 天 (过滤过期噪声)
        ann_days:  公告只保留最近 N 天
        need_dragon_tiger: 是否拉龙虎榜 (需要 trade_date 有效)
    """
    if trade_date and len(trade_date) == 8 and trade_date.isdigit():
        trade_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
    if not trade_date:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    result = {"code": _strip_code(code), "name": name, "trade_date": trade_date,
              "news": [], "announcements": [], "dragon_tiger": None,
              "data_sources": []}

    # 1. 新闻
    news = fetch_news(code, page_size=20)
    cutoff_news = (datetime.strptime(trade_date, "%Y-%m-%d") -
                   timedelta(days=news_days)).strftime("%Y-%m-%d")
    result["news"] = [n for n in news if n["time"] and n["time"][:10] >= cutoff_news]
    result["data_sources"].append(("东财新闻", f"共 {len(news)} 条, 近 {news_days} 天 {len(result['news'])} 条"))

    # 2. 公告
    anns = fetch_announcements(code, page_size=30)
    cutoff_ann = (datetime.strptime(trade_date, "%Y-%m-%d") -
                  timedelta(days=ann_days)).strftime("%Y-%m-%d")
    result["announcements"] = [a for a in anns if a["date"] and a["date"] >= cutoff_ann]
    result["data_sources"].append(("巨潮公告", f"共 {len(anns)} 条, 近 {ann_days} 天 {len(result['announcements'])} 条"))

    # 3. 龙虎榜
    if need_dragon_tiger:
        dt = fetch_dragon_tiger(code, trade_date, look_back=30)
        result["dragon_tiger"] = dt
        result["data_sources"].append(("东财龙虎榜", f"近 30 天上榜 {len(dt['records'])} 次"))

    return result


def format_report(info):
    """把 attribute_stock 的结构格式化成人眼可读的复盘报告文本。"""
    lines = []
    tag = f"{info['name']}({info['code']})" if info.get("name") else info["code"]
    lines.append(f"# {tag} 催化归因  |  基准日 {info['trade_date']}")
    lines.append("")
    lines.append("## 数据来源")
    for src, note in info["data_sources"]:
        lines.append(f"- {src}: {note}")
    lines.append("")

    # 龙虎榜
    dt = info.get("dragon_tiger")
    if dt and dt["records"]:
        lines.append("## 龙虎榜")
        for r in dt["records"][:8]:
            lines.append(f"- {r['date']}  {r['reason']}  净买 {r['net_buy']}万  换手 {r['turnover']}%")
        if dt["seats"]["buy"]:
            lines.append("")
            lines.append(f"### 最近一次买入席位 TOP5 ({dt['records'][0]['date']})")
            for s in dt["seats"]["buy"]:
                lines.append(f"- {s['name']}: 买{s['buy_amt']}万 卖{s['sell_amt']}万 净{s['net']}万")
        if dt["seats"]["sell"]:
            lines.append("")
            lines.append("### 卖出席位 TOP5")
            for s in dt["seats"]["sell"]:
                lines.append(f"- {s['name']}: 买{s['buy_amt']}万 卖{s['sell_amt']}万 净{s['net']}万")
        inst = dt["institution"]
        if inst["net_amt"]:
            lines.append("")
            lines.append(f"### 机构席位合计: 买 {inst['buy_amt']}万  卖 {inst['sell_amt']}万  "
                         f"净 {inst['net_amt']}万")
        lines.append("")

    # 公告
    if info["announcements"]:
        lines.append("## 近期公告")
        for a in info["announcements"][:15]:
            lines.append(f"- {a['date']}  [{a['type']}]  {a['title']}")
            lines.append(f"    {a['url']}")
        lines.append("")

    # 新闻
    if info["news"]:
        lines.append("## 近期新闻")
        for n in info["news"][:15]:
            lines.append(f"- {n['time'][:10]}  [{n['source']}]  {n['title']}")
            if n["url"]:
                lines.append(f"    {n['url']}")
        lines.append("")

    if not (info["news"] or info["announcements"] or (dt and dt["records"])):
        lines.append("## 无数据")
        lines.append("三路数据源(东财新闻/巨潮公告/东财龙虎榜)本次均返回空。")
        lines.append("可能原因: 1) 该股确实没近期催化; 2) 东财对本机 IP 限流; 3) 网络问题。")
        lines.append("请隔几分钟或换网络重试。")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# 5. 看板/决策看板集成入口
# ─────────────────────────────────────────────────────────────
# 优先展示的公告类别 (真硬催化, 排在前面); 其它类别只作为退路
_ANN_PRIORITY = [
    '重大合同', '收购', '重组', '资产', '中标', '订单',
    '业绩预告', '业绩快报', '业绩', '分红', '增持', '回购',
    '解禁', '减持', '定增', '发行',
]


def _pick_top_catalyst(info):
    """从 attribute_stock 结果里挑一条最"重"的一句话催化, 供看板逐股展示.
    优先级: 龙虎榜净买大 (>2000万) > 硬类型公告 (重大合同/收购/业绩) > 最新新闻 > 空.
    返回 dict{tag, text, url} 或 None.
    """
    if not info:
        return None
    dt = info.get('dragon_tiger') or {}
    records = dt.get('records') or []
    # 1) 近期龙虎榜净买 > 2000 万 → 用第一条 (最近一次)
    for r in records[:3]:
        try:
            if abs(float(r.get('net_buy') or 0)) > 2000:
                sign = '净买' if float(r.get('net_buy')) > 0 else '净卖'
                return {'tag': '龙虎榜',
                        'text': f"{r.get('date', '')} {sign} {r.get('net_buy')}万 · {r.get('reason', '')}",
                        'url': ''}
        except (TypeError, ValueError):
            continue

    # 2) 优先类型公告 (硬催化)
    for kw in _ANN_PRIORITY:
        for a in info.get('announcements', [])[:15]:
            title = a.get('title', '') or ''
            typ = a.get('type', '') or ''
            if kw in title or kw in typ:
                return {'tag': f'公告 · {typ or kw}',
                        'text': f"{a.get('date', '')} {title}",
                        'url': a.get('url', '')}

    # 3) 任意最新公告
    anns = info.get('announcements') or []
    if anns:
        a = anns[0]
        return {'tag': f"公告 · {a.get('type', '公告')}",
                'text': f"{a.get('date', '')} {a.get('title', '')}",
                'url': a.get('url', '')}

    # 4) 任意最新新闻
    news = info.get('news') or []
    if news:
        n = news[0]
        return {'tag': f"新闻 · {n.get('source', '')}",
                'text': f"{n.get('time', '')[:10]} {n.get('title', '')}",
                'url': n.get('url', '')}

    return None


def attribute_focus_pool(focus_df, trade_date=None, verbose=True):
    """为 focus_pool DataFrame 批量拉真实催化, 返回 {股票名: {catalyst, raw}} dict.

    focus_df 需含 '股票' 和 '代码' 列 ('代码' 为 sh/sz 前缀格式)。
    catalyst 字段形如 {tag: '龙虎榜', text: '2026-07-25 净买 5200万 · 机构专用', url: ''}, 无则 None.
    raw 保留 attribute_stock 完整结构, 供上层进一步展开明细.

    单只 3-4 秒 (三路串行 + 1s 节流), 6 只 focus_pool ~= 20-24 秒. 静默失败,
    任何单只出错不影响其他; 只在跑批末尾调用, 别加进盘中流程.
    """
    out = {}
    if focus_df is None or getattr(focus_df, 'empty', True):
        return out
    if '代码' not in focus_df.columns:
        if verbose:
            print("  [催化归因] focus_pool 缺 '代码' 列, 跳过 (需要 build_echelon_table 版本 ≥ v2)")
        return out
    for _, row in focus_df.iterrows():
        name = str(row.get('股票', '')).strip()
        code = str(row.get('代码', '')).strip()
        if not name or not code:
            continue
        try:
            info = attribute_stock(code, name, trade_date=trade_date)
            catalyst = _pick_top_catalyst(info)
            out[name] = {'catalyst': catalyst, 'raw': info}
            if verbose:
                tag = catalyst['tag'] if catalyst else '无催化'
                print(f"    · {name}({code}): {tag}")
        except Exception as e:
            if verbose:
                print(f"    · {name}({code}): 归因失败 {e}")
            out[name] = {'catalyst': None, 'raw': None}
    return out


# ─────────────────────────────────────────────────────────────
# CLI: python -m catalyst_attribution <code> [name] [date]
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python catalyst_attribution.py <code> [name] [YYYY-MM-DD]")
        print("示例: python catalyst_attribution.py sh601606 长城军工 2026-07-27")
        sys.exit(1)
    code = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) >= 3 else ""
    date = sys.argv[3] if len(sys.argv) >= 4 else None
    info = attribute_stock(code, name, trade_date=date)
    print(format_report(info))
