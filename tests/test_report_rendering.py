from decision_dashboard import (
    _quality_html,
    _quality_public_hint,
    build_dashboard_ctx,
    generate_dashboard_html,
    generate_dashboard_section,
)
from data_sources.models import FetchResult
from data_sources.name_resolver import NameResolution
from data_sources.quality_gate import QualityIssue, QualityReport


def _context():
    return build_dashboard_ctx(
        timing={"scene": "旧场景", "action": "旧动作", "position": "5成仓位"},
        advance_decline={"up": 3500, "down": 1500, "zt": 90, "dt": 2},
        regime={
            "code": "BROAD_STRONG", "title": "普涨反弹 · 梯队健康",
            "action": "只做主线确认，后排不追", "color": "#f85149",
            "reason": "上涨占比 0.700",
        },
        data_quality={
            "ok": False,
            "name_conflicts": 1,
            "limit_pool_status": "partial",
            "limit_pool_source": "ZT:akshare_em|CHECK:ZT:eastmoney_push2ex",
            "notes": ["涨停数量差异"],
            "missing_fields": ["previous_limit_pool_snapshot"],
            "decision_degraded": ["daily_delta"],
        },
        ladder_review={
            "distribution": {1: 60, 2: 25, 3: 1, 4: 2, 8: 1},
            "promotions": {1: {"eligible": 121, "advanced": 25, "rate": 0.207}},
            "high_break_count": 1,
            "missing_heights": [5, 6, 7],
        },
        report_date="2026-08-05",
    )


def test_action_plan_builds_named_attack_confirm_and_risk_groups_from_echelon():
    from decision_dashboard import _build_action_plan

    ctx = _context()
    ctx["echelon"] = [
        {
            "height": "2连板",
            "stock_details": [
                {"name": "江化微", "code": "sh603078", "ml": "AI算力"},
            ],
        },
        {
            "height": "3连板",
            "stock_details": [
                {"name": "沃格光电", "code": "sh603773", "ml": "AI算力"},
            ],
        },
        {
            "height": "10连板",
            "stock_details": [
                {"name": "爱丽家居", "code": "sh603221", "ml": "其它"},
            ],
        },
    ]
    ctx["scene"] = "高位承压 · 结构换挡"

    plan = _build_action_plan(ctx)

    assert plan["position"] == "2-4 成"
    assert [group["code"] for group in plan["groups"]] == ["attack", "confirm", "risk"]
    assert "江化微" in str(plan)
    assert "沃格光电" in str(plan)
    assert "爱丽家居" in str(plan)
    assert "买入" in str(plan["groups"][0])
    assert "加仓" in str(plan["groups"][1])
    assert "减仓" in str(plan["groups"][2])


def test_action_plan_rejects_missing_codes_and_deduplicates_by_risk_priority():
    from decision_dashboard import _build_action_plan

    ctx = _context()
    ctx["echelon"] = [
        {
            "height": "2连板",
            "stock_details": [
                {"name": "重复样本", "code": "603078", "ml": "AI算力"},
                {"name": "无代码样本", "code": "", "ml": "AI算力"},
            ],
        },
        {
            "height": "6连板",
            "stock_details": [
                {"name": "重复样本", "code": "sh603078", "ml": "AI算力"},
            ],
        },
    ]

    plan = _build_action_plan(ctx)
    rendered = str(plan)

    assert rendered.count("重复样本") == 1
    assert "无代码样本" not in rendered
    assert [group["code"] for group in plan["groups"]] == ["risk"]


def test_action_plan_facts_only_is_empty_and_no_candidate_plan_is_explicit():
    from decision_dashboard import _build_action_plan

    facts_only = _context()
    facts_only["publication_mode"] = "facts_only"
    facts_only["market_state"] = {"publication_mode": "facts_only"}

    blocked_plan = _build_action_plan(facts_only)
    assert blocked_plan["position"] == "空仓"
    assert blocked_plan["core_action"] == "不开新仓"
    assert blocked_plan["groups"] == []

    empty = _context()
    empty["echelon"] = []
    empty_plan = _build_action_plan(empty)
    assert empty_plan["groups"] == []
    assert empty_plan["core_action"] == "今日无合格标的，不开新仓"


