# -*- coding: utf-8 -*-
"""抱团周期兼容入口：实时判断统一使用价格研究引擎，历史案例仅作回顾。

保留构建、入口卡、HTML与导出接口；不将固定案例价格、主观评分或
事后赢家外推为当前信号。历史显示边界是推定值，不是事前发布证据。
"""
from __future__ import annotations

import os
import sys
from html import escape
from datetime import datetime
from typing import Any, Dict, List, Optional
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from paths import OUTPUT_DIR, SITE_DIR

def _norm_date(s: Any) -> str:
    if s is None:
        return ''
    return str(s).replace('-', '').replace('/', '').strip()[:8]

def _fmt_date(s: Any) -> str:
    d = _norm_date(s)
    if len(d) == 8:
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return str(s)

def _e(val: Any) -> str:
    return escape(str(val)) if val is not None else ''

BAOTUAN_HISTORICAL_CYCLES: List[Dict[str, Any]] = [
    {
        "id": "cycle_medicine_collapse",
        "name": "周期零：老医药高标抱团退潮与风险出清",
        "date_range": "2026.08.15 - 2026.08.30",
        "start_date": "20260815",
        "end_date": "20260830",
        "theme_name": "医药/微盘老抱团",
        "style_desc": "纯游资高标连板情绪投机 ➔ 见顶核按钮退潮",
        "core_stage": "老抱团瓦解出清期",
        "stage_badge": "出清终结",
        "stage_color": "#ef4444",
        "anchor_event": "深中华断板，神奇跌停，医药全线出清",
        "summary": "上一轮高位投机抱团彻底瓦解，老龙头神话破灭，核按钮频现，市场进入流动性出清尾声，为新周期孕育腾出资金空间。",
        "succession_chains": [
            {
                "track": "医药老高标",
                "nodes": [
                    {"name": "深中华", "role": "空间锚点", "desc": "高标见顶断板，开启全市场退潮"},
                    {"name": "神奇制药", "role": "退潮中军", "desc": "连续跌停，标志老抱团逻辑破灭"},
                    {"name": "百花医药", "role": "后排跟风", "desc": "补跌出清，筹码彻底踩踏"}
                ]
            }
        ],
        "trial_sectors": [],
        "discarded_sectors": [],
        "takeaway": "老抱团不彻底出清，资金绝不敢发动新周期。深中华与神奇制药的彻底跌停，是风险释放完毕的右侧信号。"
    },
    {
        "id": "cycle_consumer_agri",
        "name": "周期一：消费与农业双主线抱团 · 梯队接力周期",
        "date_range": "2026.08.31 - 2026.09.15",
        "start_date": "20260831",
        "end_date": "20260915",
        "theme_name": "大消费（零售/旅游） + 农业种植",
        "style_desc": "短线情绪投机连板，龙一断板后同题材梯队补涨接力",
        "core_stage": "高位分歧 ➔ 梯队接力 ➔ 彻底出清",
        "stage_badge": "接力与出清",
        "stage_color": "#f59e0b",
        "anchor_event": "8.31启动 ➔ 9.4国芳断板 ➔ 9.10百大断板 ➔ 9.15金健国芳出清",
        "summary": "深中华与神奇出清后，资金在8.31发动消费与农业。国芳集团走出5板确立主线。9.4国芳断板后并未立刻退潮，资金火速接力龙二百大集团与桂林旅游；同时农业万向德农接力亚盛集团、金健米业。9.15金健国芳彻底出清，宣告本周期闭环。",
        "succession_chains": [
            {
                "track": "消费主线接力链",
                "nodes": [
                    {"name": "国芳集团", "role": "龙一领跑 (5板)", "desc": "8.28-9.03 五连板奠定消费抱团基石，9.4 断板"},
                    {"name": "百大集团", "role": "龙二接力 (5板)", "desc": "9.04 国芳断板当天秒板接力，9.07-9.09 连板加速，9.10 断板"},
                    {"name": "桂林旅游", "role": "低位补涨 (4板)", "desc": "9.07-9.10 四连板承接百大分歧，9.11 见顶结束"}
                ]
            },
            {
                "track": "农业主线接力链",
                "nodes": [
                    {"name": "万向德农", "role": "领跑高标 (6板)", "desc": "农业空间龙头，8.25-9.01 连续向上拉升突破高度压制"},
                    {"name": "亚盛集团", "role": "中位助攻 (4板)", "desc": "9.03-9.08 承接万向分歧助攻涨停"},
                    {"name": "金健米业", "role": "尾声出清", "desc": "低位防御补涨，9.15 彻底破位出清"}
                ]
            },
            {
                "track": "伴生防御与异动分支",
                "nodes": [
                    {"name": "楚天龙", "role": "数字经济", "desc": "独立异动，缺乏板块助攻，未能形成主抱团"},
                    {"name": "闽东电力", "role": "电力先锋 (9.10起)", "desc": "消费高标断板期间暗中蓄势，为9.16大周期埋下伏笔"}
                ]
            }
        ],
        "trial_sectors": [
            {"sector": "消费零售", "lead_stocks": ["国芳集团", "百大集团"], "result": "✅ 晋级为主抱团核心主线"},
            {"sector": "农业种植", "lead_stocks": ["万向德农", "亚盛集团"], "result": "✅ 晋级为并列抱团副线"},
            {"sector": "数字经济", "lead_stocks": ["楚天龙"], "result": "⚠️ 单兵突击，跟风不足，仅作阶段性轮动"}
        ],
        "discarded_sectors": [
            {
                "sector": "科技类 (AI应用 / 算力液冷 / PCB / 芯片)",
                "killed_stocks": [
                    "AI应用: 龙版传媒、芒果超媒",
                    "算力液冷: 海鸥股份 -> 集泰股份，康盛股份 -> 远东股份",
                    "PCB: 超声电子、崇达技术、风华高科",
                    "芯片/CPO: 阶段性冲高回落"
                ],
                "verdict": "❌ 彻底判死刑 (假反弹 / 抽血诱多)",
                "reason": "科技处于前期大周期退潮的阴跌通道，高位堆积海量套牢盘。期间任何脉冲均为假反抽与诱多出逃，资金无法形成抱团合力，伸手必吃大面。"
            }
        ],
        "takeaway": "龙一断板绝不是行情的瞬间休克，只要老抱团未彻底出清，资金会在同板块内寻找龙二与低位补涨接力。直到所有补涨龙均断板出清（9.15金健国芳出清），该周期才真正结束。"
    },
    {
        "id": "cycle_power_subnew_trend",
        "name": "周期二：9.16大阳线与电力次新趋势共振周期",
        "date_range": "2026.09.16 - 2026.09.27",
        "start_date": "20260916",
        "end_date": "20260927",
        "theme_name": "电力能源 + 次新成长 + 电子制造",
        "style_desc": "风格深刻切换: 纯投机连板 ➔ 投机与趋势共振、大阳线放量扩散",
        "core_stage": "共振启动 ➔ 主升抱团 ➔ 震荡分歧",
        "stage_badge": "趋势共振",
        "stage_color": "#10b981",
        "anchor_event": "9.16放量大阳线 (93家涨停)，闽东电力6板突破高度压制，沈鼓集团次新共振",
        "summary": "金健国芳出清后，9.16全市场迎来放量大阳线共振。风格从单纯超短投机扩展到趋势成长。闽东电力以6连板引爆电力绿电；次新沈鼓集团引领高端装备；澳弘电子、华瓷股份接力主升。市场赚钱效应扩散，形成健康抱团格局。",
        "succession_chains": [
            {
                "track": "电力新能源",
                "nodes": [
                    {"name": "闽东电力", "role": "空间总龙 (6板)", "desc": "9.10试错启动，9.16 顶着大阳线晋级6板确立新周期"},
                    {"name": "金辰股份", "role": "新能源中军", "desc": "趋势向上推升，助攻核心资产"}
                ]
            },
            {
                "track": "次新与高端装备",
                "nodes": [
                    {"name": "沈鼓集团", "role": "9.17次新先锋", "desc": "承接大阳线情绪，次新属性吸纳增量资金"},
                    {"name": "澳弘电子", "role": "硬件趋势 (5板)", "desc": "电子制造补强，穿越分歧"}
                ]
            }
        ],
        "trial_sectors": [
            {"sector": "电力绿电", "lead_stocks": ["闽东电力"], "result": "✅ 大阳线共振总龙头"},
            {"sector": "次新制造", "lead_stocks": ["沈鼓集团", "华瓷股份"], "result": "✅ 风格切换后的重要增量载体"}
        ],
        "discarded_sectors": [
            {
                "sector": "科技老套牢盘",
                "killed_stocks": ["前期超跌科技小票"],
                "verdict": "❌ 依然处于死刑期",
                "reason": "大阳线增量优先流入与大盘共振的新能源、次新与电力，老科技反抽无持续性。"
            }
        ],
        "takeaway": "当老抱团彻底出清后，一旦出现指数大阳线共振，新周期将迅速从试错期跃升至主升期，此时应果断放弃老方向，重拳出击共振新主线！"
    },
    {
        "id": "cycle_current_chaos_trial",
        "name": "周期三：9.28老抱团出清 · 新周期混沌多点试错期",
        "date_range": "2026.09.28 - 2026.09.30",
        "start_date": "20260928",
        "end_date": "20260930",
        "theme_name": "锂电储能 / 化工染料 / 生物医药 / 混沌多点试错",
        "style_desc": "老抱团出清完成，资金多点点火试水，环境低迷看不清，观察等待市场转暖",
        "core_stage": "瓦解出清后的新周期混沌试错期",
        "stage_badge": "混沌试错 (等待转暖)",
        "stage_color": "#8b5cf6",
        "anchor_event": "9.28老抱团出清时，就可以开始试错新周期了。但目前环境太差，什么能走出来依旧看不清。",
        "summary": "9.28日前期老抱团出清完毕，敏锐资金立即启动新周期试错。锂电(时代万恒、尚水智能)、化工染料(善水科技)、医药(近岸蛋白)等多点点火。然而由于当前大盘量能萎缩、环境太差，主线尚未凝聚共识。战术上保持防守观察，等市场转暖、资金想好进攻什么再跟随也不迟！",
        "succession_chains": [
            {
                "track": "9.28 新周期试错先锋池",
                "nodes": [
                    {"name": "时代万恒", "role": "锂电探路", "desc": "首板涨停，试探新能源超跌反弹与储能抱团可能"},
                    {"name": "尚水智能", "role": "锂电设备", "desc": "日内助攻，观察是否形成板块梯队"},
                    {"name": "善水科技", "role": "化工(染料)", "desc": "首板试错，化工原料周期品异动"},
                    {"name": "近岸蛋白", "role": "生物医药", "desc": "底部探路，医药方向多点试水"}
                ]
            }
        ],
        "trial_sectors": [
            {"sector": "锂电储能", "lead_stocks": ["时代万恒", "尚水智能"], "result": "🔍 混沌试错中 (等待板块梯队是否建立)"},
            {"sector": "化工 (染料)", "lead_stocks": ["善水科技"], "result": "🔍 混沌试错中 (独立首板，观察持续性)"},
            {"sector": "生物医药", "lead_stocks": ["近岸蛋白"], "result": "🔍 混沌试错中 (超跌探路，等待行业政策催化)"}
        ],
        "discarded_sectors": [
            {
                "sector": "科技类 (AI/芯片/CPO/PCB)",
                "killed_stocks": ["科技全分支"],
                "verdict": "❌ 半个月前已被判死刑",
                "reason": "半个月前早已明确判死刑！套牢盘极为沉重，在当前存量缩量甚至环境极差的背景下，绝对没有能力支撑科技发动新抱团，切勿接力！"
            }
        ],
        "takeaway": "9.28日老抱团出清时，就可以开始试错新周期了，但目前环境太差，什么能走出来依旧看不清。准备等市场转暖，资金想好进攻什么再跟随也不迟！"
    },
    {
        "id": "cycle_october_bifurcation",
        "name": "周期四：节后去伪存真 · 善水科技20cm成妖与抱团极致分化 (最新)",
        "date_range": "2026.10.08 - 至今",
        "start_date": "20261008",
        "end_date": "20261013",
        "theme_name": "精细化工染料(善水科技) / 重组传媒(新华传媒) / 锂电(时代万恒) / 独立妖股抱团",
        "style_desc": "去伪存真与极度分化: 善水科技20cm翻倍成妖(+136%)，新华传媒10板空间总龙，近岸蛋白试错失败破位(-15%)，中位核按钮与科技权重抽血",
        "core_stage": "试错确立 ➔ 抱团极致分化与高位巨震期",
        "stage_badge": "抱团极致分化 (去伪存真)",
        "stage_color": "#f59e0b",
        "anchor_event": "善水科技狂飙翻倍逼近50元大关，新华传媒10连板顶格压制，时代万恒6板放量34%，近岸蛋白跌破支撑淘汰出清",
        "summary": "9.28试错先锋在节后经历生死大考：善水科技凭借精细化工染料中间体涨价与独立资金抱团，走出3连板20cm及节后趋势逼空，从20.79元狂飙至49.20元，涨幅+136.6%成为创业板第一妖王；新华传媒晋级10板空间总龙；时代万恒走出6板后在10.13遭遇33.9%天量换手分歧。与之形成鲜明对比的是，医药先锋近岸蛋白由于板块无梯队助攻且行业整体回调，节后连续下挫破位（160.39元跌至137.16元，-14.5%），试错宣告失败。盘面呈现'冰火两重天、赢家通吃、弃弱抱强'的极致结构特征。",
        "succession_chains": [
            {
                "track": "创业板20cm独立妖王抱团链",
                "nodes": [
                    {"name": "善水科技 (301190)", "role": "9.28试错首板 (24.95)", "desc": "化工染料中间体涨价驱动，资金多点点火试探"},
                    {"name": "善水科技 (301190)", "role": "9.29-9.30 三连板 (35.93)", "desc": "节前资金锁仓加速，晋级创业板20cm高度龙头"},
                    {"name": "善水科技 (301190)", "role": "10.08-10.12 趋势主升 (48.45)", "desc": "节后无视大盘科技下杀，连阳逼空穿越分歧"},
                    {"name": "善水科技 (301190)", "role": "10.13 高位放量震荡 (49.20)", "desc": "冲高49.90元逼近50大关，换手11.2%，多空进入高位大分歧"}
                ]
            },
            {
                "track": "主板连板空间与分歧接力链",
                "nodes": [
                    {"name": "新华传媒 (600825)", "role": "空间总龙 (10连板)", "desc": "重组+文化传媒，一字加速打出全市场高度天花板"},
                    {"name": "时代万恒 (600241)", "role": "锂电高标 (6连板)", "desc": "9.28探路起步走出6板，10.13在13.85遭遇天量换手分歧(换手34%)"},
                    {"name": "九阳股份 (002242)", "role": "消费接力 (6连板)", "desc": "承接高位分歧流动性，10.13盘中放量冲高+6.19%"}
                ]
            },
            {
                "track": "试错失败与淘汰出清链 (反面警示)",
                "nodes": [
                    {"name": "近岸蛋白 (688137)", "role": "9.28超跌试错", "desc": "生物医药底部试水，9.30最高冲至160.39元"},
                    {"name": "近岸蛋白 (688137)", "role": "节后破位淘汰", "desc": "医药板块全线回调(易明医药、津药药业大跌)，缺乏梯队，单边跌破支撑"},
                    {"name": "近岸蛋白 (688137)", "role": "10.13加速下探 (137.16)", "desc": "跌-6.67%创节后新低，试错彻底宣告失败，落入淘汰出清通道"}
                ]
            }
        ],
        "trial_sectors": [
            {"sector": "化工 (染料/中间体)", "lead_stocks": ["善水科技 (301190)"], "result": "🏆 晋级成功！从9.28首板飙升至49.20(+136%)，创业板独立抱团第一妖王"},
            {"sector": "并购重组 / 传媒", "lead_stocks": ["新华传媒 (600825)"], "result": "👑 空间总龙！10连板压制全场，打开投机高度天花板"},
            {"sector": "锂电储能", "lead_stocks": ["时代万恒 (600241)"], "result": "⚠️ 6板见顶大分歧，换手33.94%，注意高位震荡与接力风险"},
            {"sector": "生物医药", "lead_stocks": ["近岸蛋白 (688137)"], "result": "❌ 试错失败！无梯队助攻，节后连续单边破位退潮(-15%)"}
        ],
        "discarded_sectors": [
            {
                "sector": "大容量科技成长 (CPO / 算力液冷 / 半导体)",
                "killed_stocks": [
                    "CPO光模块: 中际旭创、新易盛、天孚通信",
                    "算力硬件: 工业富联、浪潮信息",
                    "半导体芯片: 阶段性冲高反杀"
                ],
                "verdict": "❌ 坚决判死刑 (大盘最大抽血重灾区)",
                "reason": "10.12-10.13中际旭创、工业富联等科技权重单日净流出超300亿，拖累创业板指单日大跌-2.32%。科技老套牢盘泰山压顶，机构持续减仓兑现，绝不可伸手接飞刀！"
            },
            {
                "sector": "弱势试错医药假反弹",
                "killed_stocks": [
                    "科创板原料: 近岸蛋白 (688137)",
                    "中位医药: 易明医药 (-4.69%)、津药药业 (跌停)"
                ],
                "verdict": "❌ 试错淘汰出清 (严防补跌)",
                "reason": "医药无持续性政策刺激与大资金板块性介入，个股超跌脉冲仅为跟风试水，一旦大盘走弱资金迅速弃弱留强，导致近岸蛋白等破位杀跌。"
            }
        ],
        "takeaway": "试错期结束后的关键法则：‘去伪存真、弃弱抱强’！善水科技之所以能走出+136%大妖行情，是因为其在首板后迅速以20cm连板凝聚了全市场投机合力；而近岸蛋白缺乏板块助攻，冲高后迅速沦为弃子。面对妖股（善水科技、时代万恒）高位巨震应以防守锁利为主，对破位弱股（近岸蛋白）绝不抱有幻想抢反弹！"
    },
    {
        "id": "cycle_october_reconstruction",
        "name": "周期五：退潮出清后的新一轮低位重构与空间试错期 (最新)",
        "date_range": "2026.10.14 - 至今",
        "start_date": "20261014",
        "end_date": "20261020",
        "theme_name": "机器人(16板) / AI算力与半导体(11板) / 汽车零部件 / 吉大正元空间试错",
        "style_desc": "高位退潮出清与低位梯队重构: 老周期高标退潮出清完毕，跌停收窄至6家；吉大正元4板打破空间压制，长春一东/东安动力等3板梯队扩容，大容量中军涨停支撑指数",
        "core_stage": "退潮出清 ➔ 低位重构与空间试错期",
        "stage_badge": "低位重构试错",
        "stage_color": "#3fb950",
        "anchor_event": "跌停家数收窄至6家，吉大正元晋级4板成为全市场最高空间标杆，机器人16家涨停引爆低位集群，大容量中军(瑞芯微、九阳股份、移远通信)放量涨停支撑指数",
        "summary": "10.13高位妖股天量大分歧后，市场经历充分退潮释放，跌停家数由高位大幅收敛至6家。吉大正元以4连板打破空间压制，成为全市场唯一连板空间标杆；中位梯队健康展开，长春一东、东安动力、大业股份、京城股份、海洋王等5只标的晋级3板；大容量中军瑞芯微(38.3亿)、九阳股份(23.9亿)、移远通信(21.9亿)强势涨停，机器人与AI算力形成强大低位做多合力。盘面进入新一轮低位试错与主线重构阶段。",
        "succession_chains": [
            {
                "track": "主板连板空间破局链",
                "nodes": [
                    {"name": "吉大正元 (003022)", "role": "空间总龙 (4连板)", "desc": "突破前期高位压制，晋级4板成为全市场唯一空间天花板标杆"},
                    {"name": "长春一东 (600148)", "role": "中位晋级 (3连板)", "desc": "军工+汽车零部件，放量换手晋级3板领跑梯队"},
                    {"name": "东安动力 (600178)", "role": "中位晋级 (3连板)", "desc": "汽车零部件身位卡位，3进4候选标的"}
                ]
            },
            {
                "track": "大容量主线中军与硬件科技链",
                "nodes": [
                    {"name": "瑞芯微 (603893)", "role": "芯片半导体中军", "desc": "放量成交38.3亿封死涨停，引领硬科技多头中枢回升"},
                    {"name": "九阳股份 (002242)", "role": "消费白马容量", "desc": "成交23.9亿换手涨停，承接大资金做多动能"},
                    {"name": "移远通信 (603236)", "role": "算力通信中军", "desc": "成交21.9亿强势涨停，助攻AI硬件修复"}
                ]
            },
            {
                "track": "高位断板退潮出清链 (风险警戒)",
                "nodes": [
                    {"name": "东方嘉盛 (002889)", "role": "3进4落败断板", "desc": "昨日3板同身位竞争落败，放量重挫-6.67%，资金踩踏出逃"},
                    {"name": "万丰奥威 (002085)", "role": "低空断板", "desc": "冲高回落跌-4.54%，高位断板负反馈明显"},
                    {"name": "老周期龙头", "role": "新华传媒/时代万恒", "desc": "老周期赶顶结束后步入退潮震荡，严禁盲目追高老题材"}
                ]
            }
        ],
        "trial_sectors": [
            {"sector": "机器人 / 自动化", "lead_stocks": ["嘉晨智能", "雷赛智能", "大业股份"], "result": "🤖 爆发领跑！日内16家涨停，低位最强集群进攻"},
            {"sector": "空间接力 / 连板破局", "lead_stocks": ["吉大正元 (003022)"], "result": "👑 空间标杆！4连板破局，锚定市场短线高度"},
            {"sector": "半导体 / AI算力中军", "lead_stocks": ["瑞芯微 (603893)", "移远通信 (603236)"], "result": "💻 容量托底！大成交涨停，多头中枢企稳回升"},
            {"sector": "汽车零部件 / 中位接力", "lead_stocks": ["长春一东 (600148)", "东安动力 (600178)"], "result": "🚗 梯队有序！3板扩容至5家，形成健康承接"}
        ],
        "discarded_sectors": [
            {
                "sector": "高位断板与同身位落败股",
                "killed_stocks": ["东方嘉盛 (-6.67%)", "万丰奥威 (-4.54%)"],
                "verdict": "❌ 坚决回避 (断板踩踏)",
                "reason": "退潮期高位断板标的面临剧烈筹码分歧与获利盘砸盘，严禁盲目抄底抢反抽！"
            },
            {
                "sector": "老周期加速赶顶妖股",
                "killed_stocks": ["新华传媒", "时代万恒", "善水科技"],
                "verdict": "⚠️ 进入兑现出清期",
                "reason": "老妖股已在10.12-10.13完成天量赶顶，资金正向低位新题材切换，坚决不接老周期最后一棒。"
            }
        ],
        "takeaway": "退潮出清后的核心法则：‘喜新厌旧、高低切换’！老周期高标（新华传媒、时代万恒、善水科技）赶顶出清后，市场跌停迅速收窄至6家，低位吉大正元顶出4板打破压制，机器人16家涨停引爆新集群。战术上坚决回避高位断板股，积极围绕低位新梯队（吉大正元、长春一东、东安动力）与容量中军（瑞芯微、九阳股份）展开试错！"
    }
]

