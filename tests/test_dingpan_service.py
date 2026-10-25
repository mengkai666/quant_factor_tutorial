# -*- coding: utf-8 -*-
"""盯盘聚合器 (dingpan_service) 与第三数据源 (em_dingpan_client) 的行为测试。

**为什么要有这个文件**

盯盘页的价值全在「第一源挂了还能不能出数」这条路径上, 而这条路径**平时跑不到**
(财联社和开盘啦都正常时它永远不执行)。不测就等于没写。

本文件全部用假客户端注入, **零网络**。可用 `QF_BLOCK_NETWORK=1` 验证:
    QF_BLOCK_NETWORK=1 pytest tests/test_dingpan_service.py -q
"""
from __future__ import annotations

import pytest

from data_sources.cls_client import CLSClient
from data_sources.em_dingpan_client import EastmoneyDingpanClient
from dingpan_service import (
    DINGPAN_TABS, FUND_HEAT_SORT_MODES, FUND_HEAT_TIME_WINDOWS,
    SOURCE_CLS, SOURCE_EM, SOURCE_KPL, DingpanAggregator,
)


# ---------------------------------------------------------------------------
# 假客户端
# ---------------------------------------------------------------------------
class FakeCLS:
    """健康的第一源。"""

    QUOTE_BASE = "https://x-quote.cls.cn"
    FUND_HEAT_MODES = CLSClient.FUND_HEAT_MODES
    FUND_HEAT_WINDOWS = CLSClient.FUND_HEAT_WINDOWS

    def __init__(self, *, degree=54, fund_heat=True):
        self.degree = degree
        self._fund_heat = fund_heat

    def get_market_emotion(self):
        return {
            "market_degree": self.degree,
            "turnover_str": "1.56万亿",
            "turnover_change_str": "-776亿",
            "preview_energy_str": "1.56万亿",
            "preview_change_str": "-776亿",
            "up_ratio_str": "55.00%",
            "sealed_count": 61,
            "touched_count": 49,
            "yesterday_perf_str": "-1.74%",
            "open_high_ratio_str": "56%",
            "profit_ratio_str": "33%",
            "up_count": 3301, "down_count": 2128, "flat_count": 138,
            "limit_up_count": 61, "limit_down_count": 11, "suspend_count": 10,
            "distribution_bins": {},
            "distribution_series": [
                {"key": "up_num", "label": "涨停", "value": 61},
                {"key": "up_2", "label": "0%", "value": 2201},
                {"key": "down_num", "label": "跌停", "value": 11},
            ],
            "limit_board": {"tiers": ["一板", "二板", "三板", "高度板"],
                            "counts": ["54", "4", "1", "2"],
                            "rates": ["连板率", "13%", "50%", "50%"]},
        }

    def get_fund_heat(self, num=10, rever=0, way="money_inflow", days=1):
        if not self._fund_heat:
            return []
        return [{"plate_code": "cls1", "plate_name": f"{way}-{rever}-{days}",
                 "change": "+2.49%", "change_num": 0.0249,
                 "inflow_str": "+36.9亿元", "inflow_num": 3.69e9}]

    def get_roll_list(self):
        return [{"id": 1, "title": "电报标题", "content": "正文", "time": 1792113900}]

    def get_today_tuyere(self):
        return [
            {"id": 14, "title": "军工", "plate_code": "cls80222",
             "subject_id": 1485, "interpret": "航展将于12月举办"},
            {"id": 13, "title": "低空经济", "plate_code": "cls82437",
             "subject_id": 11379, "interpret": "特斯拉Roadster亮相"},
        ]

    def get_mainline(self):
        return {
            "chance_desc": "判断主线机会，洞悉趋势强度",
            "mainLine_desc": "精准把控主力动向，掌握主线机会",
            "wind_desc": "紧跟每日风口，领悟市场风向",
            "style_desc": "预判风格走向，投资无往不利",
            "mainlines": [
                {"id": 4, "hot": 4, "continued": 4, "title": "影视传媒",
                 "desc": "短剧",
                 "plates": [
                     {"plate_code": "cls80155", "plate_name": "文化传媒",
                      "reason": "短剧出海", "rise_count": 5,
                      "rise_range": -0.0177,
                      "faucets": [
                          {"name": "慈文传媒", "code": "sz002343",
                           "reason": "6天涨超60%", "rise_range": 4.71},
                          {"name": "读者传媒", "code": "sh603999",
                           "reason": "2连板", "rise_range": 9.97},
                          {"name": "新华文轩", "code": "sh601811",
                           "reason": "17天涨超130%", "rise_range": 10.01},
                      ],
                      "article": {"id": 2506380, "title": "外部回暖叠加回购",
                                  "read_count": "921889"}},
                 ]},
            ],
            "styles": {
                "id": 2499598, "title": "A股风格重构深化",
                "interpret": "10月A股进入业绩验证期",
                "index_code": "sh000001", "index_name": "上证指数",
                "index_change30": "-6.07%",
                "stacks": [
                    {"code": "sh600825", "name": "新华传媒",
                     "last_px": "15.16", "pre30_close": "5.06",
                     "rise_range": "+199.60%"},
                ],
            },
            "winds": [
                {"id": 14, "title": "军工", "plate_code": "cls80222",
                 "subject_id": 1485, "hot": 4, "plate_change": 0.0248,
                 "interpret": "航展将于12月举办",
                 "faucet_stocks": [
                     {"code": "sz300581", "name": "晨曦航空", "rise_range": 20.02},
                 ]},
            ],
        }

    # --- 大盘页 C1 / C14 与下钻明细（新增） ---
    def get_index_home(self):
        return {"quotes": [
            {"code": "sh000001", "name": "上证指数", "last_px": 3716.284,
             "change": 0.0024, "change_str": "+0.24%", "change_px": 8.81,
             "up_num": 1301, "down_num": 835, "flat_num": 53},
            {"code": "sz399001", "name": "深证成指", "last_px": 11802.459,
             "change": -0.0012, "change_str": "-0.12%", "change_px": -14.2,
             "up_num": 268, "down_num": 218, "flat_num": 12},
        ], "up_down_dis": {}}

    def get_ranking_topics(self):
        return [{"name": "全部A股", "topic": "all", "style": "TOPIC-STYLE-A-240223"},
                {"name": "连板池", "topic": "continuous_up", "style": "TOPIC-STYLE-B-240223"},
                {"name": "炸板池", "topic": "up_open", "style": "TOPIC-STYLE-B-240223"},
                {"name": "跌停池", "topic": "down", "style": "TOPIC-STYLE-B-240223"}]

    def get_stock_ranking(self, topic="all", style=None, index=1, way="change",
                          reverse=1, deviation_num=20, include_st=1, fields=None):
        # style 未传时按 topic 自动配套（与真客户端一致，否则池主题会拿到 A style）
        if style is None:
            style = ("TOPIC-STYLE-B-240223" if topic in
                     ("continuous_up", "up_open", "down")
                     else "TOPIC-STYLE-A-240223")
        base = [
            {"code": "sh600825", "name": "新华传媒", "last_px": "15.16",
             "change": "+10.01%", "change_px": "1.38"},
        ]
        if topic == "all":
            base = [
                {"code": "sh600825", "name": "新华传媒", "last_px": "15.16",
                 "change": "+10.01%", "change_px": "1.38"},
                {"code": "sz000530", "name": "冰山冷热", "last_px": "9.80",
                 "change": "+10.00%", "change_px": "0.89"},
            ]
        return {"topic": topic, "way": way, "reverse": reverse,
                "total_num": 3, "items": base}

    def get_plate_list(self, plate_type="concept", rever=1):
        return {"plates": [{"code": "cls80457", "name": "芯片产业链",
                            "change": -0.0006, "change_str": "-0.06%",
                            "main_fund_diff": 4.13e9, "limit_up_num": 5,
                            "first_stock": {"code": "sh600825", "name": "新华传媒",
                                            "last_px": "15.16", "change": "+10.01%"}}],
                "is_all": "0"}

    def get_plate_up_num(self):
        return [{"code": "cls80218", "name": "国企改革", "up_num": 22}]

    def get_plate_info(self, plate_codes):
        return {c: {"code": c, "name": "芯片产业链", "change": "-0.06%",
                    "main_money_inflow_str": "41.3亿"} for c in plate_codes}

    def get_plate_up_stocks(self, plate_code):
        return [{"code": "sh688295", "name": "中复神鹰", "change": "+11.31%",
                 "change_px": 3.65, "trade_status": "ENDTR"}]

    # ---- 2026-10-17 新增接口的替身 ----
    def get_plate_stocks(self, plate_code, way="change", reverse=1, only_core=0):
        return [{"code": "sh688295", "name": "中复神鹰", "last_px": "32.39",
                 "change": "+11.31%", "assoc_desc": "碳纤维|新材料企业"}]

    def get_plate_popular_stocks(self, plate_code):
        return [{"stock_code": "sh688295", "stock_name": "中复神鹰"}]

    def get_plate_detail(self, secu_code):
        return {"plate_name": "芯片产业链", "change": "-0.06%"}

    def get_stock_tline(self, secu_code, days=1):
        return [{"minute": 930, "last_px": 2658.72, "preclose_px": 2607.76}]

    def get_stock_up_reason(self, secu_codes):
        # 真接口的键是前缀式（sz002085），服务层负责映射回榜代码
        return {c: "低空经济|公司主营低空飞行器" for c in (secu_codes or [])}

    def get_transaction_anchor(self, cdate=""):
        return [{"code": "cls82446", "name": "短剧", "time": "2026-10-16 09:31:09",
                 "float": "up", "article_id": 2506408}]

    def get_closest_trading_day(self):
        return "2026-10-16"

    def get_trading_days(self, start_date, end_date):
        return ["2026-10-16", "2026-10-15", "2026-10-14"]

    def get_index_basic(self, secu_code, fields=""):
        return {"change": 0.0024, "trade_status": "ENDTR", "status": 0}

    def get_qs_info_all(self, date=""):
        return [{"qs_name": "招商证券", "qs_mer": -2.38, "content": "展望10月…"}]

    def get_optional_stock(self, catalog_id=0):
        return []

    def get_limit_up_tool(self):
        return {"invest_pro": [], "plate_forum": [], "vip_article": []}

    def get_tuyere_stocks(self, plate_code):
        return [{"code": "sh603618", "name": "杭电股份", "last_px": "32.39",
                 "change": "+7.47%", "limit_up_days": 0}]

    def get_index_top3(self, secu_code):
        return {"code": secu_code, "name": "上证指数", "top3": [
            {"code": "sh600825", "name": "新华传媒", "last_px": "15.16",
             "change_px": 1.38, "change_30": "+199.60%"},
        ]}

    def get_bj_stock_info(self, **kw):
        return {"total_num": 351, "items": [
            {"code": "920196.BJ", "ori_code": "920196", "name": "南方乳业",
             "last_px": 22.56, "change": 0.5876, "change_str": "+58.76%"},
            {"code": "920117.BJ", "ori_code": "920117", "name": "国航远洋",
             "last_px": 8.1, "change": 0.05, "change_str": "+5.00%"},
        ]}

    def get_market_commentary(self):
        return {"title": "指数短期震荡承压", "jump": "cailianshe://article?id=1"}

    def get_transaction_and_blasting(self):
        return {"blasting_fuses": [{"target": "sh603259", "desc": "药明康德跳水",
                                    "comment": "CRO 概念回落", "direction": "down"}],
                "articles": [{"id": 2507205, "title": "两市成交额突破1.5万亿",
                              "brief": "放量近1700亿", "time": 1792113900}]}


