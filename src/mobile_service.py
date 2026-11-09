"""移动端数据融合服务 (Mobile Data Service)

将【开盘啦实抓数据】+【财联社实抓数据】+【东方财富备用源】+【本项目量化模型产物】
进行标准化聚合，输出供手机 APP / 移动前端渲染的统一数据结构。
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

from data_sources.kaipanla_client import KaiPanLaClient
from data_sources.cls_client import CLSClient
from dingpan_service import DingpanAggregator
from paths import BASE_DIR, OUTPUT_DIR

logger = logging.getLogger(__name__)

class MobileDataAggregator:
    def __init__(self, output_dir: str = None):
        # 路径一律基于 paths.BASE_DIR，避免 cwd 不同导致产物写到 src/output/ 下
        self.output_dir = Path(output_dir) if output_dir else Path(OUTPUT_DIR) / "mobile_api"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.kaipanla = KaiPanLaClient()
        self.cls = CLSClient()
        self.dingpan = DingpanAggregator(cls_client=self.cls, kpl_client=self.kaipanla)

    def build_full_bundle(self) -> Dict[str, Any]:
        """抓取并聚合全量移动端数据包"""
        today_str = datetime.now().strftime("%Y-%m-%d")
        now_time_str = datetime.now().strftime("%H:%M:%S")

        # 1. 抓取开盘啦数据
        logger.info("Fetching KaiPanLa data...")
        zdt_counts = self.kaipanla.get_zdt_count()
        tianti_data = self.kaipanla.get_zhangting_tianti()
        plate_resumption = self.kaipanla.get_plate_resumption(st=30)
        panmian_timeline = self.kaipanla.get_panmian_timeline(st=15)

        # 2. 抓取财联社数据
        logger.info("Fetching CLS data...")
        cls_emotion = self.cls.get_market_emotion()
        cls_mainline = self.cls.get_mainline()
        cls_tuyere = self.cls.get_today_tuyere()
        cls_fund_heat = self.cls.get_fund_heat(num=6)

        # 3. 聚合 Tab 1：市场情绪与大盘温度计
        market_stats = plate_resumption.get("market_stats", {})
        up_count = cls_emotion.get("up_count") or market_stats.get("up_count", 0)
        down_count = cls_emotion.get("down_count") or market_stats.get("down_count", 0)
        zt_count = zdt_counts.get("zt") or market_stats.get("zt_count", 0)
        dt_count = zdt_counts.get("dt") or market_stats.get("dt_count", 0)
        broken_ratio = market_stats.get("broken_ratio", 0.0)
        
        # 情绪度数
        degree = cls_emotion.get("market_degree", 50)
        if degree >= 70:
            degree_status = "亢奋共振期"
            degree_color = "rose"
        elif degree >= 50:
            degree_status = "活跃分歧期"
            degree_color = "amber"
        elif degree >= 30:
            degree_status = "混沌震荡期"
            degree_color = "blue"
        else:
            degree_status = "极度冰点期"
            degree_color = "emerald"

        mood_module = {
            "degree": degree,
            "status_text": degree_status,
            "color_tag": degree_color,
            "turnover": cls_emotion.get("turnover_str", "—"),
            "turnover_change": cls_emotion.get("turnover_change_str", ""),
            "up_count": up_count,
            "down_count": down_count,
            "zt_count": zt_count,
            "dt_count": dt_count,
            "broken_ratio": round(float(broken_ratio), 1) if broken_ratio else 0.0,
            "yesterday_zt_perf": market_stats.get("yesterday_zt_perf", 0.0),
            "distribution_bins": cls_emotion.get("distribution_bins", {}),
            "tianti_stocks": tianti_data.get("stocks", [])[:25], # 取前25名梯队股
            "panmian_events": panmian_timeline,
        }

        # 4. 聚合 Tab 2：抱团主线与风口
        # 读取本地已有抱团分析结果（若有）
        local_crowding = self._load_local_crowding()

        crowding_module = {
            "mainline_desc": cls_mainline.get("desc", "追踪资金主线聚焦点与题材发酵度"),
            "mainlines": cls_mainline.get("mainlines", []),
            "tuyeres": cls_tuyere,
            "fund_heat": cls_fund_heat,
            "local_cycles": local_crowding,
        }

        # 5. 先聚合 Tab 4：复盘推演预案（提取真实主线板块、龙头与抱团趋势锚）
        strategy_module = self._build_strategy_plan(degree, degree_status, tianti_data.get("stocks", []), today_str, mood_module.get("dt_count", 0))

        # 6. 聚合 Tab 3：核心自选池 (Focus Pool) 与高标异动监控样本
        focus_stocks = self._build_focus_pool(tianti_data.get("stocks", []), strategy_module)

        # 提炼严重异动雷达专区名单 (合并预案解析层已提取的高标异动雷达与自选池K线雷达)
        abnormal_radar_list = list(strategy_module.get("abnormal_radar") or [])
        seen_radar_codes = {x.get("code") for x in abnormal_radar_list if x.get("code")}
        for s in focus_stocks:
            ab = s.get("abnormal")
            if ab and s.get("code") not in seen_radar_codes:
                seen_radar_codes.add(s.get("code"))
                abnormal_radar_list.append({
                    "name": s.get("name"),
                    "code": s.get("code"),
                    "sector": s.get("sector", "核心"),
                    "latest_close": s.get("latest_close", 0.0),
                    "is_20cm": ab.get("is_20cm", False),
                    "ret10": ab.get("ret10", 0.0),
                    "safety10": ab.get("safety10", 100.0),
                    "boards_left": ab.get("boards_left", 9.9),
                    "ret30": ab.get("ret30", 0.0),
                    "safety30": ab.get("safety30", 200.0),
                    "risk_level": ab.get("risk_level", "safe"),
                    "risk_text": ab.get("risk_text", "")
                })
        strategy_module["abnormal_radar"] = sorted(
            abnormal_radar_list,
            key=lambda x: (0 if x["risk_level"] == "danger" else (1 if x["risk_level"] == "warning" else 2), x["safety10"])
        )

        # 7. 聚合「盯盘」页（财联社盯盘 1:1 结构，三源降级）
        logger.info("Building dingpan module...")
        dingpan_module = self.dingpan.build(date=today_str)

        bundle = {
            "meta": {
                "version": "1.3.0",
                "updated_at": f"{today_str} {now_time_str}",
                "date": today_str,
                "report_date": strategy_module.get("report_date", ""),
                "target_date": strategy_module.get("target_date", ""),
                "sources": ["开盘啦(6.1.6)", "财联社(8.5.4)", "东方财富(备用源)", "量化投研模型"],
                "dingpan_sources": dingpan_module.get("sources", {}),
                "dingpan_degraded": dingpan_module.get("degraded", False),
            },
            "tab_mood": mood_module,
            "tab_crowding": crowding_module,
            "tab_focus": focus_stocks,
            "tab_strategy": strategy_module,
            "tab_dingpan": dingpan_module,
        }

        # 持久化输出
        bundle_file = self.output_dir / "bundle.json"
        with open(bundle_file, "w", encoding="utf-8") as f:
            json.dump(bundle, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved mobile bundle to {bundle_file}")

        # 同步写入 site/api/ 供静态部署
        site_api_dir = Path(OUTPUT_DIR) / "site" / "api"
        site_api_dir.mkdir(parents=True, exist_ok=True)
        with open(site_api_dir / "bundle.json", "w", encoding="utf-8") as f:
            json.dump(bundle, f, ensure_ascii=False, indent=2)

        # 同步到本地 mobile_app/ 目录
        mobile_app_dir = Path(BASE_DIR) / "mobile_app"
        if mobile_app_dir.exists():
            with open(mobile_app_dir / "bundle.json", "w", encoding="utf-8") as f:
                json.dump(bundle, f, ensure_ascii=False)

        # 同步完整移动端 Web 站点到 output/site/mobile/ 供 GitHub Pages 部署
        import shutil
        site_mobile_dir = Path(OUTPUT_DIR) / "site" / "mobile"
        if mobile_app_dir.exists():
            shutil.copytree(mobile_app_dir, site_mobile_dir, dirs_exist_ok=True)
            logger.info("Synced mobile_app to output/site/mobile/")

        # 归档历史数据并同步更新历史索引
        try:
            import sys
            tools_dir = Path(BASE_DIR) / "tools"
            if str(tools_dir) not in sys.path:
                sys.path.insert(0, str(tools_dir))
            from generate_history_bundles import generate_all_history_bundles
            generate_all_history_bundles()
            logger.info("Updated historical bundles and history_index successfully.")
        except Exception as e:
            logger.warning(f"Failed to update historical bundles: {e}")

        return bundle

    def _load_local_crowding(self) -> List[Dict[str, Any]]:
        """从 output 目录检索最新的抱团分析数据"""
        try:
            output_path = Path(OUTPUT_DIR)
            json_files = sorted(output_path.glob("抱团周期分析_*.json"), reverse=True)
            if json_files:
                with open(json_files[0], "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("groups", [])
        except Exception as e:
            logger.warning(f"Could not load local crowding json: {e}")
        return []

    def _build_focus_pool(self, tianti_stocks: List[Dict[str, Any]], strategy_module: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """构建关注池标的列表（融合 focus_pool.csv、预案主线风向标与梯队前排龙头）"""
        pool = []
        mock_names = {"正常股份", "测试股", "样例股份", "示例股份"}
        # 从预案卡片提取主线板块映射，优先修正个股所属主线名称
        sector_override: Dict[str, str] = {}
        strategy_targets: List[Dict[str, Any]] = []
        if isinstance(strategy_module, dict):
            for card in strategy_module.get("baotuan_stock_targets", []) or []:
                if card.get("tag") == "avoid_black" or "禁买" in str(card.get("role", "")):
                    continue
                for t in card.get("targets", []) or []:
                    t_name = str(t.get("name", "")).strip()
                    t_code = str(t.get("code", "")).strip()
                    t_sec = str(t.get("sector", "") or card.get("sector", "")).strip()
                    if t_name and t_sec and t_sec not in {"梯队晋级", "延续观察", "持续核心观察"}:
                        sector_override[t_name] = t_sec
                    if t_name and t_code:
                        strategy_targets.append({
                            "name": t_name,
                            "code": t_code,
                            "sector": t_sec or str(card.get("sector", "主线风向标")),
                            "tag": str(t.get("tag", "")),
                            "role": str(card.get("role", "核心观察")),
                            "action": str(card.get("action", "关注竞价与分时承接")),
                            "stop": str(card.get("stop", "跌破均线支撑止损")),
                        })

        # 1. 尝试读取 focus_pool.csv（过滤测试占位股）
        focus_csv = Path(BASE_DIR) / "focus_pool.csv"
        if focus_csv.exists():
            import pandas as pd
            try:
                df = pd.read_csv(focus_csv)
                for _, row in df.iterrows():
                    s_name = str(row.get("股票", row.get("name", ""))).strip()
                    s_code = str(row.get("代码", row.get("code", ""))).strip()
                    if not s_name or s_name in mock_names:
                        continue
                    pool.append({
                        "name": s_name,
                        "code": s_code,
                        "sector": sector_override.get(s_name, str(row.get("板块", "重点自选"))),
                        "pool_type": str(row.get("策略池", "【核心池】")),
                        "entry_rule": str(row.get("入场条件", "关注早盘承接")),
                        "defense_rule": str(row.get("防守位", "跌破均线止损")),
                        "status": "green" if "3" in str(row.get("入场条件", "")) else "yellow",
                        "is_custom": True
                    })
            except Exception as e:
                logger.warning(f"Failed to read focus_pool.csv: {e}")

        # 2. 补充开盘啦高标连板龙头
        for stock in tianti_stocks[:6]:
            s_name = str(stock.get("name", "")).strip()
            if not s_name or s_name in mock_names or any(p["name"] == s_name for p in pool):
                continue
            height = stock.get("height", 1)
            status = "green" if height >= 3 else ("yellow" if height == 2 else "normal")
            pool.append({
                "name": s_name,
                "code": stock["symbol"],
                "sector": sector_override.get(s_name, stock.get("plate_name", "主线热点")),
                "pool_type": f"【{height}连板梯队】",
                "entry_rule": "连板风向标。核验早盘竞价承接与分时换手回封强度，严防无量一字炸板。",
                "defense_rule": "5日均线或前一日涨停收盘价为防守参考位。",
                "height": height,
                "status": status,
                "is_custom": False
            })

        # 3. 补充明日预案中的主线龙头与抱团趋势锚（确保盘前/周末自选池与异动雷达完整覆盖）
        for st in strategy_targets:
            if len(pool) >= 9:
                break
            if any(p["name"] == st["name"] or (p.get("code") and p["code"] == st["code"]) for p in pool):
                continue
            pool.append({
                "name": st["name"],
                "code": st["code"],
                "sector": st["sector"],
                "pool_type": f"【{st['tag'] or '预案锚点'}】",
                "entry_rule": st["action"],
                "defense_rule": st["stop"],
                "status": "yellow",
                "is_custom": False
            })

        # 3. 为自选池标的批量装载真实历史 K 线行情 (BaoStock)
        self._batch_enrich_klines(pool)

        return pool

    def _batch_enrich_klines(self, pool: List[Dict[str, Any]], days: int = 50):
        """批量获取个股真实OHLCV与EMA指标"""
        try:
            import baostock as bs
            import pandas as pd
            login_res = bs.login()
            if login_res.error_code != '0':
                logger.warning(f"BaoStock login failed: {login_res.error_msg}")
                return

            end_date = datetime.now().strftime("%Y-%m-%d")
            # 往前推 120 天以确保有足够交易日
            start_date = (datetime.now() - pd.Timedelta(days=days * 2.5)).strftime("%Y-%m-%d")

            for stock in pool:
                raw_code = stock.get("code", "")
                # 规范化 baostock 代码: sh.600865 或 sz.002343
                clean_code = raw_code.lower().replace(".", "")
                if clean_code.startswith("sh") or clean_code.startswith("sz") or clean_code.startswith("bj"):
                    bs_code = f"{clean_code[:2]}.{clean_code[2:]}"
                elif clean_code.startswith(("60", "68")):
                    bs_code = f"sh.{clean_code}"
                elif clean_code.startswith(("4", "8", "9")):
                    bs_code = f"bj.{clean_code}"
                else:
                    bs_code = f"sz.{clean_code}"

                rs = bs.query_history_k_data_plus(
                    bs_code,
                    "date,open,high,low,close,volume",
                    start_date=start_date,
                    end_date=end_date,
                    frequency="d",
                    adjustflag="3"
                )
                rows = []
                while (rs.error_code == '0') & rs.next():
                    rows.append(rs.get_row_data())

                if rows:
                    df = pd.DataFrame(rows, columns=rs.fields)
                    for col in ['open', 'high', 'low', 'close', 'volume']:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                    df = df.dropna().tail(days).reset_index(drop=True)

                    if not df.empty:
                        # 计算 EMA5 与 EMA20
                        df['ema5'] = df['close'].ewm(span=5, adjust=False).mean()
                        df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()

                        candles = []
                        ema5_list = []
                        ema20_list = []
                        for _, r in df.iterrows():
                            t = str(r['date'])
                            candles.append({
                                "time": t,
                                "open": round(float(r['open']), 2),
                                "high": round(float(r['high']), 2),
                                "low": round(float(r['low']), 2),
                                "close": round(float(r['close']), 2),
                                "volume": int(r['volume']),
                            })
                            ema5_list.append({"time": t, "value": round(float(r['ema5']), 2)})
                            ema20_list.append({"time": t, "value": round(float(r['ema20']), 2)})

                        stock["candles"] = candles
                        stock["ema5"] = ema5_list
                        stock["ema20"] = ema20_list
                        stock["latest_close"] = candles[-1]["close"]

                        # 计算严重异动安全雷达指标 (10日100% / 30日200%)
                        import math
                        num_code = clean_code[2:] if clean_code.startswith(('sh', 'sz', 'bj')) else clean_code
                        is_20cm = num_code.startswith(('30', '68'))
                        limit_pct = 20.0 if is_20cm else 10.0
                        limit_rate = 0.20 if is_20cm else 0.10
                        c_now = float(df['close'].iloc[-1])

                        ret10 = 0.0
                        safety10 = 100.0
                        boards10 = 9.9
                        if len(df) >= 10:
                            c_base10 = float(df['close'].iloc[-10])
                            if c_base10 > 0:
                                ret10 = round((c_now / c_base10 - 1.0) * 100, 2)
                                safety10 = round(100.0 - ret10, 2)
                                ratio_needed = 2.0 / (c_now / c_base10)
                                if ratio_needed <= 1.0:
                                    boards10 = 0.0
                                else:
                                    boards10 = round(math.log(ratio_needed) / math.log(1.0 + limit_rate), 1)
                        elif len(df) >= 2:
                            c_base1 = float(df['close'].iloc[0])
                            if c_base1 > 0:
                                ret10 = round((c_now / c_base1 - 1.0) * 100, 2)
                                safety10 = round(100.0 - ret10, 2)

                        ret30 = 0.0
                        safety30 = 200.0
                        if len(df) >= 30:
                            c_base30 = float(df['close'].iloc[-30])
                            if c_base30 > 0:
                                ret30 = round((c_now / c_base30 - 1.0) * 100, 2)
                                safety30 = round(200.0 - ret30, 2)

                        if safety10 <= limit_pct:
                            risk_level = "danger"
                            risk_text = "极度危险 (≤1板触线)"
                        elif safety10 <= (limit_pct * 2.2):
                            risk_level = "warning"
                            risk_text = "警戒区 (1~2板防主动断板)"
                        else:
                            risk_level = "safe"
                            risk_text = f"安全垫充裕 (余{boards10}板)"

                        stock["abnormal"] = {
                            "is_20cm": is_20cm,
                            "limit_rate": limit_rate,
                            "ret10": ret10,
                            "safety10": safety10,
                            "boards_left": max(0.0, boards10),
                            "ret30": ret30,
                            "safety30": safety30,
                            "risk_level": risk_level,
                            "risk_text": risk_text
                        }
            bs.logout()
        except Exception as e:
            logger.warning(f"Error enriching klines: {e}")


    def _build_strategy_plan(self, degree: int, status_text: str, top_stocks: List[Dict[str, Any]], date_str: str = "", dt_count: int = 0) -> Dict[str, Any]:
        """根据真实复盘研报或情绪温度动态提取深度实战预案作战看板"""
        try:
            from plan_parser import get_plan_for_date
            return get_plan_for_date(Path(__file__).resolve().parent.parent, date_str or "2026-10-22", degree, status_text, top_stocks, dt_count)
        except Exception as e:
            logger.warning(f"Error extracting rich plan: {e}")
            from plan_parser import synthesize_plan
            return synthesize_plan(date_str or "2026-10-22", degree, status_text, top_stocks, dt_count)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    aggregator = MobileDataAggregator()
    bundle = aggregator.build_full_bundle()
    print("Bundle created successfully! Summary:")
    print("Meta:", bundle["meta"])
    print("Mood Degree:", bundle["tab_mood"]["degree"], bundle["tab_mood"]["status_text"])
    print("Focus Pool Count:", len(bundle["tab_focus"]))
