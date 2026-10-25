"""开盘啦 (KaiPanLa) API 客户端

依据抓包协议封装：
  - 域名: https://apphwshhq.kaipanhong.com/w1/api/index.php
  - 编码: application/x-www-form-urlencoded
  - 版本: 6.1.6 (apiv=w46)
  - 路由: c=<模块> & a=<动作>

已验证可用接口清单 (2026-10-17 实测，未知模块会被服务端拒绝返回非 JSON):
  1. c=FuPanLa               a=GetZDTCount           全市场涨跌停家数
  2. c=FuPanLa               a=GetZhangTingTianTi    涨停连板天梯
  3. c=DailyLimitResumption  a=GetPlateInfo_w38      板块涨停原因 + 市场统计(炸板率/昨涨停表现)
  4. c=FuPanLa               a=GetPMSL_PMLD          盘面梳理异动时间线
"""

import uuid
import logging
from typing import Dict, Any, List, Optional
import requests

logger = logging.getLogger(__name__)

class KaiPanLaClient:
    BASE_URL = "https://apphwshhq.kaipanhong.com/w1/api/index.php"
    
    def __init__(self, device_id: Optional[str] = None, timeout: int = 10):
        self.device_id = device_id or str(uuid.uuid4())
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "okhttp/3.12.13",
            "Content-Type": "application/x-www-form-urlencoded"
        })

    def _call(self, module: str, action: str, **kwargs) -> Dict[str, Any]:
        """统一底层 POST 调用"""
        data = {
            "c": module,
            "a": action,
            "Red": "1",
            "apiv": "w46",
            "PhoneOSNew": "1",
            "VerSion": "6.1.6",
            "DeviceID": self.device_id,
        }
        data.update({k: str(v) for k, v in kwargs.items()})
        try:
            resp = self.session.post(self.BASE_URL, data=data, timeout=self.timeout)
            resp.raise_for_status()
            res_json = resp.json()
            if res_json.get("errcode") != "0":
                logger.warning(f"KaiPanLa API returned non-zero errcode: {res_json.get('errcode')}")
            return res_json
        except Exception as e:
            logger.error(f"KaiPanLa request failed for {module}.{action}: {e}")
            return {"errcode": "-1", "error": str(e)}

    def get_market_stats(self, st: int = 100, index: int = 0) -> Dict[str, Any]:
        """获取全市场统计口径 (DailyLimitResumption.GetPlateInfo_w38 的 nums 段)

        这是开盘啦侧最完整的市场概况口径，可作为财联社 emotion 的独立交叉校验源。

        Returns:
            up_count          上涨家数      (SZJS)
            down_count        下跌家数      (XDJS)
            limit_up_count    涨停家数      (ZT)
            limit_down_count  跌停家数      (DT)
            broken_ratio      炸板率 %      (ZBL)
            yesterday_zt_perf 昨日涨停表现 % (yestRase)
        """
        raw = self._call("DailyLimitResumption", "GetPlateInfo_w38", st=st, Index=index)
        nums = raw.get("nums", {}) or {}

        def _num(key, default=0.0):
            try:
                return float(nums.get(key, default))
            except (TypeError, ValueError):
                return default

        return {
            "up_count": int(_num("SZJS")),
            "down_count": int(_num("XDJS")),
            "limit_up_count": int(_num("ZT")),
            "limit_down_count": int(_num("DT")),
            "broken_ratio": round(_num("ZBL"), 2),
            "yesterday_zt_perf": round(_num("yestRase"), 2),
            "date": raw.get("date", ""),
        }

    def get_zdt_count(self) -> Dict[str, Any]:
        """获取全市场涨跌停家数计数
        
        Returns:
            {"ZT": 36, "DT": 11, ...}
        """
        raw = self._call("FuPanLa", "GetZDTCount")
        try:
            return {
                "zt": int(raw.get("ZT", 0)),
                "dt": int(raw.get("DT", 0)),
                "raw": raw
            }
        except (ValueError, TypeError):
            return {"zt": 0, "dt": 0, "raw": raw}

    def get_zhangting_tianti(self) -> Dict[str, Any]:
        """获取涨停连板天梯
        
        解析位置数组为结构化字典:
          [0]代码 [1]股票名 [2]连板数 [3]首次涨停时间 [4]板块代码 [5]板块名
          [6]是否首板 [7]状态标记 [8]涨停天数/高度 [9]流通市值 [10]总市值
        """
        raw = self._call("FuPanLa", "GetZhangTingTianTi")
        stock_list = raw.get("StockList", [])
        parsed_stocks = []
        for item in stock_list:
            if not isinstance(item, list) or len(item) < 11:
                continue
            code = str(item[0]).zfill(6)
            # 补齐标准前缀 sh/sz
            prefix = "sh" if code.startswith(("60", "68")) else ("bj" if code.startswith(("4", "8", "9")) else "sz")
            parsed_stocks.append({
                "code": code,
                "symbol": f"{prefix}{code}",
                "name": item[1],
                "height": int(item[2]) if str(item[2]).isdigit() else item[2],
                "first_limit_time": item[3],
                "plate_code": item[4],
                "plate_name": item[5],
                "is_first_board": bool(item[6]),
                "status_mark": item[7],
                "limit_days": item[8],
                "float_market_val": item[9],
                "total_market_val": item[10],
            })
            
        # 按连板高度从高到低排序
        parsed_stocks.sort(key=lambda x: (isinstance(x["height"], int) and x["height"] or 0), reverse=True)
        return {
            "stocks": parsed_stocks,
            "plate_groups": raw.get("ZhuShuList", []),
            "count": len(parsed_stocks)
        }

    def get_plate_resumption(self, st: int = 100, index: int = 0) -> Dict[str, Any]:
        """获取板块维度涨停原因与长文本逻辑 (DailyLimitResumption.GetPlateInfo_w38)
        
        含全市场涨跌统计 nums，以及个股涨停长文本核心理由
        """
        raw = self._call("DailyLimitResumption", "GetPlateInfo_w38", st=st, Index=index)
        nums = raw.get("nums", {})
        plate_list = raw.get("list", [])
        
        structured_plates = []
        for p in plate_list:
            plate_code = p.get("ZSCode", "")
            plate_name = p.get("ZSName", "")
            p_stocks = []
            for s in p.get("StockList", []):
                if not isinstance(s, list) or len(s) < 18:
                    continue
                code = str(s[0]).zfill(6)
                prefix = "sh" if code.startswith(("60", "68")) else ("bj" if code.startswith(("4", "8", "9")) else "sz")
                p_stocks.append({
                    "code": code,
                    "symbol": f"{prefix}{code}",
                    "name": s[1],
                    "change_pct": s[4],
                    "order_amount": s[8],     # 封单金额
                    "height_desc": s[9],      # 如 '4天3板'
                    "tags": s[11],            # 题材标签
                    "core_concept": s[16],    # 核心概念
                    "reason_text": s[17],     # 涨停原因长文本
                })
            structured_plates.append({
                "plate_code": plate_code,
                "plate_name": plate_name,
                "stock_count": p.get("num", len(p_stocks)),
                "stocks": p_stocks
            })
            
        return {
            "market_stats": {
                "up_count": nums.get("SZJS", 0),
                "down_count": nums.get("XDJS", 0),
                "zt_count": nums.get("ZT", 0),
                "dt_count": nums.get("DT", 0),
                "broken_ratio": nums.get("ZBL", 0.0),      # 炸板率
                "yesterday_zt_perf": nums.get("yestRase", 0.0) # 昨日涨停表现
            },
            "plates": structured_plates,
            "date": raw.get("date", "")
        }

    def get_panmian_timeline(self, st: int = 30, index: int = 0) -> List[Dict[str, Any]]:
        """获取盘面梳理异动事件时间线 (GetPMSL_PMLD)"""
        raw = self._call("FuPanLa", "GetPMSL_PMLD", st=st, Index=index)
        events = []
        for item in raw.get("List", []):
            events.append({
                "time_sec": item.get("TimeMin"),
                "tag_name": item.get("TagName"),
                "tag_type": item.get("TagShuXing"), # 2=涨停类 0=跌停类 1=其他
                "plate_name": item.get("ZSName"),
                "detail": item.get("Detail"),
                "stocks": item.get("StockList", [])
            })
        return events