class FakeKPL:
    """健康的第二源。"""

    def __init__(self, *, tianti=True):
        self._tianti = tianti

    def get_market_stats(self):
        return {"up_count": 3301, "down_count": 2127, "limit_up_count": 61,
                "limit_down_count": 10, "broken_ratio": 44.55,
                "yesterday_zt_perf": -1.638}

    def get_zhangting_tianti(self):
        if not self._tianti:
            return {}
        return {"stocks": [
            {"code": "600825", "symbol": "sh600825", "name": "新华传媒", "height": 11,
             "plate_name": "并购重组", "first_limit_time": 1792113957,
             "float_market_val": 15840499806},
            {"code": "603197", "symbol": "sh603197", "name": "保隆科技", "height": 1,
             "plate_name": "低空经济", "first_limit_time": 1792114221,
             "float_market_val": 6290213178},
            {"code": "920117", "symbol": "bj920117", "name": "国航远洋", "height": 2,
             "plate_name": "航运", "first_limit_time": 1792114000,
             "float_market_val": 1e9},
        ]}

    def get_plate_resumption(self, st=100, index=0):
        return {"plates": [{"plate_code": "801829", "plate_name": "低空经济",
                            "stocks": [{"name": "保隆科技", "symbol": "sh603197",
                                        "reason_text": "飞行汽车", "height_desc": "首板",
                                        "change_pct": 9.99}]}]}