def test_actionable_dashboard_replaces_generic_blocks_with_named_execution_rows():
    ctx = _context()
    ctx["scene"] = "高位承压 · 结构换挡"
    ctx["echelon"] = [
        {
            "height": "2连板",
            "stock_details": [
                {"name": "江化微", "code": "sh603078", "ml": "AI算力"},
            ],
        },
        {
            "height": "3连板",
            "stock_details": [
                {"name": "沃格光电", "code": "sh603773", "ml": "AI算力"},
            ],
        },
        {
            "height": "10连板",
            "stock_details": [
                {"name": "爱丽家居", "code": "sh603221", "ml": "其它"},
            ],
        },
    ]
    ctx["prediction_review"] = {
        "prediction_count": 2,
        "pending_count": 2,
        "scored_count": 0,
    }

    for html in (generate_dashboard_html(ctx), generate_dashboard_section(ctx)):
        assert "建议仓位" in html
        assert "2-4 成" in html
        assert "核心动作" in html
        assert "明日执行计划" in html
        assert "江化微" in html
        assert "分歧回封买入" in html
        assert "沃格光电" in html
        assert "晋级确认后加仓" in html
        assert "爱丽家居" in html
        assert "断板减仓" in html
        assert "触发" in html
        assert "失效" in html
        assert "判断依据" not in html
        assert "明日验证路径" not in html
        assert "等待验证信号" not in html
        assert "连板复盘 · 连板质量" not in html
        assert "主线复盘 · 主线集中度" not in html
        assert "历史预测复盘" not in html


def test_actionable_dashboard_only_renders_prediction_review_when_scored():
    ctx = _context()
    ctx["echelon"] = []
    ctx["prediction_review"] = {
        "prediction_count": 3,
        "pending_count": 2,
        "scored_count": 1,
        "matured_count": 1,
        "hit_rate": 1.0,
    }

    assert "历史预测复盘" in generate_dashboard_section(ctx)


def test_dashboard_surfaces_quality_before_research_evidence():
    html = generate_dashboard_html(_context())

    assert "部分内容待补齐" in html
    assert "数据可信度" not in html
    assert "名称冲突 1" not in html
    assert "partial" not in html
    assert "ZT:akshare_em" not in html
    assert "二进三" in html
    assert "样本待补" in html
    assert html.index("普涨反弹 · 梯队健康") < html.index("数据佐证")


def test_dashboard_replaces_missing_two_to_three_sample_with_current_ladder_counts():
    ctx = _context()
    ctx["echelon"] = [
        {"height": "2连板", "count": 9, "stock_details": []},
        {"height": "3连板", "count": 9, "stock_details": []},
    ]

    html = generate_dashboard_html(ctx)

    assert "低位梯队" in html
    assert "2板 9 / 3板 9" in html
    assert "样本待补" not in html


def test_embedded_dashboard_contains_same_quality_summary():
    html = generate_dashboard_section(_context())

    assert "部分内容待补齐" in html
    assert "数据可信度" not in html
    assert "涨停数量差异" not in html


def test_quality_summary_is_collapsed_and_hint_stays_inside_card():
    html = generate_dashboard_html(_context())

    assert '<details class="quality-card"' in html
    assert '<summary>' in html
    assert '数据状态' in html
    assert '昨日逐股反馈待补齐，以今日事实为准。' in html
    assert html.index('昨日逐股反馈待补齐，以今日事实为准。') > html.index('<details class="quality-card"')


def test_embedded_quality_summary_is_collapsed_too():
    html = generate_dashboard_section(_context())

    assert '<details class="dbd-quality-card"' in html
    assert '<summary>' in html
    assert '数据状态' in html
    assert '昨日逐股反馈待补齐，以今日事实为准。' in html


def test_observation_mode_preserves_business_scene_and_uses_compact_status_line():
    html = generate_dashboard_html(_context())
    embedded = generate_dashboard_section(_context())

    for page in (html, embedded):
        assert '普涨反弹 · 梯队健康' in page
        assert '盘面观察' not in page
        assert '发布状态' not in page
        assert '观察模式 · 满足触发条件后再评估' not in page
        assert '明日验证：市场宽度与主线梯队是否同步增强。' in page
        assert '关键数据未齐全' not in page
        assert '<div class="quality-blocked-note">' not in page
        assert '<div class="dbd-quality-blocked-note">' not in page
        assert '条件触发' not in page


