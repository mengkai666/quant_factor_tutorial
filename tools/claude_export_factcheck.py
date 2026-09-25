"""导给 Claude 的对话 / 知识库，出门前先过一遍事实核验。

「查什么」由人工列出：对话里说过、且能被缓存证伪的具体断言（_claims），以及 9/24 预案
逐条对账（PLAN_0924）。「真相是什么」一律在导出时从仓库数据现算：
  - 涨停历史缓存：连板数 / 跌停名单（CI 每日维护，git 可追溯）
  - 价格切片：收盘价，相邻两天算涨跌
  - limit_events 快照：首封时间 / 炸板次数 / 换手（只在本地，缺了只少一句细节）
  - 对话里智能体自己抓到的行情回显：9/24 开盘 / 最低 / 涨跌家数（缓存里没有这些字段）
缓存没有的一律写「无法核验」，不补猜。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from paths import LIMIT_EVENT_SNAPSHOT_DIR, PRICE_SLICE_DIR, ZT_CACHE_FILE  # noqa: E402

WRONG, MISSED, UNVERIFIABLE, OK = "❌ 与缓存不符", "⚠️ 遗漏", "❔ 无法核验", "✅ 属实"

_QUOTE_RE = re.compile(
    r"(?P<name>[^\s()（）:：=,，]+) \((?P<code>\d{6})\): 现价=(?P<price>[\d.]+), 涨跌幅=(?P<pct>-?[\d.]+)%, "
    r"今开=(?P<open>[\d.]+), 最高=(?P<high>[\d.]+), 最低=(?P<low>[\d.]+), 成交额=[\d.]+万, 日期=(?P<date>\d{8})")
_BREADTH_RE = re.compile(r"\[全市场涨跌统计\]: 上涨 (\d+) 家 vs 下跌 (\d+) 家")


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:+.2f}%"


class Evidence:
    """仓库缓存 + 对话行情回显的只读视图；每个查询在缓存不覆盖该日时返回 None。"""

    def __init__(self, transcript_path: Path | None = None):
        raw = Path(ZT_CACHE_FILE).read_text(encoding="utf-8-sig")
        if any(line.startswith(("<<<<<<<", ">>>>>>>", "=======")) for line in raw.splitlines()):
            raise RuntimeError(f"{ZT_CACHE_FILE} 含 git 冲突标记，先修缓存再导出")
        zt = pd.read_csv(ZT_CACHE_FILE, dtype=str, encoding="utf-8-sig")
        zt["h"] = pd.to_numeric(zt["连板数"], errors="coerce")
        self._zt = zt
        self._zt_days = set(zt["日期"])
        self._slice_days = sorted(p.name[:10] for p in Path(PRICE_SLICE_DIR).glob("*.csv.gz"))
        self._slices: dict[str, pd.Series] = {}
        self.quotes: dict[str, dict[str, dict]] = {}
        self.breadth: dict[str, tuple[int, int]] = {}
        if transcript_path and Path(transcript_path).exists():
            self._scan_transcript(Path(transcript_path))

    def _scan_transcript(self, path: Path) -> None:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                content = rec.get("content")
                if not isinstance(content, str):
                    continue
                for m in _QUOTE_RE.finditer(content):
                    day = f"{m['date'][:4]}-{m['date'][4:6]}-{m['date'][6:]}"
                    self.quotes.setdefault(day, {})[m["name"]] = {
                        "code": m["code"], **{k: float(m[k]) for k in ("price", "pct", "open", "high", "low")}}
                b = _BREADTH_RE.search(content)
                if b and rec.get("created_at"):
                    self.breadth.setdefault(str(rec["created_at"])[:10], (int(b[1]), int(b[2])))

    def _rows(self, day: str):
        ymd = day.replace("-", "")
        return None if ymd not in self._zt_days else self._zt[self._zt["日期"] == ymd]

    def height(self, day: str, name: str) -> int | None:
        rows = self._rows(day)
        if rows is None:
            return None
        hit = rows[(rows["类型"] == "ZT") & (rows["名称"] == name)]
        return int(hit["h"].iloc[0]) if len(hit) and pd.notna(hit["h"].iloc[0]) else 0

    def is_dt(self, day: str, name: str) -> bool | None:
        rows = self._rows(day)
        return None if rows is None else bool(((rows["类型"] == "DT") & (rows["名称"] == name)).any())

    def counts(self, day: str) -> tuple[int, int] | None:
        rows = self._rows(day)
        return None if rows is None else (int((rows["类型"] == "ZT").sum()), int((rows["类型"] == "DT").sum()))

    def top(self, day: str) -> tuple[int, list[str]] | None:
        rows = self._rows(day)
        if rows is None:
            return None
        zt = rows[rows["类型"] == "ZT"]
        h = int(zt["h"].max())
        return h, zt[zt["h"] == h]["名称"].tolist()

    def event(self, day: str, name: str) -> dict | None:
        path = Path(LIMIT_EVENT_SNAPSHOT_DIR) / f"{day}.json"
        if not path.exists():
            return None
        stack = [json.loads(path.read_text(encoding="utf-8"))]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if node.get("name") == name and "break_count" in node:
                    return node
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
        return None

    def close_pct(self, day: str, code: str) -> tuple[float, float] | None:
        """code 形如 sz001216；返回 (收盘, 相对上一个切片日的涨跌%)。"""
        if day not in self._slice_days or self._slice_days.index(day) == 0:
            return None
        prev = self._slice_days[self._slice_days.index(day) - 1]
        cur, old = self._slice(day).get(code), self._slice(prev).get(code)
        if cur is None or old is None or pd.isna(cur) or pd.isna(old):
            return None
        return float(cur), (float(cur) / float(old) - 1) * 100

    def _slice(self, day: str) -> pd.Series:
        if day not in self._slices:
            df = pd.read_csv(Path(PRICE_SLICE_DIR) / f"{day}.csv.gz", dtype={"code": str})
            self._slices[day] = df.set_index("code")["close_raw"]
        return self._slices[day]


# ── 9/24 预案逐条对账（第 8 轮「优先级作战手册」原文） ─────────────────────────
# kind: gap=高开阈值才开仓 / stop=分时止损 / dip=低吸 / watch=仅观察 / down=黑名单（预判下跌）
PLAN_0924 = [
    ("P0", "澳弘电子", "gap", "竞价高开 >2% 且冲 3 板才上", 2.0),
    ("P0", "澳弘转债", "stop", "沿分时均线 T+0，破均线止损、不过夜", None),
    ("P1", "中天精装", "gap", "高开 >3% 且 5 分钟放量封板才上", 3.0),
    ("P1", "中际旭创", "dip", "回踩 5 日线企稳低吸", None),
    ("P1", "新易盛", "dip", "回踩 5 日线企稳低吸", None),
    ("P2", "皓元医药", "dip", "CRO 避险：放量走强时低吸", None),
    ("P2", "皓元转债", "dip", "水下企稳后低吸", None),
    ("P3", "华瓷股份", "watch", "仅作情绪观察", None),
    ("P-Black", "闽东电力", "down", "禁止开仓；预判跌停板继续封死", None),
    ("P-Black", "会稽山", "down", "禁止开仓；预判大幅低开多杀多", None),
    ("P-Black", "世联行", "down", "禁止开仓；预判大幅低开多杀多", None),
    ("P-Black", "五洲医疗", "down", "禁止开仓；谨防惯性下杀", None),
    ("P-Black", "亿田转债", "down", "禁止开仓；谨防补跌与溢价率双杀", None),
]


def score_plan_0924(ev: Evidence) -> list[dict]:
    day, rows = "2026-09-24", []
    quotes = ev.quotes.get(day, {})
    for level, name, kind, rule, thr in PLAN_0924:
        q = quotes.get(name)
        if not q:
            rows.append({"level": level, "name": name, "rule": rule, "fact": "—", "verdict": UNVERIFIABLE, "pct": None})
            continue
        prev = q["price"] / (1 + q["pct"] / 100)
        gap, low_dd, pct = (q["open"] / prev - 1) * 100, (q["low"] / prev - 1) * 100, q["pct"]
        if kind == "gap":
            if gap < thr:
                fact = f"开盘 {_pct(gap)} 未达阈值 → 不开仓；收 {_pct(pct)}"
                verdict = "✅ 过滤避损" if pct < 0 else "⚪ 过滤后踏空"
            else:
                fact = f"开盘 {_pct(gap)} 触发；收 {_pct(pct)}"
                verdict = "✅ 触发获利" if pct > gap else "❌ 触发后回落"
        elif kind == "stop":
            fact, verdict = f"盘中最低 {_pct(low_dd)}，收 {_pct(pct)}", "⚠️ 全看止损执行"
        elif kind == "dip":
            fact = f"收 {_pct(pct)}" + ("，收在全天最低价" if abs(q["price"] - q["low"]) < 1e-9 else "")
            verdict = "✅ 低吸获利" if pct > 0 else ("❌ 任何低吸都浮亏" if abs(q["price"] - q["low"]) < 1e-9 else "❌ 收跌")
        elif kind == "watch":
            fact, verdict = f"收 {_pct(pct)}", "⚪ 观察"
        else:
            fact = f"开盘 {_pct(gap)}，收 {_pct(pct)}"
            verdict = "✅ 预判成立" if pct < 0 else "❌ 预判落空"
        rows.append({"level": level, "name": name, "rule": rule, "fact": fact, "verdict": verdict, "pct": pct})
    return rows


def branches_0924(ev: Evidence) -> list[tuple[str, list[tuple[str, bool | None]]]]:
    day = "2026-09-24"
    up = ev.breadth.get(day, (None, None))[0]
    counts = ev.counts(day)
    aohong = ev.height(day, "澳弘电子")
    mindong_dt = ev.is_dt(day, "闽东电力")
    haoyuan = ev.quotes.get(day, {}).get("皓元医药")
    return [
        ("A 硬件主线加速（原标概率 ~35%）", [
            ("澳弘电子晋级 3 板", None if aohong is None else aohong >= 3),
            ("上涨家数回升至 2800 以上", None if up is None else up >= 2800),
            ("闽东电力跌停打开", None if mindong_dt is None else not mindong_dt)]),
        ("B 高位分歧、流向医药（原标概率 ~50%）", [
            ("闽东电力继续封跌停", mindong_dt),
            ("医药 CRO 承接（皓元医药收红）", None if not haoyuan else haoyuan["pct"] > 0)]),
        ("C 主板补跌（原标概率 ~15%）", [
            ("跌停家数超过 30", None if counts is None else counts[1] > 30)]),
    ]


# ── 对话里说过、能被缓存证伪的断言 ────────────────────────────────────────────
def _claims(ev: Evidence, plan_text: str, scorecard: list[dict]) -> list[dict]:
    out: list[dict] = []

    def add(said, where, status, truth, pattern):
        out.append({"said": said, "where": where, "status": status, "truth": truth, "pattern": pattern})

    h = ev.height("2026-09-22", "华瓷股份")
    e = ev.event("2026-09-22", "华瓷股份")
    # 只认「华瓷股份(剧烈|巨量)炸板」「昨日剧烈炸板」「提示 5 板」这几种说法；
    # 「澳弘电子放量炸板剧震，华瓷股份冲高无力」是在说澳弘，不能算
    huaci = r"华瓷股份(剧烈|巨量)?炸板|华瓷股份[^。]{0,25}(昨日剧烈炸板|5 板)"
    if h is None:
        add("华瓷股份 9/22「剧烈炸板放量」、是「5 板」空间板", "", UNVERIFIABLE, "缓存缺 9/22", huaci)
    else:
        t = str(e.get("first_limit_time") or "") if e else ""
        detail = f"，首封 {t[:2]}:{t[2:4]}、炸板 {e['break_count']} 次、换手 {e['turnover_rate']:.2f}%" if e and len(t) >= 4 else ""
        add("华瓷股份 9/22「剧烈炸板放量」、是「5 板」空间板", "战法库与默认面板（已更正）",
            WRONG if h != 5 or (e and e.get("break_count") == 0) else OK,
            f"9/22 涨停池连板数 {h}{detail}；9/23 是 6 连板一字后首次开板",
            huaci)

    h = ev.height("2026-09-22", "博通集成")
    cp = ev.close_pct("2026-09-23", "sh603068")
    add("博通集成 9/23 是「2 进 3」卡位", "战法三的唯一案例",
        UNVERIFIABLE if h is None else (WRONG if h != 2 else OK),
        f"9/22 已是 {h} 板，9/23 实为 {h} 进 {(h or 0) + 1}（收 {_pct(cp and cp[1])}）",
        r"博通集成[^。]{0,25}2\s*进\s*3|2\s*进\s*3[^。]{0,15}博通集成")

    h = ev.height("2026-09-23", "中天精装")
    add("中天精装 9/23「主板放量实体 2 连板」", "",
        UNVERIFIABLE if h is None else (WRONG if h != 2 else OK), f"9/23 连板数 {h}（首板）",
        r"中天精装[^。]{0,70}实体\s*2\s*连板")

    dts = [ev.is_dt("2026-09-23", n) for n in ("会稽山", "世联行")]
    cps = [ev.close_pct("2026-09-23", c) for c in ("sh601579", "sz002285")]
    add("会稽山、世联行 9/23「天地板」（用户原话「先涨停再跌停」）", "战法六原文",
        UNVERIFIABLE if None in dts else (WRONG if not any(dts) else OK),
        f"两只均未跌停，收 {_pct(cps[0] and cps[0][1])}、{_pct(cps[1] and cps[1][1])}；是触板回落的长上影",
        r"天地板")

    h21 = [ev.height("2026-09-21", n) for n in ("世联行", "会稽山")]
    h22 = [ev.height("2026-09-22", n) for n in ("世联行", "会稽山")]
    was_leader = max(h or 0 for h in h21) >= 2 and not any(h22)
    add("世联行、会稽山是「缺乏板块集群的老周期跟风票」", "战法六原文",
        UNVERIFIABLE if None in h21 + h22 else (WRONG if was_leader else OK),
        f"9/21 世联行 {h21[0]} 板、会稽山 {h21[1]} 板，9/22 双双断板；9/23 是断板次日冲板反包失败",
        r"老周期[^。]{0,40}(会稽山|世联行)|(会稽山|世联行)[^。]{0,70}老周期")

    c = ev.counts("2026-09-23")
    add("9/23「83 只涨停，26 只跌停或跌超 9%」", "",
        UNVERIFIABLE if c is None else "⚠️ 口径不符",
        f"涨停池 {c[0]} 只、跌停 {c[1]} 只；对话未说明 83 的口径" if c else "缓存缺 9/23", r"83\s*只涨停")

    c23, c24 = ev.counts("2026-09-23"), ev.counts("2026-09-24")
    add("9/24「跌停从昨日的极少数扩大至 13 家」", "",
        UNVERIFIABLE if not (c23 and c24) else (WRONG if c23[1] >= 10 else OK),
        f"9/23 跌停 {c23 and c23[1]} 家、9/24 跌停 {c24 and c24[1]} 家，并未扩大", r"跌停个股从昨日的极少数")

    t23, t24 = ev.top("2026-09-23"), ev.top("2026-09-24")
    if t23:
        named = [n for n in t23[1] if n in plan_text]
        add("9/24 预案以华瓷股份为高标风向标", "",
            MISSED if plan_text and not named else OK,
            f"9/23 最高板是 {'、'.join(t23[1])}（{t23[0]} 板），预案一字未提"
            + (f"；9/24 最高板 {'、'.join(t24[1])}（{t24[0]} 板）" if t24 else ""),
            r"P0 \(进攻先锋\)")

    black = [r for r in scorecard if r["level"] == "P-Black" and r["pct"] is not None]
    if black:
        ups = [r for r in black if r["pct"] > 0]
        add("9/24 复盘称「P-Black 负反馈与跌停风险全面扩散」（举超声电子、一鸣食品、莱宝高科）", "",
            WRONG if len(ups) == len(black) else OK,
            f"黑名单 {len(black)} 只中 {len(ups)} 只收红（" + "、".join(f"{r['name']} {_pct(r['pct'])}" for r in black)
            + "）；所举 3 只跌停股都不在黑名单上",
            r"负反馈与跌停风险全面扩散")
    return out


PLAN_MARKER = "P0 (进攻先锋)"  # 9/24 预案（第 8 轮「优先级作战手册」）独有的矩阵表头


def build_report(transcript_path: Path | None = None, round_texts: list[str] | None = None) -> dict:
    """round_texts 是精简版各轮助手回复；出处轮次按正则实际命中现算，不手写轮号。"""
    texts = round_texts or []
    ev = Evidence(transcript_path)
    scorecard = score_plan_0924(ev)
    claims = _claims(ev, next((t for t in texts if PLAN_MARKER in t), ""), scorecard)
    for c in claims:
        hits = [str(i) for i, t in enumerate(texts, 1) if re.search(c["pattern"], t)]
        c["where"] = "；".join(filter(None, [f"精简版第 {'、'.join(hits)} 轮" if hits else "", c["where"]])) or "—"
    return {
        "claims": claims,
        "scorecard": scorecard,
        "branches": branches_0924(ev),
        "latest_cache_day": max(ev._zt_days) if ev._zt_days else "",
    }


def annotations_for(text: str, report: dict) -> list[str]:
    """一轮助手回复里命中了哪些已证伪断言，返回对应的批注行。"""
    return [f"{c['status']}：{c['said']} → {c['truth']}" for c in report["claims"]
            if c["status"] != OK and re.search(c["pattern"], text)]


def render_errata_md(report: dict) -> str:
    lines = ["### 1. 事实更正（对话原话 vs 仓库缓存）", "",
             "| 对话中的说法 | 出处 | 核验 | 缓存事实 |", "|---|---|---|---|"]
    for c in report["claims"]:
        lines.append(f"| {c['said']} | {c['where']} | {c['status']} | {c['truth']} |")
    lines += ["", "### 2. 9/24 预案逐条对账（行情取自对话中智能体抓取的 9/24 收盘回显）", "",
              "| 层级 | 标的 | 预案规则 | 9/24 实际 | 判定 |", "|---|---|---|---|---|"]
    for r in report["scorecard"]:
        lines.append(f"| {r['level']} | {r['name']} | {r['rule']} | {r['fact']} | {r['verdict']} |")
    lines += ["", "### 3. 9/24 情景分支触发条件", ""]
    fired_any = False
    for title, conds in report["branches"]:
        marks = "；".join(f"{'✅' if ok else ('❔' if ok is None else '❌')} {name}" for name, ok in conds)
        fired = all(ok for _, ok in conds)
        fired_any = fired_any or fired
        lines.append(f"- **分支 {title}**：{marks} → {'触发' if fired else '未触发'}")
    lines += ["", "**结论**：" + _conclusion(report["scorecard"], fired_any)]
    return "\n".join(lines)


def _conclusion(scorecard: list[dict], fired_any: bool) -> str:
    known = [r for r in scorecard if r["pct"] is not None]
    filtered = [r["name"] for r in known if r["verdict"].startswith(("✅ 过滤", "⚪ 过滤"))]
    longs = [r for r in known if r["level"] in ("P1", "P2") and r["rule"].find("低吸") >= 0]
    black = [r for r in known if r["level"] == "P-Black"]
    parts = []
    if filtered:
        parts.append(f"条件触发式开仓按规则拦下了 {'、'.join(filtered)}（高开阈值未满足）")
    if longs:
        losing = sum(r["pct"] < 0 for r in longs)
        parts.append(f"低吸与避险标的 {len(longs)} 只中 {losing} 只收跌")
    if black:
        up = sum(r["pct"] > 0 for r in black)
        parts.append(f"黑名单 {len(black)} 只中 {up} 只收红，「次日必跌」预判{'全部落空' if up == len(black) else '部分落空'}")
    parts.append("情景分支" + ("有一条触发" if fired_any else "没有一条触发"))
    return "；".join(parts) + "。黑名单应理解为「不开仓规则」，不是涨跌预测。"


# ── 战法证据分级与项目回测（供知识库引用；数字来自项目脚本，引用须带样本） ──────────
TACTIC_EVIDENCE = {
    "情绪周期极值律": ("🟡 部分有回测", "冰点侧成立：上涨占比 <0.20 次日反弹超 10pct 的概率 85%。过热侧（>0.75）是缓慢消化，次日反弹概率 0%，不是「断崖式淘汰」。"),
    "龙头梯队接力律": ("🔴 与回测冲突", "「断板后转攻低位首板」没有优势：龙头断板日的首板最终走到 4 板以上的比例 1.63%，基准 1.72%（0.9 倍）。「绝不幻想反包」也过度：3 板以上断板的反包率 ≥20%。"),
    "双子星卡位生死律": ("⚪ 单一个案", "只有博通集成 9/23 一例，且原记录把 3 进 4 写成了 2 进 3；未回测。"),
    "人气容量龙反包律": ("⚪ 未回测", "9/24 反例：中际旭创、新易盛都收在全天最低价。"),
    "监管异动红线踩踏律": ("⚪ 单一个案", "只有闽东电力 9/23 一例（严重异动公告属实、当日跌停），9/24 即收 +2.66%。「临界点不接力」可以保留，「必在竞价踩踏」没有样本支持。"),
    "退潮期假冲天炮出逃律": ("🟡 以回测为准", "原「老周期跟风、天地板」叙述与事实不符（见事实更正）。可依赖的是断板反包基础率：1 日内约 7.6%，3 日内 15.8%。"),
}

BACKTEST_FACTS = [
    ("情绪水位", "上涨占比 = 上涨/(上涨+下跌)。<0.20 为冰点，次日反弹超 10pct 概率 85%；>0.75 为过热，次日反弹概率 0%，择时模型据此把主升场景降级",
     "src/limit_pattern_study.py（179 交易日）；src/timing_signal.py"),
    ("连板晋级", "2 板次日仍封板率仅 33%，是最危险的位置；3~6 板 45~50%。1 进 2 晋级率 13.6% 是唯一的墙", "src/limit_pattern_study.py；龙头接替研究（209 交易日）"),
    ("断板反包", "2 板以上断板 316 例：1 日内反包成功约 7.6%，3 日内 15.8%；3 板以上断板反包率 ≥20%", "价格重建，2026-06-16~08-07（38 交易日）"),
    ("龙头接替", "前龙头断板日买首板，最终 ≥4 板的转化率只有基准的 0.9 倍，各窗口都无优势：接替是事后确认，不是提前信号", "209 交易日，15 个周期"),
    ("高度", "最高板 ≤4 板时，10 日内出现更高板的比例 100%（76 天）；8 板后前向增量转负；高度突破次日 44% 下降", "209 交易日"),
    ("首板扩张日", "首板数较昨日 ≥1.5 倍、上涨占比 ≥0.6、跌停 ≤20 时，首板池 3 板以上转化率 2.5%→5.3%（仅 4 个命中日，小样本）", "38 交易日"),
    ("星期效应", "周四上涨占比 >0.60 后，周五崩塌率 55.6%（9 例）；周一本身崩塌率 50%", "172 交易日情绪缓存"),
    ("趋势闸门", "收盘价对 MA120 + 20 日斜率 ±1.0%，连续 3 根确认；只在下跌档下调仓位上限（下跌档 T+3 上涨率 35.5%，31 例）", "src/trend_regime.py"),
]