class FakeEM:
    """健康的第三源 (备用)。"""

    def __init__(self, *, payload=None):
        self._payload = payload

    def get_dingpan_fallback(self, date):
        if self._payload is not None:
            return self._payload
        return {
            "limit_up_count": 61, "limit_down_count": 10, "broken_count": 49,
            "seal_rate_str": "55.45%", "sealed_count": 61, "touched_count": 110,
            "limit_board": {"tiers": ["一板", "二板", "三板", "高度板"],
                            "counts": ["54", "4", "1", "2"],
                            "rates": ["连板率", "13%", "50%", "50%"]},
            "zt_pool": [{"code": "600825", "name": "新华传媒", "limit_count": 11,
                         "plate_name": "出版", "first_limit_time": "09:25:00",
                         "float_market_cap": 1.58e10}],
        }


class DeadClient:
    """所有方法都抛异常 —— 模拟该源整体不可用。"""

    FUND_HEAT_MODES = CLSClient.FUND_HEAT_MODES
    FUND_HEAT_WINDOWS = CLSClient.FUND_HEAT_WINDOWS

    def __getattr__(self, name):
        def _boom(*_a, **_kw):
            raise ConnectionError(f"{name} 不可用")
        return _boom


def build(cls=None, kpl=None, em=None):
    return DingpanAggregator(
        cls_client=cls if cls is not None else FakeCLS(),
        kpl_client=kpl if kpl is not None else FakeKPL(),
        em_client=em if em is not None else FakeEM(),
    )


# ---------------------------------------------------------------------------
# 前端契约
# ---------------------------------------------------------------------------
def test_tab_bar_matches_cailianshe_order():
    """Tab 栏顺序必须与财联社 App 一致, 顺序错了页面就不叫 1:1 复刻。"""
    dp = build().build(date="2026-10-17")
    assert [t["label"] for t in dp["tab_bar"]] == [
        "自选", "大盘", "北交所", "直播", "风口", "主线", "大涨股", "涨停宝"
    ]
    assert dp["tab_bar"] == DINGPAN_TABS
    assert dp["active_tab"] == "market"


def test_payload_has_every_key_frontend_reads():
    """前端读的键一个都不能少 —— 少一个就是运行期 TypeError。"""
    dp = build().build(date="2026-10-17")
    for key in ("tab_bar", "active_tab", "market", "watchlist", "bse", "live",
                "tuyere", "mainline", "big_rise", "zt_bao", "sources",
                "unavailable", "degraded", "generated_at"):
        assert key in dp, f"缺少前端依赖的键: {key}"
    for key in ("overview", "heat", "limit_performance", "fund_heat"):
        assert key in dp["market"], f"market 缺少 {key}"


def test_fund_heat_matrix_covers_all_sort_and_window_combos():
    """12 组矩阵必须齐 —— 胶囊切换是纯前端行为, 缺一组就会白屏。"""
    dp = build().build(date="2026-10-17")
    matrix = dp["market"]["fund_heat"]["matrix"]
    assert set(matrix) == set(FUND_HEAT_SORT_MODES)
    for mode in FUND_HEAT_SORT_MODES:
        assert set(matrix[mode]) == set(FUND_HEAT_TIME_WINDOWS)
        for window in FUND_HEAT_TIME_WINDOWS:
            assert matrix[mode][window], f"{mode}/{window} 为空"


# ---------------------------------------------------------------------------
# 健康路径
# ---------------------------------------------------------------------------
def test_all_sources_healthy_uses_cls_and_does_not_degrade():
    dp = build().build(date="2026-10-17")
    assert dp["degraded"] is False
    assert dp["unavailable"] == []
    assert dp["sources"]["market_overview"] == SOURCE_CLS
    assert dp["sources"]["limit_performance"] == SOURCE_CLS
    assert dp["sources"]["fund_heat"] == SOURCE_CLS
    assert dp["market"]["heat"]["degree"] == 54
    assert dp["market"]["overview"]["series"]


def test_kpl_is_attached_as_independent_crosscheck():
    """第二源不抢主口径, 但必须留下可对账的独立数字。"""
    ov = build().build(date="2026-10-17")["market"]["overview"]
    assert ov["crosscheck"]["source"] == SOURCE_KPL
    assert ov["crosscheck"]["broken_ratio"] == 44.55
    lp = build().build(date="2026-10-17")["market"]["limit_performance"]
    assert lp["crosscheck"]["yesterday_zt_perf"] == -1.638


# ---------------------------------------------------------------------------
# 降级路径 —— 本文件存在的理由
# ---------------------------------------------------------------------------
def test_cls_dead_falls_back_to_em_and_marks_unavailable():
    """第一源整体挂掉: 备用源接管能接的部分, 接不了的显式标 unavailable。"""
    dp = build(cls=DeadClient()).build(date="2026-10-17")

    assert dp["degraded"] is True
    assert dp["sources"]["limit_performance"] == SOURCE_EM
    # 备用源给不出这三项
    for gap in ("limit_performance.yesterday_perf",
                "limit_performance.open_high_rate",
                "limit_performance.profit_rate"):
        assert gap in dp["unavailable"], f"{gap} 应被标记为不可用"
    # 能给的部分必须有值
    lp = dp["market"]["limit_performance"]
    assert lp["seal_rate"] == "55.45%"
    assert lp["sealed_count"] == 61
    assert lp["ladder"]["counts"] == ["54", "4", "1", "2"]
    assert "note" in lp, "降级块必须带说明, 否则前端没法告诉用户缺了什么"