def test_observation_mode_promotes_guarded_ai_market_judgement_to_hero():
    ctx = _context()
    ctx["report_context"] = {
        "observations": {
            "ai": {
                "status": "sanitized",
                "output": {
                    "observations": [
                        "真正有主动资金痕迹的是AI算力上游分支，多股在2—3板位置同步涨停，横向共振优于纵向高度。",
                        "情绪拐点信号：高位全灭而中位仍有资金回流，是『去高度、留厚度』的换挡形态。关键看中位梯队明日能否补出4—5板断层。",
                    ],
                    "conditions": [
                        "核对内部数据口径。",
                        "若高标大幅低开放量，则确认退潮。",
                    ],
                    "risks": ["高位断层后，中位连板容易一日游。"],
                    "decision": "",
                },
            }
        }
    }

    html = generate_dashboard_html(ctx)
    embedded = generate_dashboard_section(ctx)

    for page in (html, embedded):
        assert "去高度、留厚度 · 高位承压" in page
        assert "高位全灭而中位仍有资金回流" in page
        assert "明日验证：中位梯队明日能否补出4—5板断层" in page
        assert "观察模式 · 满足触发条件后再评估" not in page
        assert "满足触发条件后再评估。" not in page
        assert "5成仓位" not in page
        assert "只做主线确认，后排不追" not in page


def test_observation_mode_combines_ai_signals_into_clear_rotation_judgement():
    ctx = _context()
    ctx["level"] = "观察模式"
    ctx["report_context"] = {
        "observations": {
            "ai": {
                "status": "sanitized",
                "output": {
                    "observations": [
                        "高度断层特征成立且已进入负反馈阶段。高位板不是分歧，是失守。",
                        "资金从传媒、电网高位切向半导体材料、电子特气与AI算力硬件。这是资金弃高就低、重建梯队，而非全面退潮。",
                        "情绪拐点信号已出现在4板一线。",
                    ],
                    "conditions": [
                        "明日核心验证：3板梯队能否至少产出1—2只4板，补上4—5板缺档。补上则轮动延续，补不上则退潮确认。",
                    ],
                },
            }
        }
    }

    html = generate_dashboard_html(ctx)
    embedded = generate_dashboard_section(ctx)

    for page in (html, embedded):
        assert "高位失守 · 低位重建" in page
        assert "高位梯队出现明显负反馈" in page
        assert "资金正从传媒、电网高位切向半导体材料、电子特气与AI算力硬件" in page
        assert "AI综合研判" in page
        assert "报告日期" in page
        assert "明日验证：3板梯队能否至少产出1—2只4板" in page
        assert "明日验证：明日核心验证：" not in page
        assert "观察模式" not in page


def test_ai_failure_uses_market_facts_instead_of_internal_quality_reasons():
    ctx = _context()
    ctx["desc"] = (
        "使用备用数据源；ai_judgement；previous_limit_pool_snapshot；"
        "bomb_rate；reclose_rate；board_structure；上游接口连续 3 次返回 502"
    )
    ctx["curr_h"] = 10
    ctx["report_context"] = {
        "observations": {
            "ai": {"status": "error", "reason": "上游接口连续 3 次返回 502"},
        }
    }

    for page in (generate_dashboard_html(ctx), generate_dashboard_section(ctx)):
        assert "涨停90家、跌停2家，上涨占比70%" in page
        assert "优先观察低位梯队晋级" in page
        assert "ai_judgement" not in page
        assert "previous_limit_pool_snapshot" not in page
        assert "bomb_rate" not in page
        assert "上游接口连续 3 次返回 502" not in page


def test_mobile_dashboard_prioritizes_judgement_and_execution_plan():
    standalone = generate_dashboard_html(_context())
    embedded = generate_dashboard_section(_context())

    assert ".hero { display: none; }" in standalone
    assert ".dbd-hero { display: none; }" in embedded
    assert ".dashboard-header .subtitle { display: none !important; }" in embedded
    assert ".dashboard-header h1 small { display: none !important; }" in embedded


def test_ai_judgement_labels_mainline_crowding_with_height_gap():
    ctx = _context()
    ctx["report_context"] = {
        "observations": {
            "ai": {
                "status": "sanitized",
                "output": {
                    "observations": [
                        "83家涨停+0.84集中度，说明赚钱效应集中在少数主线上，资金不是撒网而是抱团；AI算力属于有梯队支撑的方向。",
                        "梯队完整度0.625配合0.84高集中度，是‘主线还在但宽度在收缩’的组合。涨停83、跌停4，情绪尚未破位；真正的拐点在于10板孤峰如何处理。",
                    ],
                    "risks": [
                        "6-9板五个台阶全空，一旦10板孤峰见顶，缺乏中间高度接盘，情绪回落可能是断崖式。",
                    ],
                    "conditions": [
                        "AI算力方向观察沃格光电、博杰股份能否补上4板位置；若龙头始终缺位，则本主线只有轮动价值无高度价值。",
                    ],
                },
            }
        }
    }

    html = generate_dashboard_section(ctx)

    assert "主线抱团 · 高位断层" in html
    assert "赚钱效应高度集中" in html
    assert "6至9板高度断层" in html
    assert "10板孤峰缺少中间梯队承接" in html
    assert "AI综合研判" in html
    assert "中性震荡" not in html