def build_baotuan_cycle(report_date: Optional[str] = None, *, research_report: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from copy import deepcopy
    from zoneinfo import ZoneInfo
    from crowding_cycle import REFERENCE_DATE, build_crowding_report, day

    requested = day(report_date) if report_date else (
        research_report['requested_date'] if research_report is not None else
        datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d'))
    research = research_report if research_report is not None else build_crowding_report(requested)
    if research['requested_date'] != requested:
        raise ValueError('研究报告截止日与请求日不一致。')
    latest = research['daily'][-1] if research['daily'] else {}
    available = research['quality'] == 'research_only' and latest.get('coverage_ok', False)
    stage = latest.get('stage', '数据不足') if available else '数据不足 / 未知'
    cases = []
    for original in BAOTUAN_HISTORICAL_CYCLES:
        known_at = max(REFERENCE_DATE, day(original['end_date']))
        if known_at > requested:
            continue
        case = deepcopy(original)
        case.update(known_at=None, visibility_date=known_at, claim_as_of=day(case['end_date']),
                    knowledge_status='retrospective_visibility_boundary', authority='历史研究观点，未经外部行情核验')
        case['name'] = case['name'].replace(' (最新)', '').replace('(最新)', '')
        case['date_range'] = f"{day(case['start_date'])} — {day(case['end_date'])}"
        cases.append(case)
    stats = dict(total_zt=None, total_dt=None, max_height=None, highest_stocks=[])
    trials = [dict(sector='全市场价格持续性候选', stocks=f"{item['name']} ({item['code']})",
                   status='价格研究候选，非新周期确认', type='待验证',
                   as_of=latest.get('date'), execution_status='not_qualified')
              for item in latest.get('top', []) if available]
    return dict(status='ok', report_date=requested.replace('-', ''), report_date_fmt=requested,
                data_as_of=research['sources_as_of'], claim_as_of=latest.get('date'), known_at=None,
                knowledge_status='reconstructed_from_current_cache',
                authority='价格证据研究，非交易信号', current_stage=stage,
                stage_badge=stage, stage_color='#6366f1', market_temp_score=None,
                market_temp_desc='不输出未经计算和校准的主观温度评分', cleansing_score=None,
                cleansing_desc='价格止跌不等于资金出清完成', tactical_guidance='检查数据时钟、固定篮子与核心失效；量能和可成交性未验证，不升级交易判断。',
                cycles=cases, current_cycle={}, today_trials=trials, today_discarded=[], today_succession=[],
                stats=stats, research_report=research)


def render_baotuan_teaser_html(result: Optional[Dict[str, Any]] = None) -> str:
    result = result if result is not None else build_baotuan_cycle()
    return f'''<section class="baotuan-teaser" style="margin:18px 0;padding:18px;border:1px solid #796bb7;border-radius:10px;background:#172132;color:#e6edf3">
      <strong>抱团周期演进与新周期试错雷达 · 证据核验版</strong>
      <p>操盘心法与定性：历史案例不覆盖当日判断；缺价未知，核心可失效，旧团可修复。</p>
      <p>截止 {_e(result['report_date_fmt'])} · {_e(result['current_stage'])} · 研究观察，不是买卖信号</p>
      <a href="baotuan/latest.html" style="color:#c9b9ff">进入抱团周期研究 →</a></section>'''


def generate_baotuan_html(result: Optional[Dict[str, Any]] = None, is_site_mode: bool = True) -> str:
    from crowding_view import generate_crowding_html

    result = result if result is not None else build_baotuan_cycle()
    document = generate_crowding_html(result['research_report'], is_site_mode=is_site_mode)
    cases = ''.join(f"<details><summary>{_e(case['name'])}</summary>"
                    f"<p>适用区间：{_e(case['date_range'])} · 回顾显示边界：{_e(case['visibility_date'])}（推定，非真实发布时点）</p>"
                    f"<p>{_e(case['authority'])}</p><p>{_e(case.get('summary', ''))}</p></details>" for case in result['cycles'])
    appendix = '<section id="historical-cases"><h2>抱团周期全景演进 · 历史案例库</h2><p class="muted">以下为事后叙事，不参与当日价格判断；不外推固定价格、评分或板块永久失效结论。</p>' + cases + '</section>'
    return document.replace('</main>', appendix + '</main>', 1)


def export_baotuan_standalone_reports(
    report_date: Optional[str] = None,
    output_dir: Optional[str] = None,
    site_dir: Optional[str] = None,
    baotuan_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    from crowding_view import archive_candidate_snapshots, archive_tracking_snapshots, archive_replay_snapshots, write_crowding_latest

    out_dir = output_dir or OUTPUT_DIR
    st_dir = site_dir or SITE_DIR
    os.makedirs(out_dir, exist_ok=True)

    if baotuan_data is None:
        baotuan_data = build_baotuan_cycle(report_date)
    elif report_date and _fmt_date(report_date) != baotuan_data['report_date_fmt']:
        raise ValueError('导出请求日与抱团研究报告不一致。')

    dt_str = baotuan_data.get("report_date", "")
    dt_fmt = baotuan_data.get("report_date_fmt", "")

    local_html = generate_baotuan_html(baotuan_data, is_site_mode=False)
    site_html = generate_baotuan_html(baotuan_data, is_site_mode=True)

    local_path = os.path.join(out_dir, f"抱团周期与新周期试错分析_{dt_fmt}.html")
    with open(local_path, "w", encoding="utf-8") as f:
        f.write(local_html)

    baotuan_site_dir = os.path.join(st_dir, "baotuan")
    os.makedirs(baotuan_site_dir, exist_ok=True)

    site_latest = os.path.join(baotuan_site_dir, "latest.html")
    write_crowding_latest(Path(site_latest), site_html, dt_fmt)

    site_dated = os.path.join(baotuan_site_dir, f"{dt_fmt}.html")
    with open(site_dated, "w", encoding="utf-8") as f:
        f.write(site_html)
    snapshot_dir = archive_candidate_snapshots(baotuan_data['research_report'], out_dir)
    tracking_dir = archive_tracking_snapshots(baotuan_data['research_report'], out_dir)
    replay_dir = archive_replay_snapshots(baotuan_data['research_report'], out_dir)

    return {
        "local_report": local_path,
        "site_latest": site_latest,
        "site_dated": site_dated,
        "candidate_snapshots": str(snapshot_dir.resolve()),
        "tracking_snapshots": str(tracking_dir.resolve()),
        "replay_snapshots": str(replay_dir.resolve()),
        "report_date": dt_str
    }

if __name__ == "__main__":
    res = build_baotuan_cycle()
    print("Build baotuan cycle success! Report date:", res["report_date"])
    print("Stage:", res["current_stage"])
    paths = export_baotuan_standalone_reports()
    print("Exported files:", paths)