def test_cls_dead_em_dead_reports_blocks_unavailable_without_crashing():
    """前两源都挂: 不能抛异常, 要把缺口如实报出来, 且不能认领备用源。"""
    dp = build(cls=DeadClient(), em=DeadClient()).build(date="2026-10-17")

    assert dp["degraded"] is False          # 备用源也没取到数, 不能算降级成功
    assert dp["sources"]["market_overview"] == ""
    assert dp["sources"]["limit_performance"] == ""
    assert "market_overview" in dp["unavailable"]
    assert "market_overview.distribution" in dp["unavailable"]
    assert "limit_performance" in dp["unavailable"]
    assert "fund_heat" in dp["unavailable"]
    assert "market_heat" in dp["unavailable"]
    # 无数据时必须显式 available=False, 让前端渲染占位而不是显示空值
    assert dp["market"]["overview"]["available"] is False
    assert dp["market"]["limit_performance"]["available"] is False
    assert dp["market"]["fund_heat"]["available"] is False
    assert dp["market"]["heat"]["available"] is False


def test_all_three_sources_dead_still_returns_renderable_payload():
    """三源全挂也不能炸 —— 这是页面能否优雅降级的底线。"""
    dp = build(cls=DeadClient(), kpl=DeadClient(), em=DeadClient()).build(date="2026-10-17")

    assert [t["label"] for t in dp["tab_bar"]] == [t["label"] for t in DINGPAN_TABS]
    assert dp["degraded"] is False
    assert dp["market"]["overview"]["available"] is False
    assert dp["market"]["overview"]["limit_up_count"] is None
    assert dp["market"]["limit_performance"]["seal_rate"] == ""
    assert dp["market"]["fund_heat"]["available"] is False
    for key in ("live", "tuyere", "big_rise", "zt_bao", "bse"):
        assert key in dp["unavailable"], f"{key} 应被标记为不可用"
    assert not dp["mainline"]["mainlines"]


def test_single_failing_block_does_not_poison_the_others():
    """只有资金热力图挂掉时, 其余块必须照常。"""
    dp = build(cls=FakeCLS(fund_heat=False)).build(date="2026-10-17")

    assert "fund_heat" in dp["unavailable"]
    assert dp["sources"]["fund_heat"] == ""
    assert dp["sources"]["market_overview"] == SOURCE_CLS
    assert dp["sources"]["limit_performance"] == SOURCE_CLS
    assert dp["market"]["heat"]["degree"] == 54
    assert dp["market"]["limit_performance"]["seal_rate"] == "55.00%"


def test_mainline_falls_back_to_kpl_when_cls_has_none():
    """财联社主线为空时, 用开盘啦板块涨停原因兜底, 并标明换源了。"""
    class NoMainlineCLS(FakeCLS):
        def get_mainline(self):
            return {"desc": "", "mainlines": []}

    dp = build(cls=NoMainlineCLS()).build(date="2026-10-17")
    assert dp["sources"]["mainline"] == SOURCE_KPL
    assert dp["mainline"]["mainlines"][0]["title"] == "低空经济"


def test_ladder_stocks_split_into_big_rise_and_zt_bao():
    """涨停池要按连板高度正确分桶。"""
    dp = build().build(date="2026-10-17")
    assert dp["sources"]["zt_bao"] == SOURCE_KPL
    # zt_bao 全量且按高度降序
    assert [s["limit_count"] for s in dp["zt_bao"]] == [11, 2, 1]
    # big_rise 只收首板
    assert {s["name"] for s in dp["big_rise"]} == {"保隆科技"}


def test_bse_tab_is_full_ranking_not_just_limit_up():
    """北交所 Tab 必须是**全量个股排行**, 而不是只筛涨停股。

    旧实现只取「北交所涨停股」, 当天没有北交所涨停就整页空白 —— 用户会以为
    是数据坏了。真实 App 这里是 300+ 只个股的涨跌幅排行。
    """
    dp = build().build(date="2026-10-17")
    # 全量排行来自财联社（不再依赖涨停池）
    assert dp["sources"]["bse"] == SOURCE_CLS
    names = {s["name"] for s in dp["bse"]}
    assert "南方乳业" in names          # 没涨停也要出现
    assert "国航远洋" in names
    # 总数透传, 供前端显示「共 N 只」
    assert dp["bse_total"] == 351

    # 涨停池里的北交所股要被打上标记（代码前缀 bj920117 vs 后缀 920117.BJ）
    by_name = {s["name"]: s for s in dp["bse"]}
    assert by_name["国航远洋"]["is_limit_up"] is True
    assert by_name["国航远洋"]["limit_count"] == 2
    assert by_name["南方乳业"]["is_limit_up"] is False


def test_bse_falls_back_to_limit_up_pool_when_ranking_dead():
    """北交所排行接口挂了 -> 退回涨停池里的北交所股, 不能整页空。"""
    dp = build(cls=DeadClient()).build(date="2026-10-17")
    assert {s["name"] for s in dp["bse"]} == {"国航远洋"}
    assert dp["sources"]["bse"] == SOURCE_KPL


def test_zt_bao_falls_back_to_em_pool_when_kpl_dead():
    """开盘啦天梯挂了, 涨停池改用东财涨停池, 且代码要补上交易所前缀。"""
    dp = build(kpl=DeadClient()).build(date="2026-10-17")
    assert dp["sources"]["zt_bao"] == SOURCE_EM
    assert dp["zt_bao"][0]["symbol"] == "sh600825"


# ---------------------------------------------------------------------------
# 第三数据源自身
# ---------------------------------------------------------------------------
def test_hhmmss_converts_eastmoney_integer_time():
    """东财首封时间是 HHMMSS 整数, 92500 必须变成 09:25:00 而不是 92:50:0。"""
    assert EastmoneyDingpanClient._hhmmss(92500) == "09:25:00"
    assert EastmoneyDingpanClient._hhmmss(145131) == "14:51:31"
    assert EastmoneyDingpanClient._hhmmss(0) == ""
    assert EastmoneyDingpanClient._hhmmss(None) == ""
    assert EastmoneyDingpanClient._hhmmss("") == ""