def test_ai_judgement_keeps_business_signal_from_mixed_diagnostic_observation():
    ctx = _context()
    ctx["report_context"] = {
        "observations": {
            "ai": {
                "status": "sanitized",
                "output": {
                    "observations": [
                        "唯一还有主动特征的是上游材料/覆铜板一线:沃格光电、华正新材封在+10%,云南锗业、宝鼎科技、百花医药同样封板。注意这是按个股常识归类的推断,数据未给逐股题材标签。",
                        "第二个误判点:引擎把封在涨停的个股统一标为 broken_positive,会系统性低估主线强度、高估退潮程度。",
                    ],
                    "risks": [
                        "6-9板真空、梯队完整度0.625,龙头与次高之间隔5个台阶,10板股一旦补跌无任何中继承接。",
                        "集中度0.84偏高,单一主线承担了绝大部分溢价,主线一退没有第二条线接得住。",
                    ],
                    "conditions": [
                        "爱丽家居明日是否补跌:直接跌停或-7%以上,视为高度重置确认。",
                    ],
                },
            }
        }
    }

    html = generate_dashboard_section(ctx)

    assert "上游材料占优 · 高位悬空" in html
    assert "上游材料、覆铜板是当前唯一仍有主动特征的方向" in html
    assert "6至9板真空" in html
    assert "10板龙头缺少中继承接" in html
    assert "数据未给逐股题材标签" not in html
    assert "broken_positive" not in html
    assert "中性震荡" not in html


def test_ai_judgement_labels_high_level_split_with_single_mainline_crowding():
    ctx = _context()
    ctx["report_context"] = {
        "observations": {
            "ai": {
                "status": "sanitized",
                "output": {
                    "observations": [
                        "按盘面实际读，今天是高位撕裂而非全面退潮：4~5板层集体走弱且出现跌停，3板层内部一半封板一半崩，说明资金在做同高度换手，不是集体撤离。",
                        "情绪位置判断：涨停83/跌停4，数量维度仍在强区，但先破的是加速段，这是顶部结构初现的顺序，不是情绪冰点的顺序。",
                    ],
                    "risks": [
                        "concentration 0.84是单主线依赖：AI算力龙头一旦断板，无第二主线承接，83家涨停会快速收缩。",
                    ],
                    "conditions": [
                        "半导体材料2板组需至少2家封3板，验证是否形成有效梯队。",
                    ],
                },
            }
        }
    }

    html = generate_dashboard_section(ctx)

    assert "高位撕裂 · 单线抱团" in html
    assert "4至5板层集体走弱" in html
    assert "顶部结构初现" in html
    assert "中性震荡" not in html


def test_ai_judgement_promotes_explicit_mid_retreat_with_height_gap():
    ctx = _context()
    ctx["report_context"] = {
        "observations": {
            "ai": {
                "status": "sanitized",
                "output": {
                    "observations": [
                        "按框架严格判定:今日没有一条符合『主动反弹』的主线——全高度晋级率为0是硬否决,任何分支都缺梯队支撑",
                        "『高度断层』特征完备:10板孤悬、6~9板真空、4板双杀含跌停、3板出现跌停。空间板与中位连板同时被打,是退潮中段而非初期",
                        "集中度0.84在退潮期是负面属性:资金挤在AI算力一条链上,一旦龙头分歧,分支同步失血,没有第二条线接资金",
                    ],
                    "conditions": [
                        "爱丽家居(10板)明日走势是总闸门:一字或早板则空间未死,断板加放量大面则确认退潮尾段,可开始等冰点",
                    ],
                },
            }
        }
    }

    html = generate_dashboard_section(ctx)

    assert "退潮中段 · 高位断层" in html
    assert "全高度晋级率为0" not in html
    assert "10板孤峰且6至9板真空" in html
    assert "AI算力高集中度" in html
    assert "高位承压 · 结构换挡" not in html


