"""盯盘聚合服务 (Dingpan Data Service)

将「财联社 盯盘页」的完整数据结构化，供手机 APP / 移动前端 1:1 渲染。

数据源优先级（三源，逐块独立降级）
----------------------------------
  第一源  财联社 CLS      —— 盯盘页原生口径，字段最全（市场概况/涨停表现/资金热力图）
  第二源  开盘啦 KaiPanLa —— 独立交叉校验源（涨跌停家数/炸板率/连板天梯）
  第三源  东方财富 EM     —— 备用源（push2ex 涨跌停池 + 同花顺封板率同比）

每个数据块都独立走降级链，并在 `sources` 中标注实际生效的数据源；
降级到第三源时 `degraded=True`，无法提供的块显式标记 unavailable，
前端显示占位提示而不用假数据填充。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from data_sources.cls_client import CLSClient
from data_sources.kaipanla_client import KaiPanLaClient
from data_sources.em_dingpan_client import EastmoneyDingpanClient

logger = logging.getLogger(__name__)


def cls_pool_topics():
    """池类 topic（连板/炸板/跌停）—— 从 CLSClient 取，避免两处硬编码不一致。"""
    return getattr(CLSClient, "POOL_TOPICS", {"continuous_up", "up_open", "down"})


# 盯盘页顶部 Tab 栏（与财联社 App 一致，顺序不可乱）
DINGPAN_TABS = [
    {"key": "watchlist", "label": "自选"},
    {"key": "market", "label": "大盘"},
    {"key": "bse", "label": "北交所"},
    {"key": "live", "label": "直播"},
    {"key": "tuyere", "label": "风口"},
    {"key": "mainline", "label": "主线"},
    {"key": "big_rise", "label": "大涨股"},
    {"key": "zt_bao", "label": "涨停宝"},
]

# 资金热力图胶囊按钮
FUND_HEAT_SORT_MODES = ["主力净流入", "主力净流出", "按涨幅", "按跌幅"]
FUND_HEAT_TIME_WINDOWS = ["今日", "5日", "10日"]

SOURCE_CLS = "财联社(8.5.4)"
SOURCE_KPL = "开盘啦(6.1.6)"
SOURCE_EM = "东方财富(备用)"


def _int_or_none(value) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


class DingpanAggregator:
    """盯盘数据聚合器 —— 三源降级。"""

    def __init__(self, cls_client: Optional[CLSClient] = None,
                 kpl_client: Optional[KaiPanLaClient] = None,
                 em_client: Optional[EastmoneyDingpanClient] = None,
                 build_fund_heat_matrix: bool = True):
        self.cls = cls_client or CLSClient()
        self.kpl = kpl_client or KaiPanLaClient()
        self.em = em_client or EastmoneyDingpanClient()
        self.build_fund_heat_matrix = build_fund_heat_matrix
        self._last_ladder_source: str = ""
        self._bse_total: Optional[int] = None
        self._bse_banner: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------
    def build(self, date: Optional[str] = None) -> Dict[str, Any]:
        """构建盯盘全量数据包"""
        date = date or datetime.now().strftime("%Y-%m-%d")
        sources: Dict[str, str] = {}
        unavailable: List[str] = []

        # --- 第一源：财联社 ---
        emotion = self._call(self.cls, "get_market_emotion") or {}
        kpl_stats = self._call(self.kpl, "get_market_stats") or {}

        # 第三源只在需要时构建（较慢），先做懒加载占位
        em_payload: Optional[Dict[str, Any]] = None

        def _em() -> Dict[str, Any]:
            nonlocal em_payload
            if em_payload is None:
                em_payload = self._call(self.em, "get_dingpan_fallback", date) or {}
            return em_payload

        # ---------- 1. 市场概况 ----------
        overview = self._build_overview(emotion, kpl_stats, _em, sources, unavailable)

        # ---------- 2. 市场热度 / 成交额 ----------
        heat = self._build_heat(emotion, sources, unavailable)

        # ---------- 3. 涨停表现 ----------
        limit_perf = self._build_limit_performance(
            emotion, kpl_stats, _em, sources, unavailable
        )

        # ---------- 4. 资金热力图 ----------
        fund_heat = self._build_fund_heat(sources, unavailable)

        # ---------- 4b. 指数报价（大盘页顶部 C1，此前完全缺失）----------
        index_quotes = self._build_index_quotes(sources, unavailable)

        # ---------- 4c. 个股排行（C14）+ 排行主题字典 ----------
        ranking = self._build_ranking(sources, unavailable)

        # ---------- 4d. 板块排行 ----------
        plate_ranking = self._build_plate_ranking(sources, unavailable)

        # ---------- 4e. 市场解读 ----------
        commentary = self._call(self.cls, "get_market_commentary") or {}
        sources["commentary"] = SOURCE_CLS if commentary.get("title") else ""
        if not commentary.get("title"):
            unavailable.append("commentary")

        # ---------- 4f. 异动与炸板（涨停宝页核心）----------
        blasting = self._build_blasting(sources, unavailable)

        # ---------- 5. 其余 Tab ----------
        live = self._call(self.cls, "get_roll_list") or []
        sources["live"] = SOURCE_CLS if live else ""
        if not live:
            unavailable.append("live")

        tuyere = self._call(self.cls, "get_today_tuyere") or []
        sources["tuyere"] = SOURCE_CLS if tuyere else ""
        if not tuyere:
            unavailable.append("tuyere")

        mainline = self._call(self.cls, "get_mainline") or {}
        sources["mainline"] = SOURCE_CLS if mainline.get("mainlines") else ""
        if not mainline.get("mainlines"):
            mainline = self._mainline_from_kpl()
            sources["mainline"] = SOURCE_KPL if mainline.get("mainlines") else ""
        if not mainline.get("mainlines"):
            unavailable.append("mainline")

        # ---------- 5a. 风口页增强 ----------
        # 真 App 风口页 = 3 张板块卡（涨跌幅 + 主力净流入）+ 投资逻辑 +
        # 龙一/龙二/龙三 + 更多概念个股。
        # `todayTuyere` 只有标题/文案，涨跌幅和净流入要另外两个接口拼：
        #   板块涨跌幅 → mainline.winds[].plate_change（同一份数据，省一次请求）
        #   主力净流入 → /v2/quote/a/plate/info（参数 plate_codes 复数，批量）
        tuyere = self._build_tuyere(tuyere, mainline, sources, unavailable)

        # ---------- 5b. 风口/板块下钻明细 ----------
        # 龙一龙二龙三固定 3 只；「更多概念个股」走 plate/stocks 全量
        tuyere_drill = self._build_tuyere_drill(tuyere, sources, unavailable)

        ladder_stocks = self._build_zt_stocks(_em)
        big_rise = [s for s in ladder_stocks if s.get("limit_count", 0) <= 1]
        zt_bao = sorted(ladder_stocks, key=lambda s: -(s.get("limit_count") or 0))

        # 大涨股页顶部的板块胶囊（真 App 显示「低空经济(16) 军工(11) …」），
        # 按涨停家数降序，前端点了可以只看该板块
        big_rise_plates = self._group_by_plate(big_rise)

        sources["big_rise"] = self._last_ladder_source
        sources["zt_bao"] = self._last_ladder_source
        if not ladder_stocks:
            unavailable.extend(["big_rise", "zt_bao"])

        # 北交所：以前只筛「北交所涨停股」，没有涨停就整页空白。
        # 真实 App 的北交所 Tab 是**全量个股排行**（300+ 只），这里改为
        # 全量排行 + 标注哪些是涨停股，彻底解决空白问题。
        bse = self._build_bse(ladder_stocks, sources, unavailable)

        # ---------- 5b. 下钻明细：让页面上的卡片/梯队「点得开」----------
        drill = self._build_drill(fund_heat, tuyere, index_quotes, sources)

        # ---------- 5c. 2026-10-17 实机抓包新增 ----------
        # 涨停宝的「涨停全景图」：板块 -> 涨停数 -> 该板块涨停股
        zt_panorama = self._build_zt_panorama(sources, unavailable)
        # 板块下钻：成分股 / 人气股 / 分时（资金热力图和板块排行点开时用）
        plate_detail = self._build_plate_detail(drill, plate_ranking,
                                                sources, unavailable)
        # 大涨股：涨停原因（每只票下面那段业务说明）
        up_reason = self._build_up_reason(big_rise, sources, unavailable)
        # 直播页：板块异动锚点（分时图上的板块标注）
        anchor = self._build_anchor(sources, unavailable)
        # 日期相关：交易日列表 + 最近交易日
        trading_days = self._build_trading_days(sources, unavailable)
        # 券商月度观点 / 指数基础行情 / 自选 / 涨停宝工具位
        extra = self._build_extra(sources, unavailable)

        market = {
            "overview": overview,
            "heat": heat,
            "limit_performance": limit_perf,
            "fund_heat": fund_heat,
            "index_quotes": index_quotes,
            "commentary": commentary,
        }

        degraded = any(
            src == SOURCE_EM for key, src in sources.items()
            if not key.startswith("_") and src
        )

        return {
            "tab_bar": DINGPAN_TABS,
            "active_tab": "market",
            "market": market,
            "watchlist": [],
            "bse": bse,
            "bse_total": self._bse_total,
            "bse_banner": self._bse_banner,
            "live": live,
            "tuyere": tuyere,
            "tuyere_drill": tuyere_drill,
            "mainline": mainline,
            "big_rise": big_rise,
            "big_rise_plates": big_rise_plates,
            "zt_bao": zt_bao,
            "ranking": ranking,
            "plate_ranking": plate_ranking,
            "blasting": blasting,
            "drill": drill,
            "zt_panorama": zt_panorama,
            "plate_detail": plate_detail,
            "up_reason": up_reason,
            "anchor": anchor,
            "trading_days": trading_days,
            "extra": extra,
            "sources": {k: v for k, v in sources.items() if not k.startswith("_")},
            "unavailable": sorted(set(unavailable)),
            "degraded": degraded,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    # ------------------------------------------------------------------
    # 各数据块
    # ------------------------------------------------------------------
    def _build_overview(self, emotion, kpl_stats, _em, sources, unavailable) -> Dict[str, Any]:
        """市场概况：涨跌分布柱状图 + 上涨/持平/下跌 + 涨停/停牌/跌停"""
        series = emotion.get("distribution_series")
        if series and any(item["value"] for item in series):
            sources["market_overview"] = SOURCE_CLS
            return {
                "available": True,
                "series": series,
                "up_count": emotion.get("up_count"),
                "flat_count": emotion.get("flat_count"),
                "down_count": emotion.get("down_count"),
                "limit_up_count": emotion.get("limit_up_count"),
                "suspend_count": emotion.get("suspend_count"),
                "limit_down_count": emotion.get("limit_down_count"),
                # 开盘啦独立口径，用于交叉校验
                "crosscheck": {
                    "source": SOURCE_KPL,
                    "up_count": kpl_stats.get("up_count"),
                    "down_count": kpl_stats.get("down_count"),
                    "limit_up_count": kpl_stats.get("limit_up_count"),
                    "limit_down_count": kpl_stats.get("limit_down_count"),
                    "broken_ratio": kpl_stats.get("broken_ratio"),
                    "yesterday_zt_perf": kpl_stats.get("yesterday_zt_perf"),
                } if kpl_stats else None,
            }

        # 降级：第三源只能给涨跌停家数，给不出分布
        em = _em()
        has_em = (em.get("limit_up_count") is not None
                  or em.get("limit_down_count") is not None)
        if has_em:
            sources["market_overview"] = SOURCE_EM
        else:
            # 备用源也没数据时不能认领这一块，否则 degraded/sources 会撒谎
            sources["market_overview"] = ""
            unavailable.append("market_overview")
        unavailable.append("market_overview.distribution")
        return {
            "available": has_em,
            "series": [],
            "up_count": None,
            "flat_count": None,
            "down_count": None,
            "limit_up_count": em.get("limit_up_count"),
            "suspend_count": None,
            "limit_down_count": em.get("limit_down_count"),
            "note": "备用源不提供全市场涨跌分布，仅提供涨跌停家数",
        }

    def _build_heat(self, emotion, sources, unavailable) -> Dict[str, Any]:
        """市场热度 / 两市成交额 / 今日预测量能"""
        if emotion.get("market_degree") is not None:
            sources["market_heat"] = SOURCE_CLS
            return {
                "available": True,
                "degree": emotion.get("market_degree", 50),
                "turnover": emotion.get("turnover_str", ""),
                "turnover_change": emotion.get("turnover_change_str", ""),
                "preview_energy": emotion.get("preview_energy_str", ""),
                "preview_change": emotion.get("preview_change_str", ""),
            }
        sources["market_heat"] = ""
        unavailable.append("market_heat")
        return {"available": False, "degree": None, "turnover": "",
                "turnover_change": "", "preview_energy": "", "preview_change": ""}

    def _build_limit_performance(self, emotion, kpl_stats, _em, sources,
                                 unavailable) -> Dict[str, Any]:
        """涨停表现：封板率 / 封板 / 触及 / 昨涨停今表现 / 高开率 / 获利率 / 连板梯队"""
        board = emotion.get("limit_board") or {}
        has_cls = bool(emotion.get("up_ratio_str")) or bool(board.get("counts"))
        if has_cls:
            sources["limit_performance"] = SOURCE_CLS
            return {
                "available": True,
                "seal_rate": emotion.get("up_ratio_str", ""),
                "sealed_count": emotion.get("sealed_count"),
                "touched_count": emotion.get("touched_count"),
                "yesterday_perf": emotion.get("yesterday_perf_str", ""),
                "open_high_rate": emotion.get("open_high_ratio_str", ""),
                "profit_rate": emotion.get("profit_ratio_str", ""),
                "ladder": board,
                "crosscheck": {
                    "source": SOURCE_KPL,
                    "broken_ratio": kpl_stats.get("broken_ratio"),
                    "yesterday_zt_perf": kpl_stats.get("yesterday_zt_perf"),
                } if kpl_stats else None,
            }

        em = _em()
        if not em:
            # 备用源也没取到：如实标记整块不可用，不认领数据源
            sources["limit_performance"] = ""
            unavailable.append("limit_performance")
            return {
                "available": False,
                "seal_rate": "", "sealed_count": None, "touched_count": None,
                "yesterday_perf": "", "open_high_rate": "", "profit_rate": "",
                "ladder": {},
                "note": "三源均未取到涨停表现数据",
            }

        sources["limit_performance"] = SOURCE_EM
        unavailable.extend(["limit_performance.yesterday_perf",
                            "limit_performance.open_high_rate",
                            "limit_performance.profit_rate"])
        return {
            "available": True,
            "seal_rate": em.get("seal_rate_str", ""),
            "sealed_count": em.get("sealed_count"),
            "touched_count": em.get("touched_count"),
            "yesterday_perf": "",
            "open_high_rate": "",
            "profit_rate": "",
            "ladder": em.get("limit_board") or {},
            "note": "备用源不提供昨涨停今表现 / 高开率 / 获利率",
        }

    def _build_fund_heat(self, sources, unavailable) -> Dict[str, Any]:
        """资金热力图：排序规则 × 显示时间 矩阵"""
        matrix: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        got_any = False
        for mode in FUND_HEAT_SORT_MODES:
            matrix[mode] = {}
            way, rever = CLSClient.FUND_HEAT_MODES[mode]
            for window in FUND_HEAT_TIME_WINDOWS:
                days = CLSClient.FUND_HEAT_WINDOWS[window]
                rows = self._call(self.cls, "get_fund_heat", num=6, rever=rever,
                                  way=way, days=days) or []
                matrix[mode][window] = rows
                got_any = got_any or bool(rows)

        if got_any:
            sources["fund_heat"] = SOURCE_CLS
            return {
                "available": True,
                "sort_modes": FUND_HEAT_SORT_MODES,
                "time_windows": FUND_HEAT_TIME_WINDOWS,
                "active_sort": FUND_HEAT_SORT_MODES[0],
                "active_window": FUND_HEAT_TIME_WINDOWS[0],
                "matrix": matrix,
            }

        sources["fund_heat"] = ""
        unavailable.append("fund_heat")
        return {
            "available": False,
            "sort_modes": FUND_HEAT_SORT_MODES,
            "time_windows": FUND_HEAT_TIME_WINDOWS,
            "active_sort": FUND_HEAT_SORT_MODES[0],
            "active_window": FUND_HEAT_TIME_WINDOWS[0],
            "matrix": {},
            "note": "三源均未取到板块资金流数据",
        }

    # ------------------------------------------------------------------
    # 指数报价 / 个股排行 / 板块排行 / 异动与炸板 / 北交所 / 下钻
    # ------------------------------------------------------------------
    def _build_index_quotes(self, sources, unavailable) -> Dict[str, Any]:
        """指数报价（大盘页顶部 C1 模块）"""
        home = self._call(self.cls, "get_index_home") or {}
        quotes = home.get("quotes") or []
        if quotes:
            sources["index_quotes"] = SOURCE_CLS
            return {"available": True, "quotes": quotes}
        sources["index_quotes"] = ""
        unavailable.append("index_quotes")
        return {"available": False, "quotes": [], "note": "未取到指数报价"}

    def _build_ranking(self, sources, unavailable) -> Dict[str, Any]:
        """个股排行（C14）+ 排行主题字典

        主题字典里的「连板池 / 炸板池 / 跌停池」就是点击梯队、炸板后
        要展开的明细列表 —— 一并预取，页面才能离线点开。
        """
        topics = self._call(self.cls, "get_ranking_topics") or []
        by_topic = {t.get("topic"): t for t in topics if isinstance(t, dict)}

        def _pick(topic, fallback_style):
            t = by_topic.get(topic) or {}
            return t.get("style") or fallback_style

        boards = {}
        # 三个池走 way=time（按涨停时间），普通主题走 way=change（涨幅榜）
        for topic in ("all", "continuous_up", "up_open", "down"):
            is_pool = topic in cls_pool_topics()
            res = self._call(self.cls, "get_stock_ranking",
                             topic=topic,
                             style=_pick(topic, "TOPIC-STYLE-A-240223"),
                             way="time" if is_pool else "change",
                             reverse=1) or {}
            boards[topic] = res.get("items") or []

        got = any(boards.values())
        sources["ranking"] = SOURCE_CLS if got else ""
        if not got:
            unavailable.append("ranking")
        return {
            "available": got,
            "topics": topics,
            "active_topic": "all",
            "boards": boards,
            "note": "" if got else "未取到个股排行",
        }

    def _build_plate_ranking(self, sources, unavailable) -> Dict[str, Any]:
        """板块排行：板块列表（含涨跌幅/主力净流入/领涨股）+ 涨停家数排行"""
        lst = self._call(self.cls, "get_plate_list", "concept") or {}
        up_num = self._call(self.cls, "get_plate_up_num") or []
        plates = lst.get("plates") or []
        got = bool(plates or up_num)
        sources["plate_ranking"] = SOURCE_CLS if got else ""
        if not got:
            unavailable.append("plate_ranking")
        return {
            "available": got,
            "plates": plates,
            "up_num": up_num,
            "note": "" if got else "未取到板块排行",
        }

    def _build_blasting(self, sources, unavailable) -> Dict[str, Any]:
        """异动与炸板（涨停宝页核心）"""
        data = self._call(self.cls, "get_transaction_and_blasting") or {}
        fuses = data.get("blasting_fuses") or []
        articles = data.get("articles") or []
        got = bool(fuses or articles)
        sources["blasting"] = SOURCE_CLS if got else ""
        if not got:
            unavailable.append("blasting")
        return {
            "available": got,
            "blasting_fuses": fuses,
            "articles": articles,
            "note": "" if got else "未取到异动与炸板数据",
        }

    def _build_bse(self, ladder_stocks, sources, unavailable) -> List[Dict[str, Any]]:
        """北交所 Tab：全量个股排行（而非只筛涨停股）

        真实 App 的北交所页是「北京证券交易所 + 新三板」两块，共 351 + 6091 只，
        按涨跌幅排行。以前这里只取「北交所涨停股」，当天没有北交所涨停就整页
        空白 —— 那是数据缺失，不是「今天没涨停」。
        """
        info = self._call(self.cls, "get_bj_stock_info") or {}
        items = info.get("items") or []
        self._bse_total = info.get("total_num")
        # 北交所页顶部横幅（真 App 有，此前完全没接）
        banner = self._call(self.cls, "get_bj_banner") or []
        if banner:
            sources["bse_banner"] = SOURCE_CLS
        self._bse_banner = banner

        # 把涨停池里属于北交所的股票标注出来（limit_count / 涨停原因）。
        # ⚠️ 代码格式不一致：涨停池是 `bj920006`（前缀式），北交所排行是
        # `920196.BJ`（后缀式），必须统一成 6 位数字再比对，否则永远匹配不上。
        def _digits(code) -> str:
            return "".join(ch for ch in str(code or "") if ch.isdigit())[-6:]

        zt_map = {}
        for s in ladder_stocks:
            code = s.get("symbol") or s.get("code") or ""
            digits = _digits(code)
            is_bj = (str(code).lower().startswith("bj")
                     or str(code).upper().endswith(".BJ")
                     or digits[:2] in ("92", "83", "87", "43"))
            if is_bj and digits:
                zt_map[digits] = s

        if items:
            sources["bse"] = SOURCE_CLS
            merged = []
            for it in items:
                key = _digits(it.get("ori_code") or it.get("code"))
                zt = zt_map.get(key) or {}
                merged.append({
                    **it,
                    "is_limit_up": bool(zt),
                    "limit_count": zt.get("limit_count"),
                    "plate_name": zt.get("plate_name"),
                })
            return merged

        # 兜底：退回只用涨停池里的北交所股
        fallback = list(zt_map.values())
        sources["bse"] = self._last_ladder_source if fallback else ""
        if not fallback:
            unavailable.append("bse")
        return fallback

    def _build_drill(self, fund_heat, tuyere, index_quotes, sources) -> Dict[str, Any]:
        """下钻明细：把「点开才需要」的数据预先取好，页面才能离线展开。

        手机端不能直连财联社（跨域 + 签名），所以所有点击展开的数据
        必须在构建 bundle 时一次性抓完。
        """
        drill: Dict[str, Any] = {"index": {}, "plate": {}, "tuyere": {}}

        # ① 指数卡点开 -> 前三大成分股
        for q in (index_quotes.get("quotes") or [])[:6]:
            code = q.get("code")
            if not code:
                continue
            top3 = self._call(self.cls, "get_index_top3", code) or {}
            if top3.get("top3"):
                drill["index"][code] = top3

        # ② 资金热力图卡片点开 -> 板块详情 + 板块内涨停个股
        #    只取默认视图（主力净流入/今日）的前 6 个，控制请求数
        cards = []
        if fund_heat.get("available"):
            mode = fund_heat.get("active_sort")
            win = fund_heat.get("active_window")
            cards = ((fund_heat.get("matrix") or {}).get(mode, {}) or {}).get(win, []) or []
        # 资金热力图卡片用的是 plate_code（不是 secu_code），别取错字段
        codes = [c.get("plate_code") or c.get("secu_code") or c.get("code")
                 for c in cards][:6]
        codes = [c for c in codes if c]
        if codes:
            infos = self._call(self.cls, "get_plate_info", codes) or {}
            for code in codes:
                stocks = self._call(self.cls, "get_plate_up_stocks", code) or []
                drill["plate"][code] = {
                    "info": infos.get(code) or {},
                    "up_stocks": stocks,
                }

        # ③ 风口点开 -> 风口板块内个股
        for t in tuyere or []:
            code = t.get("plate_code")
            if not code:
                continue
            stocks = self._call(self.cls, "get_tuyere_stocks", code) or []
            drill["tuyere"][code] = stocks

        got = any(drill[k] for k in ("index", "plate", "tuyere"))
        sources["drill"] = SOURCE_CLS if got else ""
        return drill

    # ------------------------------------------------------------------
    # 涨停池 / 梯队
    # ------------------------------------------------------------------
    def _build_zt_stocks(self, _em) -> List[Dict[str, Any]]:
        """涨停池个股：开盘啦天梯优先，降级到东方财富涨停池"""
        tianti = self._call(self.kpl, "get_zhangting_tianti") or {}
        stocks = tianti.get("stocks") or []
        if stocks:
            self._last_ladder_source = SOURCE_KPL
            return [
                {
                    "code": s.get("code"),
                    "symbol": s.get("symbol"),
                    "name": s.get("name"),
                    "limit_count": s.get("height"),
                    "plate_name": s.get("plate_name"),
                    "first_limit_time": s.get("first_limit_time"),
                    "float_market_cap": s.get("float_market_val"),
                }
                for s in stocks
            ]

        em = _em()
        pool = em.get("zt_pool") or []
        if pool:
            self._last_ladder_source = SOURCE_EM
            return [
                {
                    "code": s.get("code"),
                    "symbol": self._with_prefix(s.get("code", "")),
                    "name": s.get("name"),
                    "limit_count": s.get("limit_count"),
                    "plate_name": s.get("plate_name"),
                    "first_limit_time": s.get("first_limit_time"),
                    "float_market_cap": s.get("float_market_cap"),
                }
                for s in pool
            ]
        self._last_ladder_source = ""
        return []

    def _mainline_from_kpl(self) -> Dict[str, Any]:
        """主线降级：用开盘啦板块涨停原因充当主线描述"""
        resumption = self._call(self.kpl, "get_plate_resumption", st=8) or {}
        plates = resumption.get("plates") or []
        mainlines = []
        for p in plates[:3]:
            stocks = p.get("stocks") or []
            faucet = stocks[0] if stocks else {}
            mainlines.append({
                "hot": 3,
                "continued": 2,
                "title": p.get("plate_name", ""),
                "desc": (faucet.get("reason_text") or "")[:180],
                "plates": [{
                    "plate_code": p.get("plate_code"),
                    "plate_name": p.get("plate_name"),
                    "faucet_name": faucet.get("name"),
                    "faucet_code": faucet.get("symbol"),
                    "faucet_reason": faucet.get("height_desc"),
                    "faucet_rise": faucet.get("change_pct"),
                }],
            })
        return {"desc": "开盘啦板块涨停原因（备用主线口径）", "mainlines": mainlines}

    @staticmethod
    def _with_prefix(code: str) -> str:
        code = str(code).zfill(6)
        if code.startswith(("60", "68")):
            return f"sh{code}"
        if code.startswith(("4", "8", "9")):
            return f"bj{code}"
        return f"sz{code}"

    @staticmethod
    def _safe(fn, *args, **kwargs):
        """任何单源失败都不得中断整个盯盘包的构建"""
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("盯盘数据块降级 (%s): %s", getattr(fn, "__name__", fn), exc)
            return None

    def _call(self, client, method: str, *args, **kwargs):
        """安全调用客户端方法。

        ⚠️ 不能写成 `self._safe(client.get_x)` —— 属性查找发生在 _safe 调用
        **之前**，客户端没有这个方法时会先抛 AttributeError，根本进不了 try。
        这里先把方法名当字符串传进来，再 getattr，缺失也走降级而不是崩溃。
        """
        fn = getattr(client, method, None)
        if not callable(fn):
            logger.warning("盯盘数据块降级: %s 无 %s 方法", type(client).__name__, method)
            return None
        return self._safe(fn, *args, **kwargs)


    # ------------------------------------------------------------------
    # 2026-10-17 模拟器实机抓包新增的数据块
    # ------------------------------------------------------------------
    def _group_by_plate(self, stocks) -> List[Dict[str, Any]]:
        """按所属板块给个股分组，产出大涨股页顶部的板块胶囊。

        真 App 显示成「低空经济(16) 军工(11) 机器人概念(5)…」，括号里是家数。
        没有 plate_name 的归到「其他」。
        """
        buckets: Dict[str, List[Dict[str, Any]]] = {}
        for s in (stocks or []):
            name = (s.get("plate_name") or "").strip() or "其他"
            buckets.setdefault(name, []).append(s)
        out = []
        for name, rows in buckets.items():
            out.append({
                "name": name,
                "count": len(rows),
                "codes": [r.get("code") or r.get("symbol") for r in rows],
            })
        out.sort(key=lambda x: (-x["count"], x["name"]))
        return out

    def _build_tuyere(self, tuyere, mainline, sources, unavailable) -> List[Dict[str, Any]]:
        """风口页顶部的 3 张板块卡：涨跌幅 + 主力净流入 + 投资逻辑

        `todayTuyere` 只给标题和文案；涨跌幅来自 `mainline.winds`（同一份
        mainline 响应，不必再发一次请求），主力净流入来自 `plate/info`。
        合并后每张卡 = 标题 + 涨跌幅 + 净流入 + 投资逻辑，才和真 App 一致。
        """
        if not tuyere:
            return []

        winds_by_code = {}
        for w in (mainline.get("winds") or []):
            code = w.get("plate_code")
            if code:
                winds_by_code[code] = w

        # 批量查板块净流入（一次请求拿全部）
        codes = [t.get("plate_code") for t in tuyere if t.get("plate_code")]
        info = self._call(self.cls, "get_plate_info", codes) if codes else {}

        merged = []
        for t in tuyere:
            code = t.get("plate_code") or ""
            w = winds_by_code.get(code) or {}
            inf = (info or {}).get(code) or {}
            ch = w.get("plate_change")
            item = {
                "id": t.get("id"),
                "title": t.get("title"),
                "plate_code": code,
                "subject_id": t.get("subject_id") or w.get("subject_id"),
                "interpret": t.get("interpret") or w.get("interpret"),
                # 板块涨跌幅（winds.plate_change 是小数，转成百分数字符串）
                "plate_change": ch,
                "plate_change_str": (f"{ch * 100:+.2f}%" if isinstance(ch, (int, float)) else ""),
                # 主力净流入
                "money_inflow": inf.get("main_money_inflow"),
                "money_inflow_str": inf.get("main_money_inflow_str"),
                # 风口龙头（龙一龙二龙三），领涨那只排最前
                "faucet_stocks": w.get("faucet_stocks") or [],
                "hot": w.get("hot"),
            }
            merged.append(item)

        if any(m.get("money_inflow_str") for m in merged):
            sources["tuyere_detail"] = SOURCE_CLS
        return merged

    def _build_tuyere_drill(self, tuyere, sources, unavailable) -> Dict[str, Any]:
        """风口的点开明细

        真 App 点风口卡能看：
          - 龙一/龙二/龙三（`tuyere/stocks` 固定返回 3 只，不是分页）
          - 更多概念个股（`plate/stocks` 全量，军工 434 只）
        两类都要预取，才能在手机上点开。
        """
        if not tuyere:
            sources["tuyere_drill"] = ""
            return {}

        out: Dict[str, Any] = {}
        got = False
        for t in tuyere[:3]:
            code = t.get("plate_code")
            if not code:
                continue
            # ① 龙一龙二龙三
            top3 = self._call(self.cls, "get_tuyere_stocks", code) or []
            # ② 板块全量成分股（真 App 的"更多概念个股"）
            allstocks = self._call(self.cls, "get_plate_stocks", code) or []
            out[code] = {
                "top3": top3,
                "all_stocks": allstocks[:30],
                "all_total": len(allstocks),
            }
            got = got or bool(top3 or allstocks)

        sources["tuyere_drill"] = SOURCE_CLS if got else ""
        if not got:
            unavailable.append("tuyere_drill")
        return out

    def _build_zt_panorama(self, sources, unavailable) -> Dict[str, Any]:
        """涨停全景图（涨停宝 Tab）

        真 App 涨停宝页 = 左侧「板块 -> 涨停数」列表 + 右侧该板块的涨停股明细。
        左侧数据来自 /v2/quote/a/plate/plate_up_num，右侧明细来自
        /v2/quote/a/plate/plate_up_stocks（带 date 才能取到当日）。
        取不到 date 时靠 `get_plate_up_stocks` 自己回落。
        """
        plates = self._call(self.cls, "get_plate_up_num") or []
        got = bool(plates)
        sources["zt_panorama"] = SOURCE_CLS if got else ""
        if not got:
            unavailable.append("zt_panorama")

        # 只预取前 6 个板块的明细，够首屏点开即可（整页预取太慢）
        detail: Dict[str, Any] = {}
        for p in plates[:6]:
            code = p.get("plate_code") or p.get("code")
            if not code:
                continue
            rows = self._call(self.cls, "get_plate_up_stocks", code) or []
            if rows:
                detail[code] = rows
        return {
            "available": got,
            "plates": plates,
            "detail": detail,
            "note": "" if got else "未取到涨停全景图",
        }

    def _build_plate_detail(self, drill, plate_ranking, sources, unavailable) -> Dict[str, Any]:
        """板块下钻明细：资金热力图 / 板块排行点开后的成分股、人气股、分时

        `_build_drill` 已经点过一遍板块了，这里**复用它的 plate 键**，
        只补充它没取的三样：成分股（get_plate_stocks）、人气股、分时线。
        避免对同一板块重复发请求（一次构建几十个请求太慢）。
        """
        codes: List[str] = []
        for code in (drill.get("plate") or {}):
            if code not in codes:
                codes.append(code)
        for p in (plate_ranking.get("plates") or [])[:6]:
            code = p.get("plate_code") or p.get("code")
            if code and code not in codes:
                codes.append(code)
        codes = codes[:8]

        stocks: Dict[str, Any] = {}
        popular: Dict[str, Any] = {}
        tline: Dict[str, Any] = {}
        got = False
        for code in codes:
            rows = self._call(self.cls, "get_plate_stocks", code)
            if rows:
                stocks[code] = rows
                got = True
            pop = self._call(self.cls, "get_plate_popular_stocks", code)
            if pop:
                popular[code] = pop
                got = True
            tl = self._call(self.cls, "get_stock_tline", code)
            if tl:
                tline[code] = tl
                got = True

        sources["plate_detail"] = SOURCE_CLS if got else ""
        if not got:
            unavailable.append("plate_detail")
        return {
            "available": got,
            "keys": codes,
            "stocks": stocks,
            "popular_stocks": popular,
            "tline": tline,
            "note": "" if got else "未取到板块下钻明细",
        }

    def _build_up_reason(self, big_rise, sources, unavailable) -> Dict[str, Any]:
        """大涨股的业务说明（/quote/stock/up_reason，批量按代码查）

        ⚠️ **代码格式必须转换**：`up_reason` 只认**前缀式**（`sz002085` / `sh600825`），
        而榜单返回的是**后缀式**（`920196.BJ` / `002085`）。直接拿榜代码去查会静默
        返回空 dict（不报错），前端就查不到任何业务说明。
        返回值的键也按请求格式原样返回，这里统一映射回榜代码，前端用榜代码查即可。
        """
        def _to_prefix(code) -> str:
            """`002085` / `sz002085` / `002085.SZ` 统一成 `sz002085`"""
            s = str(code or "").strip()
            if not s:
                return ""
            digits = "".join(ch for ch in s if ch.isdigit())[-6:]
            if not digits:
                return s
            if s[:2].lower() in ("sz", "sh", "bj"):
                return s[:2].lower() + digits
            if digits[:2] in ("92", "83", "87", "43"):
                return "bj" + digits
            if digits[0] in ("6", "9"):
                return "sh" + digits
            return "sz" + digits

        codes: List[str] = []
        code_map: Dict[str, str] = {}   # 前缀式 -> 榜代码
        for s in (big_rise or [])[:20]:
            c = s.get("code") or s.get("symbol")
            pfx = _to_prefix(c)
            if pfx and pfx not in codes:
                codes.append(pfx)
                code_map[pfx] = c
        if not codes:
            sources["up_reason"] = ""
            return {"available": False, "items": {}, "note": "无大涨股，未取原因"}

        res = self._call(self.cls, "get_stock_up_reason", codes) or {}
        # 把前缀键映射回榜代码，前端直接用列表里的 code 查
        items = {code_map.get(k, k): v for k, v in res.items()}
        got = bool(items)
        sources["up_reason"] = SOURCE_CLS if got else ""
        if not got:
            unavailable.append("up_reason")
        return {"available": got, "items": items,
                "note": "" if got else "未取到大涨股业务说明"}

    def _build_anchor(self, sources, unavailable) -> Dict[str, Any]:
        """直播页：板块异动锚点（分时图上的板块标注）"""
        rows = self._call(self.cls, "get_transaction_anchor") or []
        got = bool(rows)
        sources["anchor"] = SOURCE_CLS if got else ""
        if not got:
            unavailable.append("anchor")
        return {"available": got, "items": rows,
                "note": "" if got else "未取到板块异动锚点"}

    def _build_trading_days(self, sources, unavailable) -> Dict[str, Any]:
        """交易日列表 + 最近交易日（大涨股页顶部日期选择器用）"""
        closest = self._call(self.cls, "get_closest_trading_day") or ""
        days: List[str] = []
        if closest:
            # 回看 30 个自然日，覆盖一个多月
            from datetime import timedelta
            try:
                end = datetime.strptime(closest[:10], "%Y-%m-%d")
                start = end - timedelta(days=30)
                days = self._call(self.cls, "get_trading_days",
                                  start.strftime("%Y-%m-%d"),
                                  end.strftime("%Y-%m-%d")) or []
            except ValueError:
                days = []
        got = bool(closest or days)
        sources["trading_days"] = SOURCE_CLS if got else ""
        if not got:
            unavailable.append("trading_days")
        return {"available": got, "closest": closest, "days": days,
                "note": "" if got else "未取到交易日信息"}

    def _build_extra(self, sources, unavailable) -> Dict[str, Any]:
        """券商月度观点 / 指数基础行情 / 自选 / 涨停宝工具位 / 首页聚合"""
        qs = self._call(self.cls, "get_qs_info_all") or []
        idx_basic: Dict[str, Any] = {}
        for code in getattr(CLSClient, "INDEX_ORDER", [])[:6]:
            d = self._call(self.cls, "get_index_basic", code)
            if d:
                idx_basic[code] = d
        opt = self._call(self.cls, "get_optional_stock") or []
        tool = self._call(self.cls, "get_limit_up_tool") or {}

        got = bool(qs or idx_basic or tool)
        sources["extra"] = SOURCE_CLS if got else ""
        if not got:
            unavailable.append("extra")
        return {
            "available": got,
            "qs_info": qs,
            "index_basic": idx_basic,
            "optional_stock": opt,
            "limit_up_tool": tool,
            "note": "" if got else "未取到补充数据",
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    agg = DingpanAggregator()
    bundle = agg.build()
    print("盯盘数据包构建完成")
    print("  tabs        :", [t["label"] for t in bundle["tab_bar"]])
    print("  数据源      :", bundle["sources"])
    print("  降级        :", bundle["degraded"])
    print("  不可用块    :", bundle["unavailable"])
    ov = bundle["market"]["overview"]
    print("  市场概况    :", [(s["label"], s["value"]) for s in ov.get("series", [])])
    print("  涨/平/跌    :", ov.get("up_count"), ov.get("flat_count"), ov.get("down_count"))
    print("  热度        :", bundle["market"]["heat"])
    lp = bundle["market"]["limit_performance"]
    print("  涨停表现    :", {k: v for k, v in lp.items() if k != "ladder"})
    print("  连板梯队    :", lp.get("ladder"))
    fh = bundle["market"]["fund_heat"]
    first = (fh["matrix"].get("主力净流入", {}).get("今日") or [])[:3]
    print("  资金热力图  :", [(x["plate_name"], x["inflow_str"], x["change"]) for x in first])
    print("  直播/风口   :", len(bundle["live"]), len(bundle["tuyere"]))