def test_em_zt_pool_detail_maps_all_documented_fields():
    """涨停池字段映射: 只验证映射逻辑, 不联网。"""
    client = EastmoneyDingpanClient()
    client._fetch_pool = lambda *a, **k: {"tc": 1, "qdate": 20261016, "pool": [
        {"c": "000573", "n": "粤宏远Ａ", "lbc": 2, "zdp": 10.098, "fund": 158637171,
         "fbt": 92500, "zbc": 0, "hs": 1.25, "ltsz": 2827542390.9,
         "hybk": "房地产开", "zttj": {"days": 3, "ct": 2}},
    ]}
    rows = client.get_zt_pool("20261017")
    assert rows == [{
        "code": "000573", "name": "粤宏远Ａ", "limit_count": 2, "change_pct": 10.098,
        "seal_fund": 158637171, "first_limit_time": "09:25:00", "break_count": 0,
        "turnover_rate": 1.25, "float_market_cap": 2827542390.9,
        "plate_name": "房地产开", "limit_stat": {"days": 3, "ct": 2},
    }]


def test_em_ladder_rate_uses_previous_trading_day_base():
    """连板率必须是「今日该高度 / 昨日低一档」, 不能用相邻高度比凑。"""
    client = EastmoneyDingpanClient()
    pools = {
        # 今日 2026-10-16: 一板4 二板1 三板1 高度板0
        "20261016": {"pool": [{"lbc": 1}, {"lbc": 1}, {"lbc": 1}, {"lbc": 1},
                              {"lbc": 2}, {"lbc": 3}]},
        # 上一交易日 2026-10-15: 一板4 二板1 三板1 —— 作为连板率基数
        "20261015": {"pool": [{"lbc": 1}, {"lbc": 1}, {"lbc": 1}, {"lbc": 1},
                              {"lbc": 2}, {"lbc": 3}]},
    }
    client._fetch_pool = lambda _e, date, _s: pools.get(str(date).replace("-", ""))
    ladder = client.get_ladder("2026-10-16")
    assert ladder["counts"] == ["4", "1", "1", "0"]
    # 二板连板率 = 今日二板(1) / 昨日一板(4) = 25%
    assert ladder["rates"][1] == "25%"
    # 三板连板率 = 今日三板(1) / 昨日二板(1) = 100%
    assert ladder["rates"][2] == "100%"
    # 高度板连板率 = 今日高度板(0) / 昨日三板(1) = 0%
    assert ladder["rates"][3] == "0%"


def test_em_ladder_marks_unknown_rate_instead_of_faking_it():
    """拿不到上一交易日时, 连板率必须是 '—', 不能拿相邻高度比凑出 200% 这种值。"""
    client = EastmoneyDingpanClient()
    client._fetch_pool = lambda _e, date, _s: (
        {"pool": [{"lbc": 1}, {"lbc": 2}, {"lbc": 3}, {"lbc": 4}]}
        if str(date).replace("-", "") == "20261016" else None
    )
    ladder = client.get_ladder("2026-10-16")
    assert ladder["rates"] == ["连板率", "—", "—", "—"]


def test_em_counts_derives_seal_rate_from_zt_and_broken():
    """封板率 = 涨停 / (涨停 + 炸板)。"""
    client = EastmoneyDingpanClient()
    client._fetch_pool = lambda endpoint, *_a, **_k: {
        "getTopicZTPool": {"tc": 61, "qdate": 20261016},
        "getTopicDTPool": {"tc": 10},
        "getTopicZBPool": {"tc": 49},
    }[endpoint]
    counts = client.get_limit_pool_counts("20261017")
    assert counts["limit_up_count"] == 61
    assert counts["limit_down_count"] == 10
    assert counts["broken_count"] == 49
    assert counts["touched_count"] == 110
    assert counts["seal_rate"] == 55.45


def test_em_summary_falls_back_to_last_trading_day_when_today_is_zero():
    """非交易日同花顺 today 全 0, 必须回落到 yesterday, 否则页面显示 0 家封板。"""
    client = EastmoneyDingpanClient()
    client._ths_get = lambda *a, **k: {
        "limit_up_count": {
            "today": {"num": 0, "history_num": 0, "rate": None, "open_num": 0},
            "yesterday": {"num": 59, "history_num": 108, "rate": 0.5463, "open_num": 49},
        },
        "limit_down_count": {"today": {}},
        "trade_status": {"name": "非交易日"},
    }
    summary = client.get_limit_up_summary("20261017")
    assert summary["effective_label"] == "yesterday"
    assert summary["effective"]["sealed"] == 59
    assert summary["effective"]["seal_rate"] == "54.63%"


def test_em_dead_upstream_returns_empty_not_raises():
    """备用源自己挂掉时必须安静返回空, 不能把异常抛给聚合器。

    注意替换的是 `session.get`(真正的网络层), 而不是 `_fetch_pool` ——
    后者的 try/except 正是被测的错误处理逻辑, 替换掉它等于没测。
    """
    client = EastmoneyDingpanClient()
    client.session.get = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("boom"))

    assert client.get_limit_pool_counts("20261017")["limit_up_count"] is None
    assert client.get_limit_pool_counts("20261017")["seal_rate"] is None
    assert client.get_ladder("20261017") == {}
    assert client.get_zt_pool("20261017") == []
    assert client.get_limit_up_summary("20261017") == {}
    assert client.get_continuous_ladder("20261017") == {}
    assert client.get_dingpan_fallback("20261017")["seal_rate_str"] == ""


# ---------------------------------------------------------------------------
# 连板率口径：非交易日的「基数取到自身」回归测试
# ---------------------------------------------------------------------------
# 真实故障: 2026-10-17 是周六, 东财 push2ex 静默回落到 10-16 的数据。
# 但回溯「上一交易日」时从 10-17 往回走, 第一个有数据的日子就是 10-16 ——
# 正是同一个交易日, 于是基数=自身, 连板率退化成自比值 7%/25%/100%,
# 而财联社给的是 13%/50%/50%。数值看着「像那么回事」, 所以极难察觉。
#
# 实测东财行为: qdate 恒为全市场最近交易日; 请求日 > qdate 时响应就是
# qdate 的数据(回落), 请求日 <= qdate 时响应是请求日自己的数据。
LATEST = "20261016"          # 最近交易日 2026-10-16
POOLS = {
    # 10-16: 54/4/1/1/1 -> 一板54 二板4 三板1 高度板2
    "20261016": [1] * 54 + [2] * 4 + [3] + [4] + [11],
    # 10-15: 30/2/2/2 -> 一板30 二板2 三板2 高度板2
    "20261015": [1] * 30 + [2] * 2 + [3] * 2 + [5] * 2,
    "20261014": [1] * 21 + [2] * 11 + [3] + [4] * 3,
}