def test_observation_mode_labels_retreat_when_ai_describes_height_gap_and_point_activity():
    ctx = _context()
    ctx["report_context"] = {
        "observations": {
            "ai": {
                "status": "sanitized",
                "output": {
                    "observations": [
                        "高度断层是今天的主结构：10板孤悬，6～9板无票，5板破板收绿，4板一只直接跌停——典型退潮期形态。",
                        "83家涨停与低晋级并存，涨停池外普跌，赚钱效应是点状而非面状。",
                    ],
                    "conditions": [
                        "补齐上一交易日结构化快照，重算daily_delta与晋级率。",
                        "爱丽家居次日是否放量出货或直接补跌——10板龙头走坏则整体情绪重定价。",
                    ],
                },
            }
        }
    }

    html = generate_dashboard_section(ctx)

    assert "高位退潮 · 低位试错" in html
    assert "高位梯队断层并出现明显负反馈" in html
    assert "赚钱效应偏点状" in html
    assert "明日验证：爱丽家居次日是否放量出货或直接补跌" in html
    assert "补齐上一交易日结构化快照" not in html


def test_embedded_dashboard_omits_redundant_market_stance_card():
    ctx = _context()
    ctx["stance"] = {
        "stance": "中性震荡",
        "head": "A/D 回暖，但高位梯队仍有断层，延续性需要继续确认。",
        "ad_series": [0.92, 1.01, 1.08],
        "zt": 83,
        "dt": 4,
        "max_h": 10,
        "triggers": [
            {
                "name": "右侧转强",
                "cond": "A/D 连续2日≥1.05且梯队不断档",
                "hit": True,
            },
            {
                "name": "高位退潮",
                "cond": "断板与跌停同步增加",
                "hit": False,
            },
        ],
        "play": "加仓参与主线。",
    }

    html = generate_dashboard_section(ctx)

    assert "判断依据" not in html
    assert "A/D 回暖，但高位梯队仍有断层" not in html
    assert "近3日 A/D" not in html
    assert "转向条件" not in html
    assert "涨跌停" in html
    assert "空间结构" in html
    assert "加仓参与主线" not in html


def test_degraded_quality_uses_one_line_business_hint_without_internal_diagnostics():
    html = generate_dashboard_html(_context())

    assert "昨日逐股反馈待补齐，以今日事实为准。" in html
    assert "数据降级" not in html
    assert "run_id" not in html
    assert "来源与异常" not in html


def test_quality_public_hint_targets_known_limited_modules():
    quality = {
        "status": "degraded",
        "publication_mode": "observation",
        "missing_fields": ["previous_limit_pool_snapshot"],
        "decision_degraded": ["daily_delta"],
    }

    assert _quality_public_hint(quality, {"publication_mode": "observation"}, "observation") == (
        "昨日逐股反馈待补齐，以今日事实为准。"
    )


def test_ok_quality_has_no_public_hint():
    quality = {
        "status": "ok",
        "publication_mode": "decision",
        "market_scope": "沪深北全A",
        "market_total": 2,
        "market_covered": 2,
    }

    html = _quality_html({"data_quality": quality, "market_state": {"publication_mode": "decision"}})

    assert "数据已更新" in html
    assert "quality-hint" not in html


def test_legacy_quality_metadata_combines_gate_names_and_limit_source():
    import legacy_tracker

    quality = QualityReport("2026-08-05", [
        QualityIssue("warning", "sample", "warning note"),
    ])
    limit_result = FetchResult.partial(
        dataset="limit_pool", date="2026-08-05", source="ZT:akshare_em|CHECK:ZT:eastmoney_push2ex",
        expected_count=90, actual_count=90, message="count drift",
    )
    names = NameResolution(
        names={"sz003032": "传智教育"}, sources={"sz003032": "limit_pool"},
        conflicts=[{"code": "sz003032"}],
    )

    result = legacy_tracker._report_quality_metadata(quality, names, limit_result)

    assert result["ok"] is False
    assert result["name_conflicts"] == 1
    assert result["limit_pool_status"] == "partial"
    assert "count drift" in result["notes"]


