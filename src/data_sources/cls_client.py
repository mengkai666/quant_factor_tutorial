"""财联社 (CLS) API 客户端

依据抓包协议封装，覆盖「盯盘」页面所需的全部可用接口。

协议要点:
  - 行情域名: https://x-quote.cls.cn     (GET, query string)
  - 内容域名: https://api3.cls.cn        (GET, query string)
  - 公共参数: app/sv/os/mb/ov/channel/sign/motif/net/province_code/uid/token
  - 版本: 8.5.4 (app=cailianpress)

已验证可用接口清单 (2026-10-17 实测):
  [x-quote]
    1. /v2/quote/a/stock/emotion              市场概况 + 涨停表现 + 连板梯队
    2. /v2/quote/a/stock/limit_up_performance 涨停表现「查看更多」同比明细
    3. /v2/quote/a/plate/fund_heat            资金热力图 (way/rever/days 矩阵)
    4. /v2/quote/a/plate/up_down_analysis     涨停池 / 连板梯队明细
    5. /v2/quote/a/index/list                 指数列表
  [api3]
    6. /v2/dingPan/mainline                   主线机会
    7. /v2/todayTuyere                        今日风口
    8. /v1/roll/get_roll_list                 电报 / 直播流
"""

import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

import requests

logger = logging.getLogger(__name__)