def _fake_pool_client():
    """伪造 push2ex 响应, 复刻「请求日 > qdate 即回落」的真实语义。"""
    client = EastmoneyDingpanClient()

    def _fetch_pool(action, date, order=None, **_kw):
        if action != "getTopicZTPool":
            return {}
        d = str(date).replace("-", "")
        # 请求日晚于最近交易日 -> 回落, 返回最近交易日的数据
        payload_date = LATEST if d > LATEST else d
        heights = POOLS.get(payload_date)
        if not heights:
            return {"tc": 0, "qdate": int(LATEST), "pool": []}
        pool = [{"c": f"{i:06d}", "lbc": h} for i, h in enumerate(heights)]
        return {"tc": len(pool), "qdate": int(LATEST), "pool": pool}

    client._fetch_pool = _fetch_pool
    return client


def test_resolve_trade_date_uses_qdate_only_when_response_fell_back():
    """回落判定: 请求日晚于最近交易日才算回落, 不能一律拿 qdate 顶替。

    若一律用 qdate, 2026-10-15 会被错判成 10-16, 基数又变成自身。
    """
    c = _fake_pool_client()
    assert c._resolve_trade_date("2026-10-17") == "2026-10-16"   # 周六 -> 回落
    assert c._resolve_trade_date("2026-10-16") == "2026-10-16"   # 最近交易日本身
    assert c._resolve_trade_date("2026-10-15") == "2026-10-15"   # 更早的交易日, 不回退
    assert c._resolve_trade_date("2026-10-10") == "2026-10-10"   # 过去的周末 -> 保留


def test_ladder_base_is_previous_trading_day_not_itself():
    """连板率的分母必须是「昨日」, 绝不能落到与今日同一个交易日。"""
    c = _fake_pool_client()
    ladder = c.get_ladder("2026-10-17")

    assert ladder["counts"] == ["54", "4", "1", "2"]
    # 财联社权威口径: 4/30=13%, 1/2=50%, 高度板 2/(2+2)=50%
    assert ladder["rates"] == ["连板率", "13%", "50%", "50%"]


def test_ladder_rate_is_not_self_ratio():
    """守卫断言: 自比值 7%/25%/100% 一旦重现, 立刻失败。"""
    c = _fake_pool_client()
    assert c.get_ladder("2026-10-17")["rates"] != ["连板率", "7%", "25%", "100%"]


def test_ladder_high_tier_numerator_matches_displayed_count():
    """高度板是聚合档: 分子必须用 k>=4 的家数, 与展示的家数一致。

    旧实现分子取 k==4 的精确家数(1), 展示却是 2 家, 页面自相矛盾。
    """
    c = _fake_pool_client()
    ladder = c.get_ladder("2026-10-17")
    displayed = int(ladder["counts"][3])
    # 50% = 2/4, 分子就是展示的 2 家; 若分子误用 1 家会得到 25%
    assert displayed == 2
    assert ladder["rates"][3] == "50%"


def test_ladder_returns_placeholder_when_base_missing():
    """基数取不到时给 '—', 不编数。"""
    c = _fake_pool_client()
    c._previous_trade_date = lambda *a, **k: None
    assert c.get_ladder("2026-10-17")["rates"] == ["连板率", "—", "—", "—"]


# ---------------------------------------------------------------------------
# 大盘页完整模块（C1 指数报价 / C14 个股排行 / 异动与炸板 / 下钻明细）
# ---------------------------------------------------------------------------
def test_index_quotes_present_and_ordered():
    """C1 指数报价 —— 这是页面最顶部、曾经完全缺失的一块。"""
    dp = build().build(date="2026-10-17")
    iq = dp["market"]["index_quotes"]
    assert iq["available"] is True
    assert dp["sources"]["index_quotes"] == SOURCE_CLS
    quotes = iq["quotes"]
    assert len(quotes) == 2
    # INDEX_ORDER 保证上证在前、深证在后
    assert quotes[0]["code"] == "sh000001"
    assert quotes[0]["change_str"] == "+0.24%"
    assert quotes[0]["up_num"] == 1301


def test_ranking_has_all_four_boards_for_drilldown():
    """个股排行要预取 4 个榜 —— 连板池/炸板池/跌停池就是「点开」的明细源。"""
    dp = build().build(date="2026-10-17")
    rk = dp["ranking"]
    assert rk["available"] is True
    for topic in ("all", "continuous_up", "up_open", "down"):
        assert topic in rk["boards"], f"缺少榜单 {topic}"
        assert len(rk["boards"][topic]) > 0


def test_blasting_block_exposes_fuses_and_articles():
    """涨停宝页的「异动与炸板」此前完全没有。"""
    dp = build().build(date="2026-10-17")
    b = dp["blasting"]
    assert b["available"] is True
    assert b["blasting_fuses"][0]["desc"] == "药明康德跳水"
    assert b["articles"][0]["title"].startswith("两市成交额")


def test_drill_payload_prefetched_for_every_clickable_card():
    """点得开的前提是数据已经预取在 bundle 里（手机端不能直连财联社）。"""
    dp = build().build(date="2026-10-17")
    drill = dp["drill"]
    # 指数卡 -> 前三大成分股
    assert "sh000001" in drill["index"]
    assert drill["index"]["sh000001"]["top3"][0]["name"] == "新华传媒"
    # 资金热力图卡片 -> 板块详情 + 涨停个股
    assert "cls1" in drill["plate"]
    assert drill["plate"]["cls1"]["info"]["name"] == "芯片产业链"
    assert drill["plate"]["cls1"]["up_stocks"][0]["name"] == "中复神鹰"
    # 风口 -> 板块内个股
    assert "cls80222" in drill["tuyere"]


def test_missing_client_methods_degrade_instead_of_crashing():
    """客户端没有新方法时必须安静降级。

    这条守的是 `_safe(client.get_x)` 的陷阱：属性查找发生在 try 之前，
    缺方法会先抛 AttributeError。改用 `_call(client, "get_x")` 才能兜住。
    """
    class OldCLS:
        """只实现老接口的客户端（模拟旧版本 / 精简 mock）。"""
        def get_market_emotion(self):
            return {"market_degree": 54, "distribution_series": [],
                    "limit_board": {}}

    dp = DingpanAggregator(cls_client=OldCLS(), kpl_client=FakeKPL(),
                           em_client=FakeEM()).build(date="2026-10-17")
    # 不崩，且新块如实标记不可用
    assert dp["market"]["index_quotes"]["available"] is False
    assert dp["ranking"]["available"] is False
    assert dp["blasting"]["available"] is False
    assert "index_quotes" in dp["unavailable"]
    assert "ranking" in dp["unavailable"]
    assert "blasting" in dp["unavailable"]