def test_full_report_collapses_detailed_research_layer(monkeypatch, tmp_path):
    import legacy_tracker
    import pandas as pd

    output = tmp_path / "report.html"
    monkeypatch.setattr(legacy_tracker, "OUTPUT_HTML", str(output))
    empty = pd.DataFrame()
    legacy_tracker.generate_html(
        ml_strength=empty, sub_strength=empty, ml_ma={}, sub_ma={},
        ml_thresh={}, sub_thresh={}, leaders={}, dates=["20260805"],
        ratings={}, sub_ratings={}, echelon=[], top30_data={},
        advance_decline={"up": 2500, "down": 2500, "zt": 0, "dt": 0},
        sentiment_df=empty, classified_df=empty, price_df=empty,
        data_quality={"ok": True, "name_conflicts": 0},
    )

    html = output.read_text(encoding="utf-8")
    assert '<details class="research-layer">' in html
    assert "展开研究证据" in html
    assert "min-width:420px" not in html
    assert ".research-body {" in html


def test_full_report_uses_same_limit_up_count_as_report_fact_snapshot(monkeypatch, tmp_path):
    """旧行情汇总不能覆盖同日报告不可变事实池的涨停总数。"""
    import 主线强度追踪 as legacy_tracker
    import pandas as pd

    output = tmp_path / "report.html"
    monkeypatch.setattr(legacy_tracker, "OUTPUT_HTML", str(output))
    empty = pd.DataFrame()
    report_context = {
        "publication_mode": "observation",
        "quality": {"status": "degraded", "publication_mode": "observation"},
        "facts": {
            "market_state": {
                "publication_mode": "observation",
                "title": "中性震荡",
                "allow_strong_conclusion": False,
            },
            "market_snapshot": {
                "report_date": "2026-08-07",
                "limit_up": 83,
                "limit_down": 4,
            },
        },
    }

    legacy_tracker.generate_html(
        ml_strength=empty, sub_strength=empty, ml_ma={}, sub_ma={},
        ml_thresh={}, sub_thresh={}, leaders={}, dates=["20260807"],
        ratings={}, sub_ratings={}, echelon=[], top30_data={},
        advance_decline={"up": 2800, "down": 2500, "zt": 74, "dt": 4},
        sentiment_df=empty, classified_df=empty, price_df=empty,
        market_state=report_context["facts"]["market_state"],
        report_context=report_context,
    )

    html = output.read_text(encoding="utf-8")
    assert "涨停 83 / 跌停 4" in html
    assert '<div class="value">83家</div>' in html
    assert "涨停 74 / 跌停 4" not in html


def test_facts_only_report_keeps_confirmed_micro_cycle_without_full_phase_analysis(monkeypatch, tmp_path):
    import 主线强度追踪 as report
    import phase_resonance
    import pandas as pd

    output = tmp_path / "report.html"
    monkeypatch.setattr(report, "OUTPUT_HTML", str(output))
    monkeypatch.setattr(
        phase_resonance,
        "build_phase_resonance",
        lambda: {
            "micro_cycle": {
                "status": "小周期主升",
                "signal_date": "2026-08-04",
                "confirmation_date": "2026-08-05",
                "full_confirmation_date": "2026-08-06",
                "signal_return": 3.08,
                "rising_days": 4,
                "events": {"final_stop": {"date": "2026-07-20", "low": 3741.11}},
            },
            "micro_chain": {"usable": False, "hint": "历史事实不足"},
            "micro_resonance": {
                "strong_industries": [],
                "mainlines": [{
                    "name": "AI应用",
                    "level": "连板跟随",
                    "chain_count": 2,
                    "chain_total": 7,
                    "industry_evidence": [],
                    "leaders": [],
                }],
                "attribution_coverage": 1.0,
                "leader_coverage": 1.0,
                "unattributed_count": 0,
            },
        },
    )
    monkeypatch.setattr(
        phase_resonance,
        "render_phase_resonance_html",
        lambda _data: "<div>完整阶段分析不应发布</div>",
    )
    empty = pd.DataFrame()
    report_context = {
        "publication_mode": "facts_only",
        "quality": {"status": "insufficient", "publication_mode": "facts_only"},
        "facts": {
            "market_state": {
                "publication_mode": "facts_only",
                "title": "数据待核验",
                "allow_strong_conclusion": False,
            },
            "market_snapshot": {
                "report_date": "2026-08-07",
                "limit_up": 83,
                "limit_down": 4,
            },
        },
    }

    report.generate_html(
        ml_strength=empty, sub_strength=empty, ml_ma={}, sub_ma={},
        ml_thresh={}, sub_thresh={}, leaders={}, dates=["20260807"],
        ratings={}, sub_ratings={}, echelon=[], top30_data={},
        advance_decline={"up": 1576, "down": 1446, "zt": 83, "dt": 4},
        sentiment_df=empty, classified_df=empty, price_df=empty,
        market_state=report_context["facts"]["market_state"],
        report_context=report_context,
    )

    html = output.read_text(encoding="utf-8")
    assert "短周期结构" in html
    assert "小周期主升" in html
    assert "连板跟随" in html
    assert "完整阶段分析不应发布" not in html