class CLSClient:
    QUOTE_BASE = "https://x-quote.cls.cn"
    CONTENT_BASE = "https://api3.cls.cn"

    DEFAULT_SIGN = "5b4c4c6a3b9db1dd279a0d713111c4d0"

    # 资金热力图排序规则 -> (way, rever) 映射
    # 实测: way 仅 money_inflow / change 两档真实生效, rever 控制正/反向
    FUND_HEAT_MODES = {
        "主力净流入": ("money_inflow", 0),
        "主力净流出": ("money_inflow", 1),
        "按涨幅": ("change", 0),
        "按跌幅": ("change", 1),
    }
    FUND_HEAT_WINDOWS = {"今日": 1, "5日": 5, "10日": 10}

    # 个股排行主题字典的兜底值（正常应实时拉 /v2/quote/a/stock/ranking/topics）。
    # topic+style 是**必填**参数，缺任一个都返回 9001。
    RANKING_TOPICS = [
        ("全部A股", "all", "TOPIC-STYLE-A-240223"),
        ("沪深主板", "hszb", "TOPIC-STYLE-A-240223"),
        ("创业板", "cy", "TOPIC-STYLE-A-240223"),
        ("科创板", "kc", "TOPIC-STYLE-A-240223"),
        ("沪深300", "hs300", "TOPIC-STYLE-A-240223"),
        ("上证50", "sz50", "TOPIC-STYLE-A-240223"),
        ("连板池", "continuous_up", "TOPIC-STYLE-B-240223"),
        ("炸板池", "up_open", "TOPIC-STYLE-B-240223"),
        ("跌停池", "down", "TOPIC-STYLE-B-240223"),
        ("北交所", "bj", "TOPIC-STYLE-C-240223"),
    ]

    # 指数报价固定返回这 6 个，顺序即页面展示顺序
    INDEX_ORDER = ["sh000001", "sz399001", "sz399006", "sh000688", "sh000300", "sh000905"]

    # 个股排行 / 北交所的排序维度 -> (way, fields, reverse)
    #
    # ⚠️ 2026-10-17 模拟器实机抓包确认：真 App 发的是 `reverse`（不是 `rever`），
    # 而 `/v2/quote/a/plate/fund_heat` 发的是 `rever` —— 两个接口命名不一致，
    # 写错不会报错，只会**静默按默认排序返回**，看起来"能用"但永远是涨幅榜。
    #
    # 池类（连板/炸板/跌停）走 way=time，且 fields 要带 reason/limit_up_days/plate。
    RANK_SORTS = {
        "涨幅榜": ("change", "change,last_px", 1),
        "跌幅榜": ("change", "change,last_px", 0),
        "量比": ("qrr", "qrr,last_px", 1),
        "换手率": ("tr", "tr,last_px", 1),
        "成交额": ("business_balance", "business_balance,last_px", 1),
    }
    # 池类榜单（连板池/炸板池/跌停池）的固定字段集
    POOL_FIELDS = ("change,change_px,last_px,time,reason,is_st,"
                   "limit_up_days,plate,change_color,last_px")
    # 池类 topic —— 这三个走 way=time 按涨停时间排序，且 style 必须是 B
    POOL_TOPICS = {"continuous_up", "up_open", "down"}

    # topic -> style 配套（来自 /ranking/topics 实测）
    # ⚠️ 池 topic 配 A style 不会报错，但会返回 0 条，看着像"今天没连板"
    TOPIC_STYLE = {
        "all": "TOPIC-STYLE-A-240223",
        "hszb": "TOPIC-STYLE-A-240223",
        "cy": "TOPIC-STYLE-A-240223",
        "kc": "TOPIC-STYLE-A-240223",
        "hs300": "TOPIC-STYLE-A-240223",
        "sz50": "TOPIC-STYLE-A-240223",
        "continuous_up": "TOPIC-STYLE-B-240223",
        "up_open": "TOPIC-STYLE-B-240223",
        "down": "TOPIC-STYLE-B-240223",
        "bj": "TOPIC-STYLE-C-240223",
    }

    # 北交所 / 新三板：market=bj 是北交所(351只)，market=all 是新三板全部(6091只)
    BSE_MARKETS = {"bj": "北京证券交易所", "all": "新三板"}

    # 板块排行的分类（实测 industry / concept 有数据，style / region 当日为空）
    PLATE_TYPES = {"行业": "industry", "概念": "concept", "风格": "style", "地域": "region"}

    # 涨跌分布区间 -> (字段名, 展示标签)，顺序即图表从左到右
    DISTRIBUTION_BUCKETS = [
        ("up_num", "涨停"),
        ("up_10", "大于+8%"),
        ("up_8", "+6%"),
        ("up_6", "+4%"),
        ("up_4", "+2%"),
        ("up_2", "0%"),
        ("flat_num", "平"),
        ("down_2", "-2%"),
        ("down_4", "-4%"),
        ("down_6", "-6%"),
        ("down_8", "-8%"),
        ("down_10", "小于-8%"),
        ("down_num", "跌停"),
    ]

    def __init__(self, sign: Optional[str] = None, timeout: int = 10):
        self.sign = sign or self.DEFAULT_SIGN
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "okhttp/4.9.3",
        })

    def _common_params(self) -> Dict[str, str]:
        return {
            "app": "cailianpress",
            "sv": "8.5.4",
            "os": "android",
            "mb": "nubia-NX709J",
            "ov": "28",
            "channel": "8",
            "sign": self.sign,
            "motif": "0",
            "net": "",
            "province_code": "4107",
            "uid": "",
            "token": "",
        }

    def _get(self, base_url: str, path: str, **kwargs) -> Dict[str, Any]:
        params = self._common_params()
        params.update({k: str(v) for k, v in kwargs.items()})
        url = f"{base_url}{path}"
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            res_json = resp.json()
            code = res_json.get("code", res_json.get("errno"))
            if code not in (200, 0):
                logger.warning(f"CLS API {path} returned non-success code: {code}")
            return res_json
        except Exception as e:
            logger.error(f"CLS request failed for {path}: {e}")
            return {"code": -1, "errno": -1, "error": str(e), "data": {}}

    # ------------------------------------------------------------------
    # 1. 市场概况 / 涨停表现 / 连板梯队
    # ------------------------------------------------------------------
    def get_emotion_raw(self) -> Dict[str, Any]:
        """原始 emotion 响应 data 段（未加工，供兜底解析使用）"""
        return self._get(self.QUOTE_BASE, "/v2/quote/a/stock/emotion").get("data", {}) or {}

    def get_market_emotion(self) -> Dict[str, Any]:
        """获取全市场情绪总览（盯盘页「市场概况 + 涨停表现」数据源）

        Returns:
            market_degree        市场热度 0-100
            turnover_str         两市成交额，如 '1.56万亿'
            turnover_change_str  较上一日，如 '-776亿'
            preview_*            今日预测量能
            up_ratio_str         封板率，如 '55.00%'
            sealed_count         封板家数
            touched_count        触及(炸板)家数
            yesterday_perf_str   昨涨停今表现，如 '-1.74%'
            open_high_ratio_str  高开率
            profit_ratio_str     获利率
            up/down/flat_count   上涨/下跌/持平家数
            limit_up/down_count  涨停/跌停家数
            suspend_count        停牌家数
            distribution_bins    涨跌分布原始分桶
            distribution_series  按 DISTRIBUTION_BUCKETS 排序的图表序列
            limit_board          连板梯队 (tiers/counts/rates)
        """
        data = self.get_emotion_raw()
        if not data:
            return {}

        up_down_dis = data.get("up_down_dis", {}) or {}
        limit_board = data.get("limit_up_board", {}) or {}

        def _int(v, default=0):
            try:
                return int(float(v))
            except (TypeError, ValueError):
                return default

        bins = {
            "up_num": _int(up_down_dis.get("up_num")),
            "up_10": _int(up_down_dis.get("up_10")),
            "up_8": _int(up_down_dis.get("up_8")),
            "up_6": _int(up_down_dis.get("up_6")),
            "up_4": _int(up_down_dis.get("up_4")),
            "up_2": _int(up_down_dis.get("up_2")),
            "flat_num": _int(up_down_dis.get("flat_num")),
            "down_2": _int(up_down_dis.get("down_2")),
            "down_4": _int(up_down_dis.get("down_4")),
            "down_6": _int(up_down_dis.get("down_6")),
            "down_8": _int(up_down_dis.get("down_8")),
            "down_10": _int(up_down_dis.get("down_10")),
            "down_num": _int(up_down_dis.get("down_num")),
        }
        series = [
            {"key": key, "label": label, "value": bins.get(key, 0)}
            for key, label in self.DISTRIBUTION_BUCKETS
        ]

        return {
            "market_degree": _int(data.get("market_degree"), 50),
            "turnover_str": data.get("shsz_balance", ""),
            "turnover_change_str": data.get("shsz_balance_change_px", ""),
            "preview_energy_str": data.get("preview_balance", ""),
            "preview_change_str": data.get("preview_balance_change_px", ""),
            "up_ratio_str": data.get("up_ratio", ""),
            "sealed_count": _int(data.get("up_ratio_num")),
            "touched_count": _int(data.get("up_open_num")),
            "yesterday_perf_str": data.get("performance", ""),
            "open_high_ratio_str": data.get("up_open_ratio", ""),
            "profit_ratio_str": data.get("profit_ratio", ""),
            "up_count": _int(up_down_dis.get("rise_num")),
            "down_count": _int(up_down_dis.get("fall_num")),
            "flat_count": _int(up_down_dis.get("flat_num")),
            "limit_up_count": _int(up_down_dis.get("up_num")),
            "limit_down_count": _int(up_down_dis.get("down_num")),
            "suspend_count": _int(up_down_dis.get("suspend_num")),
            "distribution_bins": bins,
            "distribution_series": series,
            "limit_board": {
                "tiers": limit_board.get("row1", []),
                "counts": limit_board.get("row2", []),
                "rates": limit_board.get("row3", []),
            },
        }

    def get_limit_up_performance(self) -> Dict[str, Any]:
        """涨停表现「查看更多」同比明细

        Returns:
            continuous_limit_rate / _yd  连板率 今 / 昨
            limit_up_num / _yd           涨停家数 今 / 昨
            up_ratio / _yd               封板率 今 / 昨
        """
        raw = self._get(self.QUOTE_BASE, "/v2/quote/a/stock/limit_up_performance")
        data = raw.get("data", {}) or {}
        if not data:
            return {}
        return {
            "continuous_limit_rate": data.get("continuous_limit_rate", ""),
            "continuous_limit_rate_prev": data.get("continuous_limit_rate_yd", ""),
            "limit_up_num": data.get("limit_up_num", ""),
            "limit_up_num_prev": data.get("limit_up_num_yd", ""),
            "up_ratio": data.get("up_ratio", ""),
            "up_ratio_prev": data.get("up_ratio_yd", ""),
        }

    # ------------------------------------------------------------------
    # 2. 资金热力图
    # ------------------------------------------------------------------
    def get_fund_heat(self, num: int = 10, rever: int = 0, way: str = "money_inflow",
                      days: int = 1) -> List[Dict[str, Any]]:
        """获取板块资金热力图

        Args:
            num:   返回条数
            rever: 0=正向(流入/涨幅榜) 1=反向(流出/跌幅榜)
            way:   'money_inflow' 按主力资金 | 'change' 按涨跌幅
            days:  1 / 5 / 10 日窗口

        Note:
            保留旧签名兼容 (num, rever)；`way`/`days` 为盯盘页新增。
        """
        raw = self._get(self.QUOTE_BASE, "/v2/quote/a/plate/fund_heat",
                        way=way, num=num, days=days, rever=rever)
        plates = raw.get("data", []) or []
        result = []
        for p in plates:
            if not isinstance(p, dict):
                continue
            result.append({
                "plate_code": p.get("secu_code"),
                "plate_name": p.get("secu_name"),
                "change": p.get("change"),
                "change_num": p.get("change_num"),
                "inflow_str": p.get("money_inflow"),
                "inflow_num": p.get("money_inflow_num"),
            })
        return result

    def get_fund_heat_matrix(self, num: int = 6) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        """构建「排序规则 × 显示时间」完整矩阵，供前端胶囊按钮即时切换。

        Returns:
            { "主力净流入": { "今日": [...], "5日": [...], "10日": [...] }, ... }
        """
        matrix: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        for mode_label, (way, rever) in self.FUND_HEAT_MODES.items():
            matrix[mode_label] = {}
            for window_label, days in self.FUND_HEAT_WINDOWS.items():
                matrix[mode_label][window_label] = self.get_fund_heat(
                    num=num, rever=rever, way=way, days=days
                )
        return matrix

    # ------------------------------------------------------------------
    # 3. 涨停池 / 连板梯队明细
    # ------------------------------------------------------------------
    def get_up_down_analysis(self, date: str = "", up_limit: int = 1) -> Dict[str, Any]:
        """涨停池 / 连板梯队明细 (plate/up_down_analysis)"""
        raw = self._get(self.QUOTE_BASE, "/v2/quote/a/plate/up_down_analysis",
                        date=date, up_limit=up_limit)
        data = raw.get("data", {}) or {}
        groups = []
        for group in data.get("continuous_limit_up", []) or []:
            stocks = []
            for s in group.get("stock_list", []) or []:
                stocks.append({
                    "code": s.get("secu_code"),
                    "name": s.get("secu_name"),
                })
            groups.append({"height": group.get("height", 1), "stocks": stocks})
        return {"continuous_limit_up": groups, "raw": data}

    def get_index_list(self) -> List[Dict[str, Any]]:
        """指数列表 (index/list)"""
        raw = self._get(self.QUOTE_BASE, "/v2/quote/a/index/list")
        data = raw.get("data", []) or []
        return [
            {"code": i.get("secu_code"), "name": i.get("secu_name"), "market": i.get("market")}
            for i in data if isinstance(i, dict)
        ]

    # ------------------------------------------------------------------
    # 4. 内容域：主线 / 风口 / 直播
    # ------------------------------------------------------------------
    def get_mainline(self) -> Dict[str, Any]:
        """获取市场主线、市场风格、每日风口 (api3 /v2/dingPan/mainline)

        ⚠️ 这一个接口同时提供**盯盘三个 Tab 的数据**，之前只解析了 chances 的
        faucet_1，导致主线页和风口页大面积空白。完整结构：

        - `chance_desc`/`mainLine_desc`/`wind_desc`/`style_desc` —— 各模块副标题
        - `chances[]` —— 主线机会，每个含 `plates[]`（板块 + 龙一/龙二/龙三
          `faucet_1/2/3` + 关联文章 `article` + 涨停家数 `rise_count`）
        - `styles` —— 市场风格，含 `index_1.change30`（指数 30 日区间涨幅）
          和 `index_1.stacks[]`（区间涨幅 top3 个股）
        - `winds[]` —— 每日风口，含 `faucet_stocks[]`（风口龙头）和 `plate_change`

        注意 `winds` 里 `last_px`/`pre30_close` 实测恒为 0，只有 `rise_range` 有效；
        板块净流入要另外调 `/v2/quote/a/plate/info`（参数 `plate_codes` 复数）。
        """
        raw = self._get(self.CONTENT_BASE, "/v2/dingPan/mainline")
        data = raw.get("data", {}) or {}

        # ---- 主线机会 ----
        mainline_list = []
        for c in data.get("chances", []) or []:
            if not isinstance(c, dict):
                continue
            plates = []
            for p in c.get("plates", []) or []:
                if not isinstance(p, dict):
                    continue
                faucets = []
                for k in ("faucet_1", "faucet_2", "faucet_3"):
                    f = p.get(k) or {}
                    if not isinstance(f, dict) or not f.get("stock_name"):
                        continue
                    faucets.append({
                        "name": f.get("stock_name"),
                        "code": f.get("stock_code"),
                        "reason": f.get("reason"),
                        "rise_range": f.get("rise_range"),
                        "status": f.get("status"),
                    })
                art = p.get("article") or {}
                plates.append({
                    "plate_code": p.get("code"),
                    "plate_name": p.get("name"),
                    "reason": p.get("reason"),
                    "rise_count": p.get("rise_count"),
                    "rise_range": p.get("rise_range"),
                    "time": p.get("time"),
                    "faucets": faucets,
                    "article": {
                        "id": art.get("id"),
                        "title": art.get("title"),
                        "img": art.get("img"),
                        "read_count": art.get("read_count"),
                        "time": art.get("time"),
                    } if isinstance(art, dict) and art.get("id") else None,
                })
            mainline_list.append({
                "id": c.get("id"),
                "hot": c.get("hot", 1),
                "continued": c.get("continued", 1),
                "title": c.get("title", ""),
                "desc": c.get("mainLine_desc", ""),
                "plates": plates,
            })

        # ---- 市场风格 ----
        styles_raw = data.get("styles") or {}
        styles = None
        if isinstance(styles_raw, dict) and styles_raw:
            idx = styles_raw.get("index_1") or {}
            stacks = []
            for s in idx.get("stacks", []) or []:
                if not isinstance(s, dict):
                    continue
                stacks.append({
                    "code": s.get("stock_code"),
                    "name": s.get("stock_name"),
                    "last_px": s.get("last_px"),
                    "pre30_close": s.get("pre30_close"),
                    "rise_range": s.get("rise_range"),
                    "change_px": s.get("change_px"),
                    "trade_status": s.get("trade_status"),
                })
            styles = {
                "id": styles_raw.get("id"),
                "title": styles_raw.get("title", ""),
                "interpret": styles_raw.get("interpret", ""),
                "index_code": idx.get("code"),
                "index_name": idx.get("name"),
                "index_change30": idx.get("change30"),
                "stacks": stacks,
            }

        # ---- 每日风口 ----
        winds = []
        for w in data.get("winds", []) or []:
            if not isinstance(w, dict):
                continue
            faucet_stocks = []
            for s in w.get("faucet_stocks", []) or []:
                if not isinstance(s, dict):
                    continue
                faucet_stocks.append({
                    "code": s.get("stock_code"),
                    "name": s.get("stock_name"),
                    "rise_range": s.get("rise_range"),
                    "status": s.get("status"),
                })
            winds.append({
                "id": w.get("id"),
                "title": w.get("title"),
                "plate_code": w.get("plate_code"),
                "subject_id": w.get("subject_id"),
                "hot": w.get("hot"),
                "plate_change": w.get("plate_change"),
                "interpret": w.get("interpret"),
                "img": w.get("img"),
                "faucet_stocks": faucet_stocks,
            })

        return {
            "chance_desc": data.get("chance_desc", ""),
            "mainline_desc": data.get("mainLine_desc", ""),
            "wind_desc": data.get("wind_desc", ""),
            "style_desc": data.get("style_desc", ""),
            "mainlines": mainline_list,
            "styles": styles,
            "winds": winds,
        }

    def get_today_tuyere(self) -> List[Dict[str, Any]]:
        """获取今日风口 (api3 /v2/todayTuyere)

        `id` 和 `subject_id` 也要带上：`subject_id` 是「风口追踪」文章列表的
        专题 id（虽然 `/api/subject/{id}/article` 因签名拿不到，但要留好，
        以后拿到签名算法就能直接补上）。
        """
        raw = self._get(self.CONTENT_BASE, "/v2/todayTuyere")
        tuyere_list = (raw.get("data", {}) or {}).get("today_tuyere", []) or []
        result = []
        for item in tuyere_list:
            if not isinstance(item, dict):
                continue
            result.append({
                "id": item.get("id"),
                "title": item.get("title"),
                "plate_code": item.get("plate_code"),
                "subject_id": item.get("subject_id"),
                "interpret": item.get("interpret"),
            })
        return result

    def get_roll_list(self) -> List[Dict[str, Any]]:
        """获取电报 / 直播流 (api3 /v1/roll/get_roll_list) —— 盯盘「直播」Tab

        ⚠️ 该接口的 sign 是对「参与签名的参数集合」算出的固定值，一旦额外传入
        rn / last_time 等参数，服务端立即返回 `10012 签名错误`。
        因此这里必须 **不附加任何查询参数** 调用（实测返回最近 16 条电报）。
        """
        raw = self._get(self.CONTENT_BASE, "/v1/roll/get_roll_list")
        rolls = (raw.get("data", {}) or {}).get("roll_data", []) or []
        result = []
        for r in rolls:
            if not isinstance(r, dict):
                continue
            # 注意: plate_list / stock_list 键存在但值为 null，不能用 get(k, []) 兜底
            stock_list = r.get("stock_list") or []
            plate_list = r.get("plate_list") or []
            result.append({
                "id": r.get("id"),
                "title": r.get("title") or r.get("brief") or "",
                "content": r.get("content") or r.get("brief") or "",
                "time": r.get("ctime"),
                "level": r.get("level"),
                "reading_num": r.get("reading_num"),
                "is_top": bool(r.get("is_top")),
                "plate_list": [
                    {"code": p.get("code"), "name": p.get("name")}
                    for p in plate_list if isinstance(p, dict)
                ],
                "stock_list": [
                    {"code": s.get("StockID") or s.get("code"),
                     "name": s.get("name") or s.get("Name")}
                    for s in stock_list if isinstance(s, dict)
                ],
            })
        return result

    # ------------------------------------------------------------------
    # 5. 指数报价 / 个股排行（大盘页顶部与 C14 模块）
    # ------------------------------------------------------------------
    def get_index_home(self) -> Dict[str, Any]:
        """指数报价 (quote /quote/index/home) —— 大盘页顶部 C1 模块

        返回 6 个指数的报价 + 涨跌家数，另外附一份全市场涨跌分布。
        这是页面最顶部、**之前完全缺失**的一块。
        """
        raw = self._get(self.QUOTE_BASE, "/quote/index/home")
        data = raw.get("data", {}) or {}

        quotes = []
        for q in data.get("index_quote", []) or []:
            if not isinstance(q, dict):
                continue
            change = q.get("change")
            quotes.append({
                "code": q.get("secu_code"),
                "name": q.get("secu_name"),
                "last_px": q.get("last_px"),
                "change": change,
                "change_str": f"{change * 100:+.2f}%" if isinstance(change, (int, float)) else "",
                "change_px": q.get("change_px"),
                "up_num": q.get("up_num"),
                "down_num": q.get("down_num"),
                "flat_num": q.get("flat_num"),
            })
        # 按 INDEX_ORDER 排序，保证与 App 展示顺序一致
        order = {c: i for i, c in enumerate(self.INDEX_ORDER)}
        quotes.sort(key=lambda x: order.get(x["code"], 99))

        return {"quotes": quotes, "up_down_dis": data.get("up_down_dis") or {}}

    def get_index_top3(self, secu_code: str) -> Dict[str, Any]:
        """指数前三大成分股 (quote /v2/quote/a/index/top3_component)

        点开顶部指数卡时用 —— 回答「这个指数今天靠谁涨的」。
        """
        raw = self._get(self.QUOTE_BASE, "/v2/quote/a/index/top3_component",
                        secu_code=secu_code)
        data = raw.get("data", {}) or {}
        return {
            "code": data.get("secu_code") or secu_code,
            "name": data.get("secu_name", ""),
            "last_px": data.get("last_px"),
            "change_30": data.get("change_30", ""),
            "top3": [
                {
                    "code": s.get("secu_code"),
                    "name": s.get("secu_name"),
                    "last_px": s.get("last_px"),
                    "change_px": s.get("change_px"),
                    "change_30": s.get("change_30"),
                }
                for s in (data.get("top3") or []) if isinstance(s, dict)
            ],
        }

    def get_ranking_topics(self) -> List[Dict[str, str]]:
        """排行主题字典 (quote /v2/quote/a/stock/ranking/topics)"""
        raw = self._get(self.QUOTE_BASE, "/v2/quote/a/stock/ranking/topics")
        data = raw.get("data")
        if not isinstance(data, list) or not data:
            return [{"name": n, "topic": t, "style": s} for n, t, s in self.RANKING_TOPICS]
        return [
            {"name": t.get("name"), "topic": t.get("topic"), "style": t.get("style")}
            for t in data if isinstance(t, dict)
        ]

    def get_stock_ranking(self, topic: str = "all", style: Optional[str] = None,
                          index: int = 1, way: str = "change", reverse: int = 1,
                          deviation_num: int = 20, include_st: int = 1,
                          fields: Optional[str] = None) -> Dict[str, Any]:
        """个股排行 (quote /v2/quote/a/stock/ranking) —— 大盘页 C14 模块

        topic + style **必填**（缺任一个返回 9001）。
        topic 取 'all' 全部A股 / 'continuous_up' 连板池 / 'up_open' 炸板池 /
        'down' 跌停池 / 'bj' 北交所 —— 这正是「点开连板梯队看明细」的数据来源。

        ⚠️ **排序参数是 `reverse` 不是 `rever`**（真 App 抓包确认）。写错不报错，
        只会永远返回涨幅榜 —— 这也是为什么之前「跌幅榜/量比/换手率/成交额」点不动。

        ⚠️ **`style` 必须与 `topic` 配套**：普通主题用 `TOPIC-STYLE-A-240223`，
        三个池用 `TOPIC-STYLE-B-240223`，北交所用 `TOPIC-STYLE-C-240223`。
        传错（例如池 topic 配 A style）不会报错，但**返回 0 条** —— 看起来像"今天没连板"，
        实际是参数配错。这里根据 topic 自动选 style，也可以显式覆盖。

        ⚠️ **`index` 是偏移量不是页码**：deviation_num=30 时翻页要传 1 / 31 / 61，
        不是 1 / 2 / 3。

        `way` 取值（对应 UI 上的 5 个排序 Tab）：
            change           涨幅榜(reverse=1) / 跌幅榜(reverse=0)
            qrr              量比
            tr               换手率
            business_balance 成交额
            time             池类（连板/炸板/跌停）按涨停时间

        `fields` 必须与 `way` 匹配，否则对应数值字段不返回（涨跌幅能拿到但量比是 None）。
        """
        if fields is None:
            if topic in self.POOL_TOPICS:
                fields = self.POOL_FIELDS
            else:
                # fields 必须与 way 匹配，否则对应数值字段不返回（量比/换手率/成交额会是 None）
                fields = {
                    "change": "change,last_px",
                    "qrr": "qrr,last_px",
                    "tr": "tr,last_px",
                    "business_balance": "business_balance,last_px",
                    "time": self.POOL_FIELDS,
                }.get(way, "change,last_px")
        # style 按 topic 自动配套，显式传入优先
        if style is None:
            style = self.TOPIC_STYLE.get(topic, "TOPIC-STYLE-A-240223")
        raw = self._get(self.QUOTE_BASE, "/v2/quote/a/stock/ranking",
                        topic=topic, style=style, index=index, way=way,
                        reverse=reverse, deviation_num=deviation_num,
                        include_st=include_st, fields=fields)
        data = raw.get("data", {}) or {}
        items = []
        for s in data.get("items", []) or []:
            if not isinstance(s, dict):
                continue
            items.append({
                "code": s.get("secu_code"),
                "ori_code": s.get("ori_code"),
                "name": s.get("secu_name"),
                "last_px": s.get("last_px"),
                "change": s.get("change"),
                "change_px": s.get("change_px"),
                "qrr": s.get("qrr"),
                "tr": s.get("tr"),
                "business_balance": s.get("business_balance"),
                "open_px": s.get("open_px"),
                "high_px": s.get("high_px"),
                "low_px": s.get("low_px"),
                "preclose_px": s.get("preclose_px"),
                "time": s.get("time"),
                "reason": s.get("reason"),
                "plate": s.get("plate"),
                "limit_up_days": s.get("limit_up_days"),
                "is_st": s.get("is_st"),
                "trade_status": s.get("trade_status"),
            })
        return {
            "topic": topic,
            "way": way,
            "reverse": reverse,
            "total_num": data.get("total_num"),
            "index": data.get("index"),
            "items": items,
        }

    # ------------------------------------------------------------------
    # 6. 板块：列表 / 详情 / 涨停家数 / 涨停个股 / 新概念
    # ------------------------------------------------------------------
    def get_plate_list(self, plate_type: str = "concept", rever: int = 1) -> Dict[str, Any]:
        """板块列表 (quote /web_quote/plate/plate_list)

        点开资金热力图卡片、或看「板块排行」时用。
        返回板块涨跌幅 + 主力净流入 + 涨停家数 + 领涨股。
        """
        raw = self._get(self.QUOTE_BASE, "/web_quote/plate/plate_list",
                        type=plate_type, rever=rever)
        data = raw.get("data", {}) or {}
        plates = []
        for p in data.get("plate_data", []) or []:
            if not isinstance(p, dict):
                continue
            fs = p.get("first_stock") or {}
            change = p.get("change")
            plates.append({
                "code": p.get("secu_code"),
                "name": p.get("secu_name"),
                "change": change,
                "change_str": f"{change * 100:+.2f}%" if isinstance(change, (int, float)) else "",
                "main_fund_diff": p.get("main_fund_diff"),
                "limit_up": p.get("limit_up"),
                "limit_up_num": p.get("limit_up_num"),
                "limit_down_num": p.get("limit_down_num"),
                "first_stock": {
                    "code": fs.get("secu_code"),
                    "name": fs.get("secu_name"),
                    "last_px": fs.get("last_px"),
                    "change": fs.get("change"),
                } if fs else None,
            })
        fund = data.get("main_fund_diff") or {}
        return {
            "plates": plates,
            "is_all": data.get("is_all"),
            "top_main_fund": fund.get("top_main_fund_diff"),
            "last_main_fund": fund.get("last_main_fund_diff"),
        }

    def get_plate_info(self, plate_codes: List[str]) -> Dict[str, Any]:
        """板块详情 (quote /v2/quote/a/plate/info) —— 点开某个板块时用

        plate_codes 支持逗号分隔批量查询。
        """
        if not plate_codes:
            return {}
        raw = self._get(self.QUOTE_BASE, "/v2/quote/a/plate/info",
                        plate_codes=",".join(plate_codes))
        data = raw.get("data", {}) or {}
        out = {}
        for code, v in data.items():
            if not isinstance(v, dict):
                continue
            out[code] = {
                "code": v.get("plate_code") or code,
                "name": v.get("plate_name"),
                "change": v.get("change"),
                "change_px": v.get("change_px"),
                "main_money_inflow": v.get("main_money_inflow_1"),
                "main_money_inflow_str": v.get("main_money_inflow_1_str"),
            }
        return out

    def get_plate_up_num(self) -> List[Dict[str, Any]]:
        """板块涨停家数排行 (quote /v2/quote/a/plate/plate_up_num)"""
        raw = self._get(self.QUOTE_BASE, "/v2/quote/a/plate/plate_up_num")
        data = raw.get("data") or []
        return [
            {"code": p.get("plate_code"), "name": p.get("plate_name"),
             "up_num": p.get("up_num")}
            for p in data if isinstance(p, dict)
        ]

    def get_plate_up_stocks(self, plate_code: str) -> List[Dict[str, Any]]:
        """板块内涨停个股 (quote /v2/quote/a/plate/plate_up_stocks) —— 点开板块看成分股"""
        raw = self._get(self.QUOTE_BASE, "/v2/quote/a/plate/plate_up_stocks",
                        plate_code=plate_code)
        data = raw.get("data") or []
        return [
            {"code": s.get("secu_code"), "name": s.get("secu_name"),
             "change": s.get("change"), "change_px": s.get("change_px"),
             "trade_status": s.get("trade_status")}
            for s in data if isinstance(s, dict)
        ]

    def get_tuyere_stocks(self, plate_code: str) -> List[Dict[str, Any]]:
        """风口板块内个股 (quote /v2/quote/a/plate/tuyere/stocks) —— 点开风口看个股"""
        raw = self._get(self.QUOTE_BASE, "/v2/quote/a/plate/tuyere/stocks",
                        plate_code=plate_code)
        data = raw.get("data") or []
        return [
            {"code": s.get("secu_code"), "name": s.get("secu_name"),
             "last_px": s.get("last_px"), "change": s.get("change"),
             "change_px": s.get("change_px"),
             "limit_up_days": s.get("limit_up_days"),
             "trade_status": s.get("trade_status")}
            for s in data if isinstance(s, dict)
        ]

    def get_new_concept(self) -> List[Dict[str, Any]]:
        """新概念板块 (quote /v2/quote/a/plate/new_concept)"""
        raw = self._get(self.QUOTE_BASE, "/v2/quote/a/plate/new_concept")
        data = raw.get("data") or []
        return [p for p in data if isinstance(p, dict)]

    # ------------------------------------------------------------------
    # 7. 北交所（此前该 Tab 只有涨停股，没有全量排行）
    # ------------------------------------------------------------------
    def get_bj_stock_info(self, way: str = "change", rever: int = 1,
                          index: int = 1, deviation_num: int = 50,
                          market: str = "bj",
                          types: Optional[str] = None) -> Dict[str, Any]:
        """北交所 / 新三板个股排行 (quote /quote/xsb/bj_stock_info)

        注意：这与「北交所涨停股」不是一回事 —— 它返回**全量个股**按涨跌幅排序。
        之前盯盘「北交所」Tab 只筛涨停股，没涨停就显示空白，正是因为缺了这个接口。

        `market` 决定数据集（2026-10-17 实机验证）：
            market=bj   → 北京证券交易所，351 只
            market=all  → 新三板全部，6091 只
        真 App 的北交所页是「北京证券交易所 + 新三板」两块，就是同一个接口换 market。

        ⚠️ 这里的分页参数是 `rever`（不是 ranking 的 `reverse`），且 `types`
        要与 `way` 匹配：way=change 用 `change,last_px`，way=qrr 用 `qrr,last_px`，
        way=tr 用 `tr,last_px`，way=business_balance 用 `business_balance,last_px`。

        ⚠️ `index` 是**偏移量**不是页码：deviation_num=50 时翻页传 1 / 51 / 101。
        """
        if types is None:
            types = {
                "change": "change,last_px",
                "qrr": "qrr,last_px",
                "tr": "tr,last_px",
                "business_balance": "business_balance,last_px",
            }.get(way, "change,last_px")

        raw = self._get(self.QUOTE_BASE, "/quote/xsb/bj_stock_info",
                        market=market, way=way, rever=rever, index=index,
                        types=types, deviation_num=deviation_num)
        data = raw.get("data", {}) or {}
        items = []
        for s in data.get("items", []) or []:
            if not isinstance(s, dict):
                continue
            ch = s.get("change")
            items.append({
                "code": s.get("secu_code"),
                "ori_code": s.get("ori_code"),
                "name": s.get("secu_name"),
                "last_px": s.get("last_px"),
                "change": ch,
                "change_str": f"{ch * 100:+.2f}%" if isinstance(ch, (int, float)) else "",
                "qrr": s.get("qrr"),
                "tr": s.get("tr"),
                "business_balance": s.get("business_balance"),
                "trade_status": s.get("trade_status"),
            })
        return {
            "market": market,
            "market_name": self.BSE_MARKETS.get(market, market),
            "way": way,
            "total_num": data.get("total_num"),
            "items": items,
        }

    def get_bj_banner(self) -> List[Dict[str, Any]]:
        """北交所顶部横幅 (api3 /v3/depth/bjs/topbanner)"""
        raw = self._get(self.CONTENT_BASE, "/v3/depth/bjs/topbanner")
        data = raw.get("data", {}) or {}
        return [
            {"id": b.get("id"), "title": b.get("title"), "img": b.get("img"),
             "url": b.get("active_url")}
            for b in (data.get("banner") or []) if isinstance(b, dict)
        ]

    # ------------------------------------------------------------------
    # 8. 大盘页骨架 / 市场解读 / 异动与炸板 / 月度分布
    # ------------------------------------------------------------------
    def get_dingpan_config(self) -> List[Dict[str, Any]]:
        """大盘页模块配置 (api3 /v2/dingPan/config)

        ⚠️ 这个接口在 **api3** 上，不是 x-quote（接口地图里漏了域名）。
        返回 C1..C20 的模块开关与排序，是「大盘页到底该有哪些块」的权威依据。
        """
        raw = self._get(self.CONTENT_BASE, "/v2/dingPan/config")
        cfg = raw.get("common_config") or []
        modules = []
        for m in cfg:
            if not isinstance(m, dict):
                continue
            modules.append({
                "num": m.get("num"),
                "name": m.get("module_name"),
                "seq": m.get("default_sequence"),
                "hidden": bool(m.get("is_hide")),
                "valid": bool(m.get("is_valid")),
            })
        return modules

    def get_market_commentary(self) -> Dict[str, Any]:
        """大盘页顶部「市场解读」(api3 /api/subject/dapan/new/article)"""
        raw = self._get(self.CONTENT_BASE, "/api/subject/dapan/new/article")
        data = raw.get("data", {}) or {}
        return {
            "title": data.get("article_title", ""),
            "jump": data.get("jump", ""),
        }

    def get_transaction_and_blasting(self) -> Dict[str, Any]:
        """异动与炸板 (api3 /v3/transaction_and_blasting) —— 涨停宝页核心

        ⚠️ **必须零业务参数调用**。实测加 vip / CTime 任一参数都会返回
        `10012 签名错误`（签名覆盖的参数集是固定的，多传即校验失败）。
        """
        raw = self._get(self.CONTENT_BASE, "/v3/transaction_and_blasting")
        data = raw.get("data", {}) or {}

        fuses = []
        for f in data.get("blasting_fuses", []) or []:
            if not isinstance(f, dict):
                continue
            fuses.append({
                "target": f.get("stock"),
                "schema": f.get("schema"),
                "desc": f.get("description"),
                "comment": f.get("comment"),
                "direction": f.get("float"),
                "article_id": f.get("article_id"),
            })

        articles = []
        for a in data.get("transaction_articles", []) or []:
            if not isinstance(a, dict):
                continue
            articles.append({
                "id": a.get("article_id"),
                "title": a.get("title"),
                "brief": a.get("brief"),
                "time": a.get("ctime"),
                "img": a.get("img"),
                "plate_name": a.get("plate_name"),
                "stock_list": [
                    {"code": s.get("StockID") or s.get("code"),
                     "name": s.get("name") or s.get("Name")}
                    for s in (a.get("stock_list") or []) if isinstance(s, dict)
                ],
            })

        return {"blasting_fuses": fuses, "articles": articles}

    def get_month_distribution(self, date: str = "") -> Dict[str, Any]:
        """月度涨跌分布 (quote /quote/golden/distribution_compose)

        date 必须是 `YYYYMM`（如 202610）；传 `YYYYMMDD` 或空都会返回 9000。
        """
        if not date:
            date = datetime.now().strftime("%Y%m")
        raw = self._get(self.QUOTE_BASE, "/quote/golden/distribution_compose", date=date)
        data = raw.get("data") or {}
        if not isinstance(data, dict):
            return {}
        return {
            "date": data.get("date"),
            "stock_sum": data.get("stock_sum"),
            "ksh_sum": data.get("ksh_sum"),
            "esa_sum": data.get("esa_sum"),
            "hk_sum": data.get("hk_sum"),
            "month_return": data.get("month_er"),
            "shsz300_change": data.get("shsz300_change"),
        }

    def get_five_month(self) -> List[Dict[str, Any]]:
        """近五个月列表 (quote /quote/golden/five_month) —— 月度分布切月份用"""
        raw = self._get(self.QUOTE_BASE, "/quote/golden/five_month")
        data = raw.get("data") or []
        return [
            {"year": m.get("year"), "month": m.get("month"), "date": m.get("date_num")}
            for m in data if isinstance(m, dict)
        ]

    def get_roll_info(self, roll_id) -> Dict[str, Any]:
        """快讯详情 (api3 /v1/roll/info) —— 点开某条电报看全文"""
        if not roll_id:
            return {}
        raw = self._get(self.CONTENT_BASE, "/v1/roll/info", id=roll_id)
        data = raw.get("data", {}) or {}
        if not isinstance(data, dict):
            return {}
        return {
            "id": data.get("id") or roll_id,
            "title": data.get("title", ""),
            "content": data.get("content") or data.get("brief") or "",
            "time": data.get("ctime"),
            "reading_num": data.get("reading_num"),
            "share_url": data.get("share_url", ""),
            "stock_list": [
                {"code": s.get("StockID") or s.get("code"),
                 "name": s.get("name") or s.get("Name")}
                for s in (data.get("stock_list") or []) if isinstance(s, dict)
            ],
            "plate_list": [
                {"code": p.get("code"), "name": p.get("name")}
                for p in (data.get("plate_list") or []) if isinstance(p, dict)
            ],
        }

    # ------------------------------------------------------------------
    # 9. 2026-10-17 模拟器实机抓包新增：板块详情 / 分时 / 涨停原因 /
    #    交易日 / 涨停宝工具 / 券商观点 / 指数基础 / 风口追踪 / 自选
    #
    #    这一批都是「点开某个卡片之后才发的请求」，正是之前「很多数据点不开」
    #    的根因 —— 下钻数据必须在这里取到，再塞进 bundle 给前端。
    # ------------------------------------------------------------------
    def get_transaction_anchor(self, cdate: str = "") -> List[Dict[str, Any]]:
        """板块异动锚点 (api3 /v3/transaction/anchor) —— 直播页分时图上的板块标注

        `cdate` 格式 `YYYY-MM-DD`。**必须传一个交易日**：实测传周末/未来日期
        返回空数组，不传也返回空。所以默认回落到「最近交易日」。

        每条 = 某时刻某板块异动 + 关联文章 id，前端按 c_time 定位到分时图横坐标。
        """
        if not cdate:
            cdate = self.get_closest_trading_day() or datetime.now().strftime("%Y-%m-%d")
            if isinstance(cdate, list):          # 个别情况下接口返回列表
                cdate = cdate[0] if cdate else ""
        if not cdate:
            return []
        raw = self._get(self.CONTENT_BASE, "/v3/transaction/anchor", cdate=cdate)
        data = raw.get("data") or []
        if not isinstance(data, list):
            return []
        out = []
        for a in data:
            if not isinstance(a, dict):
                continue
            out.append({
                "code": a.get("symbol_code"),
                "name": a.get("symbol_name"),
                "time": a.get("c_time"),
                "float": a.get("float"),          # up / down
                "article_id": a.get("article_id"),
                "schema": a.get("schema"),
            })
        return out

    def get_plate_stocks(self, plate_code: str, way: str = "change",
                         reverse: int = 1, only_core: int = 0) -> List[Dict[str, Any]]:
        """板块成分股 (quote /v2/quote/a/plate/stocks) —— 点开板块卡后的明细

        `only_core=0` 全部 / `1` 只要核心股（真 App 用 0）。

        ⚠️ 返回结构是 `data.stocks`（不是 data.items），且字段名与其他接口不同：
        代码叫 `stock_code`（带 `.BJ`/`.SH` 后缀）、名称叫 `stock_name`、
        业务说明叫 `assoc_desc`；`last_px` / `change` 都是**字符串**。
        """
        if not plate_code:
            return []
        raw = self._get(self.QUOTE_BASE, "/v2/quote/a/plate/stocks",
                        plate_code=plate_code, way=way, reverse=reverse,
                        only_core=only_core)
        data = raw.get("data") or {}
        stocks = (data.get("stocks") if isinstance(data, dict) else data) or []
        out = []
        for s in stocks:
            if not isinstance(s, dict):
                continue
            out.append({
                "code": s.get("stock_code"),
                "name": s.get("stock_name"),
                "last_px": s.get("last_px"),
                "change": s.get("change"),
                "assoc_desc": s.get("assoc_desc"),
                "is_core": s.get("is_core"),
                "limit_up_days": s.get("limit_up_days"),
            })
        return out

    def get_plate_popular_stocks(self, plate_code: str) -> List[Dict[str, Any]]:
        """板块人气股 (quote /v2/quote/a/plate/popular_stocks)"""
        if not plate_code:
            return []
        raw = self._get(self.QUOTE_BASE, "/v2/quote/a/plate/popular_stocks",
                        plate_code=plate_code)
        data = raw.get("data") or []
        if isinstance(data, dict):
            data = data.get("items") or []
        return [s for s in data if isinstance(s, dict)]

    def get_plate_detail(self, secu_code: str) -> Dict[str, Any]:
        """板块详情 (quote /web_quote/plate/info) —— 板块页头部数据"""
        if not secu_code:
            return {}
        raw = self._get(self.QUOTE_BASE, "/web_quote/plate/info",
                        secu_code=secu_code, user_id="")
        data = raw.get("data") or {}
        return data if isinstance(data, dict) else {}

    def get_stock_tline(self, secu_code: str, days: int = 1) -> List[Dict[str, Any]]:
        """分时线 (quote /v2/quote/a/stock/tline 或 tline_5d)

        `secu_code` 传**板块代码**（cls80457）能拿到点；传指数代码（sh000001）
        实测返回 `line: []`（指数的分时走另一条链路）。
        返回结构 `data.line[] = {date, minute, last_px, av_px, preclose_px}`。
        """
        if not secu_code:
            return []
        path = "/v2/quote/a/stock/tline" if days == 1 else "/v2/quote/a/stock/tline_5d"
        raw = self._get(self.QUOTE_BASE, path, secu_code=secu_code)
        data = raw.get("data") or {}
        line = (data.get("line") if isinstance(data, dict) else data) or []
        return [p for p in line if isinstance(p, dict)]

    def get_stock_up_reason(self, secu_codes: List[str]) -> Dict[str, str]:
        """涨停/大涨原因 (quote /quote/stock/up_reason)

        批量传代码（逗号分隔），返回 `{代码: "板块|业务说明"}`。
        真 App「大涨股」每只票下面那段业务说明就是这个字段。
        """
        codes = [c for c in (secu_codes or []) if c]
        if not codes:
            return {}
        raw = self._get(self.QUOTE_BASE, "/quote/stock/up_reason",
                        secu_codes=",".join(codes))
        data = raw.get("data") or {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, str)}

    def get_trading_days(self, start_date: str, end_date: str) -> List[str]:
        """区间内的交易日列表 (quote /v2/quote/a/stock/range_trading_days)

        日期格式 `YYYY-MM-DD`。用于日期选择器（大涨股页顶部）。
        """
        raw = self._get(self.QUOTE_BASE, "/v2/quote/a/stock/range_trading_days",
                        start_date=start_date, end_date=end_date)
        data = raw.get("data") or []
        return [d for d in data if isinstance(d, str)]

    def get_closest_trading_day(self) -> str:
        """最近交易日 (quote /quote/stock/closest_trading_day)

        ⚠️ 实测返回的是**列表**（如 `['2026-10-16','2026-10-15']`，降序），
        不是单个日期 —— 早期版本直接 `str(data)` 会得到 "['2026-10-16', ...]"
        这种字符串，传给别的接口必然拿不到数据。这里统一取第一个。
        """
        raw = self._get(self.QUOTE_BASE, "/quote/stock/closest_trading_day")
        data = raw.get("data")
        if isinstance(data, list):
            for d in data:
                if isinstance(d, str) and d.strip():
                    return d.strip()
                if isinstance(d, dict):
                    v = d.get("date") or d.get("trading_day")
                    if v:
                        return str(v)
            return ""
        if isinstance(data, dict):
            return str(data.get("date") or data.get("trading_day") or "")
        return str(data or "").strip()

    def get_limit_up_tool(self) -> Dict[str, Any]:
        """涨停宝工具位 (api3 /api/limit_up_tool) —— 涨停宝页的工具入口

        返回 invest_pro / plate_forum / vip_article 三块。
        """
        raw = self._get(self.CONTENT_BASE, "/api/limit_up_tool")
        data = raw.get("data") or {}
        return data if isinstance(data, dict) else {}

    def get_qs_info_all(self, date: str = "") -> List[Dict[str, Any]]:
        """券商月度观点 (quote /quote/golden/qs_info_all)

        `date` 必须是 `YYYYMM`（与 distribution_compose 同规则）。
        每条 = 券商名 + 月度收益 + 观点正文 + 推荐个股。
        """
        if not date:
            date = datetime.now().strftime("%Y%m")
        raw = self._get(self.QUOTE_BASE, "/quote/golden/qs_info_all", date=date)
        data = raw.get("data") or []
        out = []
        for q in data:
            if not isinstance(q, dict):
                continue
            out.append({
                "index": q.get("index"),
                "qs_id": q.get("qs_id"),
                "qs_name": q.get("qs_name"),
                "qs_mer": q.get("qs_mer"),
                "content": q.get("content"),
                "stocks": q.get("stocks") or [],
            })
        return out

    def get_index_basic(self, secu_code: str,
                        fields: str = "change,trade_status,status") -> Dict[str, Any]:
        """指数基础行情 (quote /quote/index/basic)

        只要涨跌幅和交易状态，比 index/home 轻，用于轮询刷新。
        """
        if not secu_code:
            return {}
        raw = self._get(self.QUOTE_BASE, "/quote/index/basic",
                        secu_code=secu_code, fields=fields)
        data = raw.get("data") or {}
        return data if isinstance(data, dict) else {}

    def get_subject_articles(self, subject_id: int | str,
                             last_article_time: str = "",
                             article_id: int | str = 0,
                             limit: int = 20) -> List[Dict[str, Any]]:
        """专题文章列表 (api3 /api/subject/{id}/article) —— 风口页「风口追踪」

        实测风口专题 subject_id = 1485，分页靠 `last_article_time` 游标。

        ❌ **当前不可用**：真 App 对这个端点用的是另一套 sign（path 里带 id，
        签名覆盖了路径参数），我们手上的固定 sign 一律返回 `10012 签名错误`。
        已试过省略 `articleId` / `last_article_time`、只传 `subject_id` 等组合，全部失败。
        → 保持返回空数组，由上层标记 unavailable，**不要用假数据填充**。
        要补这一块只能重新抓一次该请求的完整 sign 再逆向签名算法。
        """
        if not subject_id:
            return []
        raw = self._get(self.CONTENT_BASE, f"/api/subject/{subject_id}/article",
                        subject_id=subject_id,
                        last_article_time=last_article_time,
                        articleId=article_id)
        data = raw.get("data") or []
        if not isinstance(data, list):
            return []
        out = []
        for a in data[:limit]:
            if not isinstance(a, dict):
                continue
            out.append({
                "id": a.get("article_id"),
                "title": a.get("article_title"),
                "time": a.get("article_time") or a.get("ctime"),
                "brief": a.get("article_brief") or a.get("brief"),
            })
        return out

    def get_optional_catalog(self) -> List[Dict[str, Any]]:
        """自选分组 (api3 /api/optional/catalog)"""
        raw = self._get(self.CONTENT_BASE, "/api/optional/catalog")
        data = raw.get("data") or []
        return [c for c in data if isinstance(c, dict)]

    def get_optional_stock(self, catalog_id: int | str = 0) -> List[Dict[str, Any]]:
        """自选股列表 (api3 /api/optional/stock)"""
        raw = self._get(self.CONTENT_BASE, "/api/optional/stock",
                        CatalogId=str(catalog_id))
        data = raw.get("data") or {}
        if isinstance(data, dict):
            data = data.get("stocks") or []
        return [s for s in data if isinstance(s, dict)]

    def get_top_assembled(self) -> Dict[str, Any]:
        """首页聚合 (api3 /v5/top/assembled)

        含 banner / hot_subject / media_article / roll_bar / tools（工具位）。
        `tools` 里能看到盯盘、今日风口、异动雷达等入口的开关状态。
        """
        raw = self._get(self.CONTENT_BASE, "/v5/top/assembled")
        data = raw.get("data") or {}
        return data if isinstance(data, dict) else {}