# ======================================================================
# 2026-10-17 模拟器实机抓包带来的参数契约
# 这些断言锁的是「真 App 到底发什么参数」，防止以后改回错误写法
# ======================================================================


class _RecordingCLS(FakeCLS):
    """记录收到的 kwargs，用来断言参数名是否正确。"""

    def __init__(self):
        super().__init__()
        self.ranking_calls = []
        self.bj_calls = []
        self.up_reason_calls = []

    def get_stock_ranking(self, topic="all", style=None, index=1, way="change",
                          reverse=1, deviation_num=20, include_st=1, fields=None):
        self.ranking_calls.append(
            {"topic": topic, "style": style, "way": way, "reverse": reverse,
             "fields": fields})
        return super().get_stock_ranking(
            topic=topic, style=style, way=way, reverse=reverse)

    def get_bj_stock_info(self, way="change", rever=1, index=1,
                          deviation_num=50, market="bj", types=None):
        self.bj_calls.append({"market": market, "way": way, "rever": rever})
        return {"market": market, "market_name": "北京证券交易所",
                "total_num": 351, "items": [
                    {"code": "920196.BJ", "name": "南方乳业", "last_px": 22.56,
                     "change": 0.5876, "change_str": "+58.76%"}]}

    def get_stock_up_reason(self, secu_codes):
        self.up_reason_calls.append(list(secu_codes or []))
        # 真接口的键 = 请求格式（前缀式）
        return {c: "低空经济|说明" for c in (secu_codes or [])}


def test_ranking_uses_reverse_not_rever():
    """真 App 的 ranking 发的是 `reverse`，写 `rever` 不报错但永远是涨幅榜。

    这是本轮在模拟器里抓包才发现的问题：之前的实现传 `rever`，
    跌幅榜/量比/换手率/成交额四个排序全都不生效，看起来"能跑"其实是假数据。
    """
    cls = _RecordingCLS()
    dp = DingpanAggregator(cls_client=cls, kpl_client=FakeKPL(),
                           em_client=FakeEM()).build()
    assert dp["ranking"]["available"]
    for call in cls.ranking_calls:
        assert "reverse" in call and "rever" not in call
    # 池类（连板/炸板/跌停）必须走 way=time
    pool_calls = [c for c in cls.ranking_calls
                  if c["topic"] in ("continuous_up", "up_open", "down")]
    assert pool_calls, "应该取三个池"
    for c in pool_calls:
        assert c["way"] == "time"


def test_ranking_pool_topics_get_style_b():
    """池 topic 配 A style 不报错但返回 0 条，看着像"今天没连板"。"""
    cls = _RecordingCLS()
    DingpanAggregator(cls_client=cls, kpl_client=FakeKPL(),
                      em_client=FakeEM()).build()
    style_by_topic = {c["topic"]: c["style"] for c in cls.ranking_calls}
    assert style_by_topic["all"] == "TOPIC-STYLE-A-240223"
    assert style_by_topic["continuous_up"] == "TOPIC-STYLE-B-240223"
    assert style_by_topic["up_open"] == "TOPIC-STYLE-B-240223"
    assert style_by_topic["down"] == "TOPIC-STYLE-B-240223"


def test_up_reason_converts_to_prefix_style():
    """up_reason 只认前缀式代码，榜代码是后缀式/纯数字，必须转换。

    不转换时接口静默返回空 dict，前端查不到任何业务说明 —— 不报错，
    所以只能靠这条断言兜住。
    """
    cls = _RecordingCLS()
    dp = DingpanAggregator(cls_client=cls, kpl_client=FakeKPL(),
                           em_client=FakeEM()).build()
    assert cls.up_reason_calls, "应该请求过 up_reason"
    for codes in cls.up_reason_calls:
        for c in codes:
            assert c[:2] in ("sz", "sh", "bj"), f"{c} 不是前缀式"
    # 返回值要映射回榜代码，前端才查得到
    items = dp["up_reason"]["items"]
    assert items, "up_reason 不应为空"
    for k in items:
        assert k[:2] not in ("sz", "sh", "bj"), f"{k} 应已映射回榜代码"


def test_new_blocks_present_and_marked():
    """新增的 6 个数据块必须出现在 payload 里，且来源标注正确。"""
    cls = _RecordingCLS()
    dp = DingpanAggregator(cls_client=cls, kpl_client=FakeKPL(),
                           em_client=FakeEM()).build()
    for key in ("zt_panorama", "plate_detail", "up_reason", "anchor",
                "trading_days", "extra"):
        assert key in dp, f"payload 缺少 {key}"
        assert dp[key]["available"], f"{key} 标记为不可用"
        assert dp["sources"].get(key) == "财联社(8.5.4)", \
            f"{key} 的来源标注不对: {dp['sources'].get(key)!r}"


def test_zt_panorama_prefetches_plate_detail():
    """涨停宝的「涨停全景图」要点得开，板块明细必须预取。"""
    cls = _RecordingCLS()
    dp = DingpanAggregator(cls_client=cls, kpl_client=FakeKPL(),
                           em_client=FakeEM()).build()
    zp = dp["zt_panorama"]
    assert zp["plates"], "应取到板块涨停家数"
    assert zp["detail"], "板块明细必须预取，否则点不开"


def test_trading_days_use_closest_day():
    """交易日列表要落到最近交易日，周末/节假日不能返回空。"""
    cls = _RecordingCLS()
    dp = DingpanAggregator(cls_client=cls, kpl_client=FakeKPL(),
                           em_client=FakeEM()).build()
    td = dp["trading_days"]
    assert td["closest"] == "2026-10-16"
    assert td["days"], "应取到交易日列表"


# ======================================================================
# 2026-10-17 风口 / 主线 深挖（第二轮）
# /v2/dingPan/mainline 一个接口同时喂三个 Tab，之前只解析了 chances.faucet_1
# ======================================================================