def test_full_report_sentiment_chart_can_shrink_on_mobile(monkeypatch, tmp_path):
    import 主线强度追踪 as report
    import phase_resonance
    import pandas as pd

    output = tmp_path / "report.html"
    monkeypatch.setattr(report, "OUTPUT_HTML", str(output))
    monkeypatch.setenv("AI_ENABLE", "0")
    monkeypatch.setattr(phase_resonance, "build_phase_resonance", lambda: {})
    monkeypatch.setattr(phase_resonance, "render_phase_resonance_html", lambda _data: "")
    empty = pd.DataFrame()
    sentiment = pd.DataFrame({
        "日期": ["20260805", "20260806", "20260807"],
        "ad_mood": [48.0, 51.0, 52.8],
        "up": [2400, 2600, 2800],
        "down": [2700, 2500, 2300],
    })

    report.generate_html(
        ml_strength=empty, sub_strength=empty, ml_ma={}, sub_ma={},
        ml_thresh={}, sub_thresh={}, leaders={}, dates=["20260807"],
        ratings={}, sub_ratings={}, echelon=[], top30_data={},
        advance_decline={"up": 2800, "down": 2500, "zt": 83, "dt": 4},
        sentiment_df=sentiment, classified_df=empty, price_df=empty,
        wc_data={
            "hot_stock_b64": "data:image/png;base64,eA==",
            "plate_b64": "data:image/png;base64,eA==",
        },
    )

    html = output.read_text(encoding="utf-8")
    assert 'id="sentimentChart" style="height:350px;flex:1 1 600px;min-width:0;"' in html
    assert 'id="sentimentChart" style="height:350px;flex:1;min-width:600px;"' not in html
    assert "flex:1;min-width:380px" not in html
    assert html.count("flex:1 1 380px;min-width:0") == 2


def test_phase_timeline_contains_its_wide_table_on_mobile():
    from phase_resonance import _phase_timeline

    html = _phase_timeline({
        "det": {"phases": {"最新日": ("2026-08-06", "2026-08-07")}},
        "breadth": {"最新日": {"median": 0.11, "win": 52.0}},
        "phase_names": ["最新日"],
        "index_ret": {"最新日": 1.02},
    })

    assert 'class="phase-timeline-wrap"' in html
    assert "max-width:100%;overflow-x:auto" in html


def test_phase_turning_summary_renders_current_stage_and_independent_leader_lists():
    from phase_resonance import _turning_summary_html

    html = _turning_summary_html({
        "current_phase": {
            "label": "箱体突破",
            "detail": "箱体 3768~3941，振幅 4.6%，收在上沿",
            "turning_date": "2026-07-17",
            "latest_date": "2026-08-07",
            "index_return": 4.67,
            "trading_days": 16,
        },
        "turning_leaders": {
            "sectors": [
                {"name": "贵金属", "return": 35.09},
                {"name": "教育", "return": 25.56},
                {"name": "能源金属", "return": 17.38},
            ],
            "stocks": [
                {"code": "sh603221", "name": "爱丽家居", "return": 140.59, "st": False},
                {"code": "sz003032", "name": "传智教育", "return": 102.63, "st": False},
            ],
            "stock_hint": "",
        },
    })

    for token in (
        "当前阶段", "箱体突破", "主要转折", "2026-07-17", "转折以来领涨板块",
        "贵金属", "+35.1%", "转折以来领涨个股", "爱丽家居", "传智教育",
    ):
        assert token in html
    assert "强制归因" not in html
    assert "*ST传智" not in html
    assert "phase-turning-grid" in html


def test_phase_turning_summary_hides_empty_rankings_and_keeps_small_coverage_hint():
    from phase_resonance import _turning_summary_html

    html = _turning_summary_html({
        "current_phase": {
            "label": "二次探底", "detail": "", "turning_date": "2026-07-17",
            "latest_date": "2026-08-07", "index_return": -1.2, "trading_days": 16,
        },
        "turning_leaders": {"sectors": [], "stocks": [], "stock_hint": "区间个股覆盖不足"},
    })

    assert "当前阶段" in html
    assert "转折以来领涨板块" not in html
    assert "转折以来领涨个股" not in html
    assert "区间个股覆盖不足" in html
    assert "None" not in html
    assert "nan" not in html.lower()


