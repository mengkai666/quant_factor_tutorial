import requests  # type: ignore
# pyrefly: ignore [missing-import]
import urllib3
from datetime import datetime
from time_utils import get_latest_date
 
# 禁用 SSL 警告 
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning) 
 
class FuPanZhangTingYuanYin: 
    def __init__(self): 
        self.url = "https://apphwshhq.longhuvip.com/w1/api/index.php" 
        self.headers = { 
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", 
            "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; PFEM10 Build/PQ3A.190605.003)", 
            "Host": "apphwshhq.longhuvip.com", 
            "Connection": "Keep-Alive", 
            "Accept-Encoding": "gzip" 
        } 
        self.base_params_reason = { 
            "a": "GetPlateInfo_w38", 
            "st": "100", 
            "c": "DailyLimitResumption", 
            "PhoneOSNew": "1", 
            "DeviceID": "20adcd18-9e93-3bb7-b4d5-c9fd5fa30b3f", 
            "VerSion": "5.23.0.4", 
            "Index": "0", 
            "apiv": "w44" 
        } 
        self.base_params_ladder = { 
            "a": "GetZhangTingTianTi", 
            "c": "FuPanLa", 
            "PhoneOSNew": "1", 
            "DeviceID": "20adcd18-9e93-3bb7-b4d5-c9fd5fa30b3f", 
            "VerSion": "5.23.0.4", 
            "apiv": "w44" 
        } 
 
    def get_data(self, day=None): 
        """抓取并解析所有复盘数据 (同步版)""" 
        if not day: 
            day = get_latest_date().strftime("%Y-%m-%d") 
         
        # 1. 抓取涨停原因 
        reason_data = self._fetch_reason(day) 
        # 2. 抓取涨停天梯 
        ladder_data = self._fetch_ladder(day) 
         
        return { 
            "date": day, 
            "reason": reason_data, 
            "ladder": ladder_data 
        } 
 
    def _fetch_reason(self, day): 
        params = self.base_params_reason.copy() 
        params["Day"] = day 
        payload = "&".join([f"{k}={v}" for k, v in params.items()]) 
        try: 
            response = requests.post(self.url, data=payload, headers=self.headers, verify=False, timeout=15) 
            if response.status_code == 200: 
                return self.optimize_reason(response.json()) 
        except Exception: 
            return None 
 
    def _fetch_ladder(self, day): 
        params = self.base_params_ladder.copy() 
        params["Day"] = day 
        payload = "&".join([f"{k}={v}" for k, v in params.items()]) 
        try: 
            response = requests.post(self.url, data=payload, headers=self.headers, verify=False, timeout=15) 
            if response.status_code == 200: 
                return self.optimize_ladder(response.json()) 
        except Exception: 
            return None 
 
    def optimize_reason(self, raw_data): 
        """优化涨停原因数据""" 
        if not raw_data or "nums" not in raw_data: 
            return None 
        nums = raw_data.get("nums", {}) 
        plates = [] 
        for p in raw_data.get("list", []): 
            plate_name = str(p.get("ZSName", "")) 
            if "\\u" in plate_name: 
                plate_name = plate_name.encode("utf-8").decode("unicode_escape") 
             
            stock_list = [] 
            for s in p.get("StockList", []): 
                # 基于诊断结果的精确字段映射 
                time_raw = s[6] if len(s) > 6 else 0 
                try: 
                    time_str = datetime.fromtimestamp(time_raw).strftime('%H:%M') 
                except: 
                    time_str = "--:--" 
                 
                # 转换市值为“亿” 
                mv_raw = s[15] if len(s) > 15 else 0 
                mv_str = f"{float(mv_raw)/100000000:.2f}亿" if mv_raw else "0.00亿" 
                 
                # 转换封单为“亿” 
                seal_raw = s[8] if len(s) > 8 else 0 
                seal_str = f"{float(seal_raw)/100000000:.2f}亿" if seal_raw else "0.00亿" 
                 
                # 开板判断 
                open_count = s[10] if len(s) > 10 else 0 
                is_open_str = "是" if int(open_count) > 0 else "否" 
 
                stock_list.append({ 
                    "code": s[0], 
                    "name": s[1], 
                    "time": time_str, 
                    "status": s[9] if len(s) > 9 else "首板", 
                    "market_value": mv_str, 
                    "concept": str(s[11]).replace("、", "<br>"), 
                    "is_open": is_open_str, 
                    "seal_order": seal_str, 
                    "reason": s[17] if len(s) > 17 else "暂无原因" 
                }) 
            plates.append({"plate_name": plate_name, "stocks": stock_list}) 
        
        szjs = int(nums.get("SZJS", 0))
        xdjs = int(nums.get("XDJS", 0))
        total = szjs + xdjs
        sentiment_factor = f"{(szjs / total * 100):.1f}%" if total > 0 else "50.0%"

        return { 
            "summary": { 
                "szjs": szjs, "xdjs": xdjs, 
                "sentiment_factor": sentiment_factor,
                "ztjs": nums.get("ZT", 0), "dtjs": nums.get("DT", 0), 
                "zbl": f"{nums.get('ZBL', 0):.2f}%", 
                "yest_rase": f"{nums.get('yestRase', 0):.2f}%" 
            }, 
            "plates": plates 
        } 
 
    def optimize_ladder(self, raw_data): 
        """优化涨停天梯数据""" 
        if not raw_data or "StockList" not in raw_data: 
            return None 
         
        category = {"3板及以上": [], "2板": [], "首板": []} 
        sector_summary = {} 
         
        for stock in raw_data.get("StockList", []): 
            name = str(stock[1]) 
            if "\\u" in name: name = name.encode("utf-8").decode("unicode_escape") 
             
            level = stock[2] if len(stock) > 2 else 0 
            time_raw = stock[3] if len(stock) > 3 else 0 
            try: 
                time_str = datetime.fromtimestamp(time_raw).strftime('%H:%M') 
            except: 
                time_str = "09:25" 
             
            sector = stock[5] if len(stock) > 5 else "其他" 
            sector_summary[sector] = sector_summary.get(sector, 0) + 1 
             
            info = {"code": stock[0], "name": name, "level": level, "time": time_str, "sector": sector} 
             
            if level >= 3: category["3板及以上"].append(info) 
            elif level == 2: category["2板"].append(info) 
            elif level == 1: category["首板"].append(info) 
             
        return {"category": category, "sector_summary": sector_summary} 
 
    def export_html(self, data): 
        """导出为一体化 HTML (支持选项卡切换)""" 
        if not data: return 
        date = data["date"] 
        reason = data["reason"] 
        ladder = data["ladder"] 
        filename = f"综合复盘报告_{date}.html" 
 
        # 1. 构造天梯 HTML
        if ladder:
            # 热门板块 
            sector_html = "" 
            sorted_sectors = sorted(ladder["sector_summary"].items(), key=lambda x: x[1], reverse=True)[:6] 
            for s, c in sorted_sectors: 
                sector_html += f'<div class="sector-box"><div class="s-name">{s}({c})</div><div class="s-desc">板块活跃度--</div></div>' 
             
            # 天梯行 
            def build_ladder_row(label, stocks): 
                if not stocks: return "" 
                cards = "".join([f'<div class="stock-card"><div class="s-time">{s["time"]}</div><div class="s-name">{s["name"]}</div><div class="s-sector">{s["sector"]}</div></div>' for s in stocks]) 
                return f'<div class="ladder-row"><div class="row-label">{label}</div><div class="row-content">{cards}</div></div>' 
             
            ladder_content = f""" 
            <div id="ladder-tab" class="tab-content"> 
                <div class="section-header">热门题材分布</div>
                <div class="sector-grid">{sector_html}</div> 
                <div class="section-header">涨停连板天梯</div>
                <div class="ladder-container"> 
                    {build_ladder_row("高度板", ladder["category"]["3板及以上"])} 
                    {build_ladder_row("进级板", ladder["category"]["2板"])} 
                    {build_ladder_row("起步板", ladder["category"]["首板"])} 
                </div> 
            </div> 
            """ 
        else: 
            ladder_content = '<div id="ladder-tab" class="tab-content">暂无市场动向数据</div>' 
 
        # 2. 构造原因 HTML
        if reason:
            plates_html = "" 
            for p in reason["plates"]: 
                # 每个板块的列表头 
                list_header = """ 
                <div class="r-header list-header"> 
                    <span>个股名称</span> 
                    <span>晋级时间</span> 
                    <span>连板高度</span> 
                    <span>流通市值</span> 
                    <span>核心题材</span> 
                    <span>曾开板</span> 
                    <span>封单金额</span> 
                </div> 
                """ 
                 
                nl = '\n'
                nl_esc = '\\n'
                stocks_html = "".join([f""" 
                <div class="reason-item"> 
                    <div class="r-header"> 
                        <span class="r-name"><strong>{s['name']}</strong><br><small>{s['code']}</small></span> 
                        <span class="r-time">{s['time']}</span> 
                        <span class="r-status">{s['status']}</span> 
                        <span class="r-mv">{s['market_value']}</span> 
                        <span class="r-concept">{str(s['concept']).replace(nl_esc, '<br>')}</span> 
                        <span class="r-open">{s['is_open']}</span> 
                        <span class="r-seal">{s['seal_order']}</span> 
                    </div> 
                    <div class="r-body">{str(s['reason']).replace(nl, '<br>') if s['reason'] else '暂无分析'}</div> 
                </div>""" for s in p["stocks"]]) 
                 
                plates_html += f""" 
                <div class="plate-section"> 
                    <div class="p-title"> 
                        <span>{p["plate_name"]}</span> 
                        <small>共 {len(p["stocks"])} 家涨停</small> 
                    </div> 
                    {list_header} 
                    {stocks_html} 
                </div>""" 
             
            summary = reason["summary"] 
            reason_content = f""" 
            <div id="reason-tab" class="tab-content active"> 
                <div class="summary-bar"> 
                    <div class="sum-row"> 
                        <div class="sum-item">
                            <label>情绪量化因子</label>
                            <span class="val red">{summary['sentiment_factor']}</span>
                        </div>
                        <div class="sum-item">
                            <label>市场赚钱效应</label>
                            <span class="val green">{summary['yest_rase']}</span>
                        </div>
                    </div> 
                    <div class="sum-row"> 
                        <div class="sum-item">
                            <label>涨跌力量对比</label>
                            <span class="val"><span class="red">{summary['ztjs']}</span> : <span class="green">{summary['dtjs']}</span></span>
                        </div>
                        <div class="sum-item">
                            <label>炸板抛压比例</label>
                            <span class="val red">{summary['zbl']}</span>
                        </div>
                    </div> 
                </div> 
                {plates_html} 
            </div> 
            """ 
        else: 
            reason_content = '<div id="reason-tab" class="tab-content active">暂无涨停原因分析</div>' 
 
        html_template = f""" 
        <!DOCTYPE html> 
        <html> 
        <head> 
            <meta charset="UTF-8"> 
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>每日复盘报告 - {date}</title> 
            <style> 
                body {{ font-family: "PingFang SC", "Microsoft YaHei", sans-serif; background: #f8f9fa; margin: 0; padding: 20px 10px; color: #333; }} 
                .container {{ max-width: 600px; margin: 0 auto; background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.08); }} 
                .tabs {{ display: flex; background: #fff; border-bottom: 1px solid #f0f0f0; position: sticky; top: 0; z-index: 100; }} 
                .tab {{ flex: 1; padding: 15px; text-align: center; color: #888; cursor: pointer; transition: all 0.3s; font-size: 15px; }} 
                .tab.active {{ color: #d63031; border-bottom: 3px solid #d63031; font-weight: bold; background: #fff5f5; }} 
                .tab-content {{ display: none; padding: 0; animation: fadeIn 0.4s; }} 
                .tab-content.active {{ display: block; }} 
                @keyframes fadeIn {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}

                .section-header {{ padding: 15px 10px 5px; font-weight: bold; color: #2d3436; font-size: 14px; border-left: 4px solid #d63031; margin: 10px 0; }}

                /* 情绪看板 */
                .summary-bar {{ background: #fff; padding: 20px; border-bottom: 8px solid #f8f9fa; }} 
                .sum-row {{ display: flex; justify-content: space-between; margin-bottom: 15px; }} 
                .sum-row:last-child {{ margin-bottom: 0; }}
                .sum-item {{ flex: 1; display: flex; flex-direction: column; align-items: center; }}
                .sum-item label {{ font-size: 12px; color: #999; margin-bottom: 4px; }}
                .sum-item .val {{ font-size: 18px; font-weight: bold; font-family: "DIN Alternate", sans-serif; }}
                .red {{ color: #d63031; }} 
                .green {{ color: #27ae60; }} 
                 
                /* 题材动向 */ 
                .sector-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; padding: 10px; }} 
                .sector-box {{ background: #fff5f5; border: 1px solid #ffe8e8; padding: 10px 5px; border-radius: 6px; text-align: center; }} 
                .s-name {{ color: #d63031; font-size: 13px; font-weight: bold; margin-bottom: 2px; }} 
                .s-desc {{ color: #b2bec3; font-size: 11px; }} 

                /* 连板天梯 */
                .ladder-container {{ padding: 0 10px 20px; }}
                .ladder-row {{ display: flex; border-bottom: 1px solid #f1f2f6; padding: 15px 0; }} 
                .row-label {{ width: 50px; font-weight: bold; font-size: 12px; display: flex; align-items: center; color: #636e72; flex-shrink: 0; }} 
                .row-content {{ flex: 1; display: grid; grid-template-columns: repeat(auto-fill, minmax(110px, 1fr)); gap: 8px; }} 
                .stock-card {{ background: #fff; border: 1px solid #f1f2f6; padding: 8px; text-align: center; border-radius: 6px; position: relative; transition: transform 0.2s; }} 
                .stock-card:hover {{ transform: translateY(-2px); box-shadow: 0 2px 8px rgba(0,0,0,0.05); }}
                .s-time {{ font-size: 9px; color: #dfe6e9; position: absolute; top: 4px; right: 6px; }} 
                .s-name {{ font-size: 13px; margin-top: 10px; color: #2d3436; font-weight: 500; }} 
                .s-sector {{ font-size: 11px; color: #fdcb6e; margin-top: 2px; }} 
 
                /* 涨停原因详情 */ 
                .plate-section {{ border-bottom: 8px solid #f8f9fa; }} 
                .p-title {{ padding: 12px 15px; font-weight: bold; color: #fff; background: linear-gradient(135deg, #d63031, #ff7675); display: flex; justify-content: space-between; align-items: center; }} 
                .p-title small {{ font-weight: normal; font-size: 12px; opacity: 0.85; }} 
                .reason-item {{ padding: 15px; border-bottom: 1px solid #f1f2f6; }} 
                .r-header {{ display: grid; grid-template-columns: 1.2fr 0.8fr 0.8fr 0.9fr 1.5fr 0.6fr 0.9fr; gap: 6px; font-size: 11px; margin-bottom: 10px; align-items: center; text-align: center; color: #636e72; }} 
                .r-header.list-header {{ background: #fdfdfd; padding: 8px 15px; color: #b2bec3; font-size: 10px; border-bottom: 1px solid #f1f2f6; }} 
                .r-name {{ text-align: left; font-size: 14px; font-weight: bold; color: #2d3436; }} 
                .r-time {{ font-size: 12px; }} 
                .r-status {{ font-size: 12px; color: #0984e3; font-weight: 600; }} 
                .r-mv {{ font-size: 12px; }} 
                .r-concept {{ font-size: 11px; color: #6c5ce7; line-height: 1.3; }} 
                .r-open, .r-seal {{ font-size: 12px; }} 
                .r-body {{ font-size: 13px; color: #2d3436; background: #f9f9f9; padding: 12px; border-radius: 8px; line-height: 1.6; border-left: 4px solid #dfe6e9; }} 
            </style> 
        </head> 
        <body> 
            <div class="container"> 
                <div class="tabs"> 
                    <div class="tab" onclick="switchTab('ladder')">市场热度</div> 
                    <div class="tab active" onclick="switchTab('reason')">涨停解析</div> 
                </div> 
                {ladder_content} 
                {reason_content} 
            </div> 
            <script> 
                function switchTab(type) {{ 
                    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active')); 
                    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active')); 
                    if(type === 'ladder') {{ 
                        document.querySelectorAll('.tab')[0].classList.add('active'); 
                        document.getElementById('ladder-tab').classList.add('active'); 
                    }} else {{ 
                        document.querySelectorAll('.tab')[1].classList.add('active'); 
                        document.getElementById('reason-tab').classList.add('active'); 
                    }} 
                }} 
            </script> 
        </body> 
        </html> 
        """ 
        with open(filename, "w", encoding="utf-8") as f: 
            f.write(html_template) 
        print(f"综合 HTML 报告已生成: {filename}") 
 
if __name__ == "__main__": 
    api = FuPanZhangTingYuanYin() 
    # 示例：获取截图中的日期 
    target_date = "2026-03-16" 
    data = api.get_data(target_date) 
     
    if data: 
        api.export_html(data) 
    else: 
        print("❌ 数据抓取失败") 
