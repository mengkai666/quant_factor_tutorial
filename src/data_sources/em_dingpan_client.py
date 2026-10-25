"""第三个数据接口：东方财富 (Eastmoney) 盯盘备用源

定位
----
项目原有的两个盯盘数据源是【开盘啦 KaiPanLa】与【财联社 CLS】。
本模块提供 **第三个独立数据源**，当上述两源同时不可用时接管盯盘页面渲染。

上游组合（互为内部兜底，对外表现为「一个备用数据源」）
------------------------------------------------------
1. 东方财富 push2ex 涨跌停池 —— 主上游
     - https://push2ex.eastmoney.com/getTopicZTPool   涨停池 (data.tc = 涨停家数)
     - https://push2ex.eastmoney.com/getTopicDTPool   跌停池 (data.tc = 跌停家数)
     - https://push2ex.eastmoney.com/getTopicZBPool   炸板池 (data.tc = 炸板家数)
   由此可算：涨停/跌停/炸板家数、封板率、连板梯队、涨停池明细。

2. 同花顺 dataapi —— 次上游（补 push2ex 不提供的同比口径）
     - https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool
       提供 limit_up_count.today/yesterday {num 封板, history_num 触及, rate 封板率, open_num 炸板}
     - https://data.10jqka.com.cn/dataapi/limit_up/continuous_limit_up
       提供连板梯队 {height, number, code_list}

字段语义对齐（与 CLS / KaiPanLa 保持同构）
----------------------------------------
    封板率  = 封板家数 / 触及家数        (东财: ZT / (ZT + ZB))
    封板    = 收盘仍封住涨停的家数
    触及    = 盘中曾触及涨停的家数（含炸板）
    连板率  = 该高度家数 / 上一高度家数

已知能力边界（实测 2026-10-17）
-------------------------------
东方财富 push2*.eastmoney.com 的行情列表接口 (clist) 在本机网络环境下不可达，
因此 **涨跌家数分布 / 资金热力图** 无法由本备用源提供。
这两块在降级时会由 dingpan_service 明确标记为 unavailable，前端显示占位提示，
不会用假数据填充。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# 东方财富 push2ex 使用的公开 ut 令牌（行情中心前端同款）
EM_UT = "7eea3edcaed734bea9cbfc24409ed989"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json,text/plain,*/*",
}

# 同花顺涨停池字段（语义化字段名，避免猜测数字字段 ID）
THS_FIELDS = (
    "199112,10,9001,330323,330324,330325,9002,330329,"
    "133971,133970,1968584,3475914,9003,9004"
)


def _retry_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update(DEFAULT_HEADERS)
    return session


def _to_yyyymmdd(date: str) -> str:
    """把 2026-10-17 / 20261017 统一成 20261017"""
    cleaned = str(date).replace("-", "").replace("/", "")
    if len(cleaned) == 8 and cleaned.isdigit():
        return cleaned
    return datetime.now().strftime("%Y%m%d")


class EastmoneyDingpanClient:
    """东方财富 (+同花顺) 盯盘备用源客户端。"""

    EM_BASE = "https://push2ex.eastmoney.com"
    THS_BASE = "https://data.10jqka.com.cn/dataapi/limit_up"

    def __init__(self, session: Optional[requests.Session] = None, timeout: int = 12,
                 now=None):
        self.session = session or _retry_session()
        self.timeout = timeout
        self.now = now or (lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------
    # 东方财富 push2ex
    # ------------------------------------------------------------------
    def _fetch_pool(self, endpoint: str, date: str, sort: str) -> Optional[Dict[str, Any]]:
        try:
            resp = self.session.get(
                f"{self.EM_BASE}/{endpoint}",
                params={
                    "ut": EM_UT,
                    "dpt": "wz.ztzt",
                    "Pageindex": 0,
                    "pagesize": 10000,
                    "sort": sort,
                    "date": _to_yyyymmdd(date),
                },
                headers={**DEFAULT_HEADERS, "Referer": "https://quote.eastmoney.com/"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
            if not isinstance(payload, dict) or payload.get("rc") != 0:
                logger.warning("EM %s business error rc=%s", endpoint, payload.get("rc"))
                return None
            data = payload.get("data")
            if not isinstance(data, dict):
                logger.warning("EM %s returned no data", endpoint)
                return None
            return data
        except Exception as exc:  # noqa: BLE001 - 备用源必须静默降级
            logger.warning("EM %s unavailable: %s", endpoint, exc)
            return None

    def get_limit_pool_counts(self, date: str) -> Dict[str, Any]:
        """涨跌停 + 炸板家数（盯盘「市场概况 / 涨停表现」核心口径）"""
        zt = self._fetch_pool("getTopicZTPool", date, "fbt:asc")
        dt = self._fetch_pool("getTopicDTPool", date, "fund:asc")
        zb = self._fetch_pool("getTopicZBPool", date, "fbt:asc")

        zt_count = int(zt.get("tc", 0)) if zt else None
        dt_count = int(dt.get("tc", 0)) if dt else None
        zb_count = int(zb.get("tc", 0)) if zb else None

        touched = None
        seal_rate = None
        if zt_count is not None and zb_count is not None:
            touched = zt_count + zb_count
            if touched > 0:
                seal_rate = round(zt_count / touched * 100, 2)

        return {
            "limit_up_count": zt_count,
            "limit_down_count": dt_count,
            "broken_count": zb_count,
            "touched_count": touched,
            "seal_rate": seal_rate,
            "date": zt.get("qdate") if zt else None,
        }

    def get_ladder(self, date: str) -> Dict[str, Any]:
        """连板梯队（由涨停池 lbc 字段聚合）

        连板率口径与财联社一致：`该高度今日家数 / 上一高度昨日家数`。
        因此需要回退到上一个交易日取基数；取不到时该档显示 '—'，
        绝不用「相邻高度比」之类的近似值冒充真实连板率。

        Returns:
            tiers   ['一板','二板','三板','高度板']
            counts  ['54','4','1','2']
            rates   ['连板率','13%','50%','50%']
        """
        counts_by_height, _ = self._ladder_counts(date)
        if counts_by_height is None:
            return {}

        # 关键：先把「请求日」校正为「实际交易日」再回溯。
        # 若直接拿请求日回溯，遇到非交易日（如周六请求落到周五）会回溯到
        # 同一个交易日，基数=自身，连板率退化成自比值（7%/25%/100% 这种假数）。
        anchor = self._resolve_trade_date(date)
        prev_date = self._previous_trade_date(anchor) if anchor else None
        prev_counts, _ = self._ladder_counts(prev_date) if prev_date else (None, None)

        first = counts_by_height.get(1, 0)
        second = counts_by_height.get(2, 0)
        third = counts_by_height.get(3, 0)
        high = sum(v for k, v in counts_by_height.items() if k >= 4)

        def _ratio(num: int, base: int) -> str:
            """连板率 = 今日该档家数 / 昨日低一档家数；基数缺失时如实给 '—'"""
            if not prev_counts or base <= 0:
                return "—"
            return f"{round(num / base * 100)}%"

        # 「高度板」是聚合档（>=4 板），分子分母都必须聚合：若分子取 k==4 的
        # 精确家数，就会出现「高度板显示 2 家、连板率却按 1 家算」的自相矛盾。
        prev_third_and_up = sum(v for k, v in (prev_counts or {}).items() if k >= 3)

        return {
            "tiers": ["一板", "二板", "三板", "高度板"],
            "counts": [str(first), str(second), str(third), str(high)],
            "rates": [
                "连板率",
                _ratio(second, (prev_counts or {}).get(1, 0)),
                _ratio(third, (prev_counts or {}).get(2, 0)),
                _ratio(high, prev_third_and_up),
            ],
        }

    def _resolve_trade_date(self, date: str) -> Optional[str]:
        """把请求日校正为接口实际返回的交易日。

        东财 push2ex 的 qdate 恒为「全市场最近交易日」，不是请求日对应的
        交易日，所以不能直接拿 qdate 当答案。实测行为分两种：
          · 请求日 > qdate（周末/节假日且晚于最近交易日）→ 静默回落，
            返回的就是 qdate 当天数据；
          · 请求日 <= qdate → 返回请求日自身数据；若请求日不是交易日
            且已是过去时，则返回空池（tc=0）。
        因此「请求日 > qdate」正好等价于「本次响应是回落来的」。
        所有需要拿另一个交易日做基数的口径（连板率等）都必须先过这一步，
        否则基数会取到自身，连板率退化成自比值。
        """
        if not date:
            return None
        zt = self._fetch_pool("getTopicZTPool", date, "fbt:asc")
        qdate = (zt or {}).get("qdate")
        if not qdate:
            return date
        s = str(qdate)
        if not (len(s) == 8 and s.isdigit()):
            return date
        latest = f"{s[:4]}-{s[4:6]}-{s[6:]}"
        return latest if _to_yyyymmdd(date) > _to_yyyymmdd(latest) else date

    def _ladder_counts(self, date: str):
        """取某日涨停池按连板高度分桶；返回 (counts_dict, raw_tc) 或 (None, None)"""
        if not date:
            return None, None
        zt = self._fetch_pool("getTopicZTPool", date, "fbt:asc")
        if not zt or not (zt.get("pool") or []):
            return None, None
        counts: Dict[int, int] = {}
        for item in zt["pool"]:
            try:
                lbc = int(item.get("lbc", 1) or 1)
            except (TypeError, ValueError):
                lbc = 1
            counts[lbc] = counts.get(lbc, 0) + 1
        return counts, zt.get("tc")

    def _previous_trade_date(self, date: str, max_back: int = 7) -> Optional[str]:
        """向前回溯至最近一个有涨停池数据的交易日，返回 YYYY-MM-DD"""
        base = _to_yyyymmdd(date)
        try:
            cursor = datetime.strptime(base, "%Y%m%d")
        except ValueError:
            return None
        for _ in range(max_back):
            cursor = cursor.fromordinal(cursor.toordinal() - 1)
            candidate = cursor.strftime("%Y-%m-%d")
            zt = self._fetch_pool("getTopicZTPool", candidate, "fbt:asc")
            if zt and (zt.get("pool") or []):
                return candidate
        return None

    def get_zt_pool(self, date: str) -> List[Dict[str, Any]]:
        """涨停池明细（代码/名称/连板/封单额/首封时间/换手/流通市值/行业）"""
        zt = self._fetch_pool("getTopicZTPool", date, "fbt:asc")
        if not zt:
            return []
        rows = []
        for item in zt.get("pool") or []:
            code = str(item.get("c", "")).zfill(6)
            fbt = item.get("fbt")
            rows.append({
                "code": code,
                "name": item.get("n", ""),
                "limit_count": item.get("lbc", 1),
                "change_pct": item.get("zdp"),
                "seal_fund": item.get("fund"),
                "first_limit_time": self._hhmmss(fbt),
                "break_count": item.get("zbc", 0),
                "turnover_rate": item.get("hs"),
                "float_market_cap": item.get("ltsz"),
                "plate_name": item.get("hybk", ""),
                "limit_stat": item.get("zttj") or {},
            })
        rows.sort(key=lambda r: -(r["limit_count"] or 1))
        return rows

    @staticmethod
    def _hhmmss(value) -> str:
        """东财首封时间是 HHMMSS 整数(如 92500 表示 09:25:00)"""
        if value in (None, "", 0):
            return ""
        try:
            digits = str(int(value)).zfill(6)
            return f"{digits[:2]}:{digits[2:4]}:{digits[4:]}"
        except (TypeError, ValueError):
            return ""

    # ------------------------------------------------------------------
    # 同花顺 dataapi（补同比口径）
    # ------------------------------------------------------------------
    def _ths_get(self, path: str, date: str, **extra) -> Optional[Dict[str, Any]]:
        try:
            params = {
                "page": 1,
                "limit": 200,
                "field": THS_FIELDS,
                "filter": "HS,GEM2STAR",
                "order_field": "330324",
                "order_type": "0",
                "date": _to_yyyymmdd(date),
            }
            params.update(extra)
            resp = self.session.get(f"{self.THS_BASE}/{path}", params=params,
                                    timeout=self.timeout)
            resp.raise_for_status()
            payload = resp.json()
            if not isinstance(payload, dict) or payload.get("status_code") != 0:
                logger.warning("THS %s business error status_code=%s", path,
                               payload.get("status_code") if isinstance(payload, dict) else "?")
                return None
            return payload.get("data") or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("THS %s unavailable: %s", path, exc)
            return None

    def get_limit_up_summary(self, date: str) -> Dict[str, Any]:
        """同花顺封板率同比口径

        Returns:
            today / yesterday 各含 sealed(封板) touched(触及) broken(炸板) seal_rate(封板率)
            effective         实际可用的一组口径（非交易日 today 全 0 时自动落到最近交易日）
        """
        data = self._ths_get("limit_up_pool", date)
        if not data:
            return {}

        def _norm(block: Dict[str, Any]) -> Dict[str, Any]:
            rate = block.get("rate")
            return {
                "sealed": block.get("num"),
                "touched": block.get("history_num"),
                "broken": block.get("open_num"),
                "seal_rate": f"{round(float(rate) * 100, 2)}%" if rate is not None else "",
            }

        today = _norm(data.get("limit_up_count", {}).get("today", {}) or {})
        yesterday = _norm(data.get("limit_up_count", {}).get("yesterday", {}) or {})

        # 非交易日 / 盘前：同花顺 today 全为 0，此时以最近有数据的交易日为准
        effective = today if (today.get("sealed") or 0) > 0 else yesterday
        effective_label = "today" if effective is today else "yesterday"

        return {
            "today": today,
            "yesterday": yesterday,
            "effective": effective,
            "effective_label": effective_label,
            "limit_down_today": _norm(data.get("limit_down_count", {}).get("today", {}) or {}),
            "trade_status": (data.get("trade_status") or {}).get("name", ""),
        }

    def get_continuous_ladder(self, date: str) -> Dict[str, Any]:
        """同花顺连板梯队（push2ex 不可用时的梯队兜底）

        连板率口径必须与财联社 / get_ladder 一致：分母是「昨日低一档家数」，
        不能拿今日低一档凑数（那会算出 7%/25%/100% 这类自比值）。
        """
        # 同花顺对非交易日直接返回空（不像 push2ex 会回落到最近交易日），
        # 所以必须先校正请求日，否则周末调用会拿到空梯队。
        anchor = self._resolve_trade_date(date)
        by_height = self._ths_ladder_by_height(anchor) if anchor else {}
        if not by_height:
            return {}

        prev_date = self._previous_trade_date(anchor) if anchor else None
        prev_by_height = self._ths_ladder_by_height(prev_date) if prev_date else {}

        first = by_height.get(1, 0)
        second = by_height.get(2, 0)
        third = by_height.get(3, 0)
        high = sum(v for k, v in by_height.items() if k >= 4)

        def _rate(numerator: int, denominator: int) -> str:
            if denominator <= 0:
                return "—"
            return f"{round(numerator / denominator * 100)}%"

        prev_third_and_up = sum(v for k, v in prev_by_height.items() if k >= 3)

        # 同花顺 continuous_limit_up 只收录连板股（2 板起），本身不含首板。
        # 此时把一板写成 0 会读成「今日没有首板」，属误导，故如实显示 '—'。
        first_display = str(first) if 1 in by_height else "—"

        return {
            "tiers": ["一板", "二板", "三板", "高度板"],
            "counts": [first_display, str(second), str(third), str(high)],
            "rates": [
                "连板率",
                _rate(second, prev_by_height.get(1, 0)),
                _rate(third, prev_by_height.get(2, 0)),
                _rate(high, prev_third_and_up),
            ],
        }

    def _ths_ladder_by_height(self, date: str) -> Dict[int, int]:
        """同花顺连板梯队 → {连板高度: 家数}"""
        if not date:
            return {}
        data = self._ths_get("continuous_limit_up", date, page=1, limit=50)
        if not data:
            return {}
        if isinstance(data, list):
            groups = data
        else:
            groups = data.get("info") or data.get("list") or []
        by_height: Dict[int, int] = {}
        for g in groups:
            try:
                height = int(g.get("height", 1))
            except (TypeError, ValueError):
                continue
            number = g.get("number")
            if number is None:
                number = len(g.get("code_list") or [])
            by_height[height] = int(number)
        return by_height

    # ------------------------------------------------------------------
    # 组合入口
    # ------------------------------------------------------------------
    def get_dingpan_fallback(self, date: str) -> Dict[str, Any]:
        """产出与 CLS 同构的盯盘数据块（尽力而为，缺失块明确标注）"""
        counts = self.get_limit_pool_counts(date)
        summary = self.get_limit_up_summary(date)
        effective = summary.get("effective", {}) or {}

        # 封板率优先用同花顺（含同比），否则退回东财自算
        seal_rate = effective.get("seal_rate") or ""
        sealed = effective.get("sealed")
        touched = effective.get("touched")
        if sealed is None:
            sealed = counts.get("limit_up_count")
        if touched is None:
            touched = counts.get("touched_count")
        if not seal_rate and counts.get("seal_rate") is not None:
            seal_rate = f"{counts['seal_rate']:.2f}%"

        ladder = self.get_ladder(date) or self.get_continuous_ladder(date)

        return {
            "limit_up_count": counts.get("limit_up_count"),
            "limit_down_count": counts.get("limit_down_count"),
            "broken_count": counts.get("broken_count"),
            "seal_rate_str": seal_rate,
            "sealed_count": sealed,
            "touched_count": touched,
            "effective_label": summary.get("effective_label", ""),
            "trade_status": summary.get("trade_status", ""),
            "limit_board": ladder,
            "zt_pool": self.get_zt_pool(date)[:40],
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    client = EastmoneyDingpanClient()
    today = datetime.now().strftime("%Y%m%d")
    print(f"--- 东方财富备用源探测 {today} ---")
    print("counts :", client.get_limit_pool_counts(today))
    print("summary:", client.get_limit_up_summary(today))
    print("ladder :", client.get_ladder(today))
    pool = client.get_zt_pool(today)
    print(f"zt_pool: {len(pool)} 只，首只 = {pool[0] if pool else None}")