class _FullMainlineCLS(FakeCLS):
    """返回与真 App 抓包一致的 mainline 完整结构。"""

    def get_mainline(self):
        return {
            "chance_desc": "判断主线机会，洞悉趋势强度",
            "mainLine_desc": "精准把控主力动向，掌握主线机会",
            "wind_desc": "紧跟每日风口，领悟市场风向",
            "style_desc": "预判风格走向，投资无往不利",
            "mainlines": [{
                "id": 4, "hot": 4, "continued": 4, "title": "影视传媒",
                "desc": "短剧出海数据亮眼",
                "plates": [{
                    "plate_code": "cls80155", "plate_name": "文化传媒",
                    "reason": "据Sensor Tower数据…", "rise_count": 5,
                    "rise_range": -0.0177,
                    "faucets": [
                        {"name": "慈文传媒", "code": "sz002343",
                         "reason": "6天涨超60%", "rise_range": 4.71},
                        {"name": "读者传媒", "code": "sh603999",
                         "reason": "2连板", "rise_range": 9.97},
                        {"name": "新华文轩", "code": "sh601811",
                         "reason": "17天涨超130%", "rise_range": 10.01},
                    ],
                    "article": {"id": 2506380, "title": "外部回暖叠加AI链龙头密集回购",
                                "read_count": "921889"},
                }],
            }],
            "styles": {
                "id": 2499598, "title": "A股风格重构深化",
                "interpret": "10月A股进入业绩验证期",
                "index_code": "sh000001", "index_name": "上证指数",
                "index_change30": "-6.07%",
                "stacks": [
                    {"code": "sh600825", "name": "新华传媒",
                     "last_px": "15.16", "pre30_close": "5.06",
                     "rise_range": "+199.60%", "change_px": 1.38},
                ],
            },
            "winds": [{
                "id": 14, "title": "军工", "plate_code": "cls80222",
                "subject_id": 1485, "hot": 4, "plate_change": 0.0248,
                "interpret": "第十六届中国国际航空航天博览会将于12月举办",
                "faucet_stocks": [
                    {"code": "sz300581", "name": "晨曦航空", "rise_range": 20.02},
                    {"code": "920006.BJ", "name": "晟楠科技", "rise_range": 29.98},
                    {"code": "sz002724", "name": "海洋王", "rise_range": 10.07},
                ],
            }, {
                "id": 13, "title": "低空经济", "plate_code": "cls82437",
                "subject_id": 11379, "hot": 4, "plate_change": 0.0272,
                "interpret": "特斯拉第二代Roadster亮相",
                "faucet_stocks": [
                    {"code": "sz300045", "name": "华力创通", "rise_range": 20.04},
                ],
            }],
        }


def test_mainline_exposes_all_three_blocks():
    """一个 mainline 接口要喂三个 Tab：主线 / 市场风格 / 每日风口。

    之前只解析 chances.faucet_1，styles 和 winds 整块丢掉，
    主线页只剩一个标题，风口页的涨跌幅和龙头全是空的。
    """
    dp = DingpanAggregator(cls_client=_FullMainlineCLS(), kpl_client=FakeKPL(),
                           em_client=FakeEM()).build()
    ml = dp["mainline"]

    for k in ("chance_desc", "mainLine_desc", "wind_desc", "style_desc",
              "mainlines", "styles", "winds"):
        assert k in ml, f"mainline 缺 {k}"

    m = ml["mainlines"][0]
    assert m["title"] == "影视传媒"
    assert m["hot"] == 4 and m["continued"] == 4

    p = m["plates"][0]
    assert p["plate_name"] == "文化传媒"
    assert p["rise_count"] == 5
    # 三个龙一龙二龙三都要在（之前只有 faucet_1）
    assert len(p["faucets"]) == 3, f"只要到 {len(p['faucets'])} 个龙头"
    assert [f["name"] for f in p["faucets"]] == ["慈文传媒", "读者传媒", "新华文轩"]
    assert p["article"]["title"] == "外部回暖叠加AI链龙头密集回购"

    s = ml["styles"]
    assert s["index_name"] == "上证指数"
    assert s["index_change30"] == "-6.07%"
    assert len(s["stacks"]) == 1

    w = ml["winds"][0]
    assert w["title"] == "军工"
    assert w["subject_id"] == 1485
    assert len(w["faucet_stocks"]) == 3


def test_tuyere_cards_merge_change_and_inflow():
    """风口页 3 张卡 = 标题 + 涨跌幅 + 主力净流入 + 投资逻辑。

    涨跌幅来自 mainline.winds（不必再发请求），净流入来自 plate/info。
    之前 `todayTuyere` 只有标题和文案，卡片上两个数都是空的。
    """
    cls = _FullMainlineCLS()
    dp = DingpanAggregator(cls_client=cls, kpl_client=FakeKPL(),
                           em_client=FakeEM()).build()
    ty = dp["tuyere"]
    assert ty, "风口不应为空"
    for t in ty:
        assert t.get("plate_change_str"), f"{t['title']} 缺涨跌幅"
        assert t.get("money_inflow_str"), f"{t['title']} 缺净流入"
        assert t.get("interpret"), f"{t['title']} 缺投资逻辑"
        assert t.get("subject_id"), "subject_id 要留着，风口追踪以后要用"


def test_tuyere_drill_prefetches_top3_and_full_list():
    """风口点开要能看到龙一龙二龙三 + 更多概念个股（板块全量）。"""
    cls = _FullMainlineCLS()
    dp = DingpanAggregator(cls_client=cls, kpl_client=FakeKPL(),
                           em_client=FakeEM()).build()
    td = dp["tuyere_drill"]
    assert td, "风口中钻必须预取，否则点不开"
    for _code, d in td.items():
        assert d.get("top3"), "龙一龙二龙三必须有"
        assert "all_total" in d, "要带上板块全量家数（更多概念个股）"
        assert d["all_total"] > 0, "板块全量不为 0"


def test_tuyere_subject_id_preserved_for_future_use():
    """subject_id 是「风口追踪」的专题 id，现在虽然拿不到文章也要留住。"""
    dp = DingpanAggregator(cls_client=_FullMainlineCLS(), kpl_client=FakeKPL(),
                           em_client=FakeEM()).build()
    ids = [t.get("subject_id") for t in dp["tuyere"]]
    assert 1485 in ids, "军工的 subject_id=1485 丢了"
