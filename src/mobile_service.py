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

        # 5. 聚合 Tab 3：核心自选池 (Focus Pool) 与龙头个股
        focus_stocks = self._build_focus_pool(tianti_data.get("stocks", []))

        # 6. 聚合 Tab 4：复盘推演预案
        strategy_module = self._build_strategy_plan(degree, degree_status, tianti_data.get("stocks", []), today_str, mood_module.get("dt_count", 0))

        # 7. 聚合「盯盘」页（财联社盯盘 1:1 结构，三源降级）
        logger.info("Building dingpan module...")
        dingpan_module = self.dingpan.build(date=today_str)

        bundle = {
            "meta": {
                "version": "1.1.0",
                "updated_at": f"{today_str} {now_time_str}",
                "date": today_str,
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

    def _build_focus_pool(self, tianti_stocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """构建关注池标的列表（融合 focus_pool.csv 与梯队前排龙头）"""
        pool = []
        # 1. 尝试读取 focus_pool.csv
        focus_csv = Path(BASE_DIR) / "focus_pool.csv"
        if focus_csv.exists():
            import pandas as pd
            try:
                df = pd.read_csv(focus_csv)
                for _, row in df.iterrows():
                    pool.append({
                        "name": str(row.get("股票", row.get("name", ""))),
                        "code": str(row.get("代码", row.get("code", ""))),
                        "sector": str(row.get("板块", "重点自选")),
                        "pool_type": str(row.get("策略池", "【核心池】")),
                        "entry_rule": str(row.get("入场条件", "关注早盘承接")),
                        "defense_rule": str(row.get("防守位", "跌破均线止损")),
                        "status": "green" if "3" in str(row.get("入场条件", "")) else "yellow",
                        "is_custom": True
                    })
            except Exception as e:
                logger.warning(f"Failed to read focus_pool.csv: {e}")

        # 2. 如果自选数量较少，补充开盘啦高标连板龙头
        for stock in tianti_stocks[:6]:
            # 避免重复
            if any(p["name"] == stock["name"] for p in pool):
                continue
            height = stock.get("height", 1)
            status = "green" if height >= 3 else ("yellow" if height == 2 else "normal")
            pool.append({
                "name": stock["name"],
                "code": stock["symbol"],
                "sector": stock.get("plate_name", "主线热点"),
                "pool_type": f"【{height}连板梯队】",
                "entry_rule": f"连板龙头。首选分时平开弱转强放量换手承接，谨防无量一字秒开坑杀。",
                "defense_rule": "5日均线或前一日涨停价为防守位。",
                "height": height,
                "status": status,
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