def test_micro_cycle_template_renders_events_strong_industries_and_mainline_leaders():
    from phase_resonance import _micro_cycle_html

    html = _micro_cycle_html({
        "micro_cycle": {
            "status": "小周期主升", "signal_date": "2026-08-04",
            "confirmation_date": "2026-08-05", "full_confirmation_date": "2026-08-06",
            "signal_return": 3.08, "rising_days": 4, "signal_basis": "price+limit_pool",
            "events": {
                "final_stop": {"date": "2026-07-20", "low": 3741.11},
                "rebound_high": {"high_date": "2026-07-22", "close_date": "2026-07-23"},
                "secondary_bottom": {"date": "2026-07-30", "low": 3767.50, "higher_low": True},
                "retest": {"date": "2026-08-03", "close": 3809.66},
            },
        },
        "micro_chain": {"usable": True, "consecutive_days": 4, "rows": [{"code": "sz002552", "name": "宝鼎科技"}], "hint": "历史事实交集"},
        "micro_resonance": {
            "strong_industries": [{"name": "电子化学品", "return": 17.14}, {"name": "贵金属", "return": 15.13}],
            "mainlines": [{
                "name": "AI算力", "level": "核心共振", "chain_count": 4, "chain_total": 7,
                "industry_evidence": ["电子化学品", "元件", "半导体"],
                "leaders": [{"code": "sz002552", "name": "宝鼎科技", "return": 26.77}],
            }],
            "attribution_coverage": 1.0, "leader_coverage": 1.0, "unattributed_count": 0,
        },
    })

    for token in (
        "短周期结构", "小周期主升", "7/20", "7/22-23", "7/30", "8/4",
        "转强信号", "8/5", "突破确认", "强行业", "电子化学品", "共振主线",
        "核心共振", "AI算力", "板块领涨个股", "宝鼎科技", "+26.8%",
    ):
        assert token in html
    assert "贵金属" in html
    assert "贵金属</strong>" not in html
    assert "micro-cycle-timeline" in html


def test_micro_cycle_template_hides_empty_evidence_headings_and_keeps_small_hint():
    from phase_resonance import _micro_cycle_html

    html = _micro_cycle_html({
        "micro_cycle": {
            "status": "震荡筑底", "signal_date": "", "confirmation_date": "",
            "full_confirmation_date": "", "signal_return": None, "rising_days": 0,
            "events": {"final_stop": {"date": "2026-07-20", "low": 3741.11}},
        },
        "micro_chain": {"usable": False, "hint": "历史事实不足"},
        "micro_resonance": {},
    })

    assert "短周期结构" in html
    assert "历史事实不足" in html
    assert "强行业" not in html
    assert "共振主线" not in html
    assert "None" not in html
    assert "nan" not in html.lower()


def test_dashboard_does_not_render_static_probability_claims():
    html = generate_dashboard_html(_context())

    forbidden = (
        "胜率 <40%", "次日崩塌概率 39%", "2板仅 33%", "晋级率 45-50%",
        "周四高潮易引周五崩(56%)", "崩塌 35%", "周一 66% 概率反弹",
        "历史 T+3 破新高 55%", "胜率均为经验概率",
    )
    assert not [phrase for phrase in forbidden if phrase in html]


def test_outcome_rate_is_not_labeled_as_next_day_scenario_probability():
    ctx = _context()
    ctx["scenario_stats"] = {
        "breakout": {"sample_count": 80, "win_rate": 0.3, "horizon": 3, "min_samples": 10},
        "continuation": {"sample_count": 10, "win_rate": 0.7, "horizon": 3, "min_samples": 10},
    }

    html = generate_dashboard_html(ctx)

    assert "明日基准情形" not in html
    assert "基准情形已高亮" not in html
    assert "盘面判断" in html
    assert "明日验证路径" not in html


def test_observation_mode_removes_generic_scenarios_and_uses_direct_plan_copy():
    html = generate_dashboard_html(_context())
    embedded = generate_dashboard_section(_context())

    for page in (html, embedded):
        assert "观察次高梯队是否快速晋级，以确认主线延续" not in page
        assert "三因子共振" not in page
        assert "顶部崩塌预警" not in page
        assert "等待验证信号" not in page
        assert "按条件确认强弱变化" not in page
        assert "建议仓位" in page
        assert "今日无合格标的，不开新仓" in page
