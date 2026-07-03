"""
主线强度追踪系统 V3 — 概念板块版
数据源: 
  - 财联社CLS API (概念板块 + 涨停梯队 + up_tags)
  - baostock (行业分类 + 历史K线)

图表:
  1. 涨停梯队属性梳理表 (连板高度→主属性/次属性/核心成分股)
  2. 大主线强度折线图 (百分比强度)
  3. 大主线堆叠柱状图
  4. 大周期细分堆叠柱状图
  5. 细分板块强度折线图 (*5/*10/*20/*30 + 龙头标注)
  6. N日涨幅Top30排行榜 (5/10/20/60日)
  7. 盘面涨跌统计
  8. 各周期领涨板块热力分析
"""

import pandas as pd
import numpy as np
import os, sys, json, time, hashlib, requests, socket  # type: ignore
from timing_signal import generate_timing_signal
from screener import generate_focus_pool
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from datetime import datetime, timedelta
from fupan_report import FuPanZhangTingYuanYin
from time_utils import get_latest_date

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # type: ignore
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')  # type: ignore
except Exception:
    pass

# === 运行环境检测 ===
IS_GITHUB_ACTIONS = os.environ.get('GITHUB_ACTIONS') == 'true'
if IS_GITHUB_ACTIONS:
    print("[环境] 检测到 GitHub Actions 运行环境")

# === 配置 ===
if getattr(sys, 'frozen', False):
    CACHE_DIR = os.path.dirname(sys.executable)
else:
    CACHE_DIR = os.path.dirname(os.path.abspath(__file__))
ZT_CACHE_FILE = os.path.join(CACHE_DIR, '涨停历史缓存.csv')
PRICE_CACHE = os.path.join(CACHE_DIR, 'price_history_cache.csv')
INDUSTRY_CACHE = os.path.join(CACHE_DIR, 'industry_cache.csv')
SENTIMENT_CACHE = os.path.join(CACHE_DIR, 'sentiment_history_cache.csv')
CLS_PLATE_CACHE = os.path.join(CACHE_DIR, 'cls_plate_cache.csv')
OUTPUT_HTML = os.path.join(CACHE_DIR, '主线强度追踪.html')

# === 缓存大小限制 ===
# GitHub Actions 环境下使用更严格的限制，避免仓库/缓存膨胀
if IS_GITHUB_ACTIONS:
    CACHE_MAX_SIZE_MB = 10   # CI 环境: 单个缓存文件最大 10MB
else:
    CACHE_MAX_SIZE_MB = 100  # 本地环境: 单个缓存文件最大 100MB

def trim_cache_file(filepath, date_col='日期', max_size_mb=CACHE_MAX_SIZE_MB, encoding='utf-8-sig'):
    """检查缓存文件大小，如超过限制则删除最老的数据直到满足限制
    
    Args:
        filepath: 缓存文件路径
        date_col: 日期列名 (用于排序，优先删除老数据)
        max_size_mb: 最大文件大小 (MB)
        encoding: CSV编码
    """
    if not os.path.exists(filepath):
        return
    
    file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
    if file_size_mb <= max_size_mb:
        return
    
    try:
        df = pd.read_csv(filepath, dtype=str, encoding=encoding)
        if df.empty or date_col not in df.columns:
            return
        
        original_rows = len(df)
        original_size = file_size_mb
        
        # 获取所有唯一日期并排序 (旧→新)
        unique_dates = sorted(df[date_col].unique())
        
        # 估算每天数据占用的平均大小，然后计算需要删除多少天
        avg_size_per_date = file_size_mb / len(unique_dates) if unique_dates else 0
        excess_mb = file_size_mb - max_size_mb * 0.9  # 目标缩减到90%以留有余量
        dates_to_remove = max(1, int(excess_mb / avg_size_per_date)) if avg_size_per_date > 0 else 1
        
        # 删除最老的日期数据
        dates_to_drop = unique_dates[:dates_to_remove]
        df = df[~df[date_col].isin(dates_to_drop)]
        
        # 保存
        df.to_csv(filepath, index=False, encoding=encoding)
        
        new_size_mb = os.path.getsize(filepath) / (1024 * 1024)
        print(f"  🗑️ 缓存瘦身: {os.path.basename(filepath)} "
              f"{original_size:.1f}MB → {new_size_mb:.1f}MB "
              f"(删除 {len(dates_to_drop)} 天旧数据, "
              f"{original_rows - len(df)} 行)")
        
        # 如果仍然超限，递归继续裁剪
        if new_size_mb > max_size_mb:
            trim_cache_file(filepath, date_col, max_size_mb, encoding)
    except Exception as e:
        print(f"  ⚠️ 缓存瘦身失败 ({os.path.basename(filepath)}): {e}")

# === 邮件发送配置 ===
# 所有敏感信息从环境变量读取，不在代码中硬编码
EMAIL_ENABLE = os.environ.get("EMAIL_ENABLE", "0") == "1"  # 默认关闭，设置 EMAIL_ENABLE=1 启用
EMAIL_SMTP_SERVER = "smtp.qq.com"  # SMTP服务器 (如 QQ邮箱为 smtp.qq.com)
EMAIL_SMTP_PORT = 465  # SMTP端口 (SSL通常为465)
EMAIL_SENDER = os.environ.get("EMAIL_SENDER", "")  # 发件人邮箱地址 (从环境变量/GitHub Secrets读取)
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")  # 发件人邮箱授权码 (从环境变量/GitHub Secrets读取)
_receivers_env = os.environ.get("EMAIL_RECEIVERS", "")
EMAIL_RECEIVERS = [r.strip() for r in _receivers_env.split(",") if r.strip()] if _receivers_env else []

# ============================================================
# 概念板块 → 大主线/细分板块 映射
# ============================================================
# CLS up_tags / plate_name → (细分板块, 大主线)
CONCEPT_TO_SECTOR = {
    # ===== 大周期 =====
    '有色金属': ('有色', '大周期'), '铜': ('有色', '大周期'), '锆': ('有色', '大周期'),
    '黄金': ('有色', '大周期'), '白银': ('有色', '大周期'), '稀土': ('有色', '大周期'),
    '钨': ('有色', '大周期'), '锡': ('有色', '大周期'), '锂': ('有色', '大周期'),
    '铝': ('有色', '大周期'), '锌': ('有色', '大周期'), '钴': ('有色', '大周期'),
    '化工': ('化工', '大周期'), '农药': ('化工', '大周期'), '染料': ('化工', '大周期'),
    '化肥': ('化工', '大周期'), '氟化工': ('化工', '大周期'), '钛白粉': ('化工', '大周期'),
    '有机硅': ('化工', '大周期'), '化纤': ('化工', '大周期'), '煤化工': ('化工', '大周期'),
    '石油化工': ('化工', '大周期'), '磷化工': ('化工', '大周期'), '纯碱': ('化工', '大周期'),
    '橡胶': ('化工', '大周期'), '涂料': ('化工', '大周期'), '聚氨酯': ('化工', '大周期'),
    '玻璃': ('化工', '大周期'), '水泥': ('化工', '大周期'),
    '油气设服': ('油气', '大周期'), '石油': ('油气', '大周期'), '页岩气': ('油气', '大周期'),
    '天然气': ('天然气', '大周期'), '煤炭': ('煤炭', '大周期'),
    '钢铁': ('黑色', '大周期'), '铁矿石': ('黑色', '大周期'),
    # ===== 人工智能 =====
    'AI应用': ('AI应用', '人工智能'), 'AI医疗': ('AI应用', '人工智能'),
    'AI教育': ('AI应用', '人工智能'), 'AI+': ('AI应用', '人工智能'),
    'AI算力': ('算力', '人工智能'), '算力': ('算力', '人工智能'), '算力租赁': ('算力', '人工智能'),
    '华为算力': ('算力', '人工智能'), '液冷': ('液冷', '人工智能'),
    '光通信': ('光通信', '人工智能'), '光纤光缆': ('光通信', '人工智能'),
    '光模块': ('光通信', '人工智能'), 'CPO': ('光通信', '人工智能'),
    '缆线光纤': ('光通信', '人工智能'), '光纤光纤': ('光通信', '人工智能'),
    '机器人': ('机器人', '人工智能'), '机器人概念': ('机器人', '人工智能'),
    '大模型': ('AI应用', '人工智能'), 'AIGC': ('AI应用', '人工智能'),
    'Sora': ('AI应用', '人工智能'), 'Kimi': ('AI应用', '人工智能'),
    'ChatGPT': ('AI应用', '人工智能'), '多模态': ('AI应用', '人工智能'),
    '文生视频': ('AI应用', '人工智能'), '虚拟人': ('AI应用', '人工智能'),
    '知识付费': ('AI应用', '人工智能'), '短剧': ('AI应用', '人工智能'),
    'IDC电源': ('算力', '人工智能'),
    '数据要素': ('AI应用', '人工智能'), '数字经济': ('AI应用', '人工智能'),
    '云计算': ('算力', '人工智能'), '边缘计算': ('算力', '人工智能'),
    '自动驾驶': ('AI应用', '人工智能'), '智能驾驶': ('AI应用', '人工智能'),
    # ===== 半导体 =====
    '存储芯片': ('存储', '半导体'), '存储器': ('存储', '半导体'),
    '半导体': ('半导体', '半导体'), '半导体材料': ('半导体', '半导体'),
    '芯片': ('芯片', '半导体'), 'EDA': ('芯片', '半导体'),
    'PCB': ('PCB', '半导体'), '封装': ('封装', '半导体'), '电子布': ('PCB', '半导体'), '覆铜板': ('PCB', '半导体'), '铜箔': ('PCB', '半导体'),
    '光刻胶': ('半导体', '半导体'), '第三代半导体': ('半导体', '半导体'),
    'IGBT': ('半导体', '半导体'), 'GPU': ('芯片', '半导体'),
    'MCU': ('芯片', '半导体'), 'RISC-V': ('芯片', '半导体'),
    # ===== 商业航天/军工 =====
    '商业航天': ('航天', '商业航天'), '航天': ('航天', '商业航天'),
    '军工': ('军工', '商业航天'), '国防军工': ('军工', '商业航天'),
    '无人机': ('无人机', '商业航天'), '无人机物流': ('无人机', '商业航天'),
    '卫星通信': ('卫星', '商业航天'), '卫星导航': ('卫星', '商业航天'),
    '低空经济': ('低空经济', '商业航天'), 'eVTOL': ('低空经济', '商业航天'),
    '航空发动机': ('军工', '商业航天'), '船舶': ('船舶', '商业航天'),
    # ===== 大消费 =====
    '食品': ('食品', '大消费'), '白酒': ('饮料', '大消费'), '饮料': ('饮料', '大消费'),
    '养老': ('养老', '大消费'), '旅游': ('旅游', '大消费'), '免税': ('免税', '大消费'),
    '医药': ('医药', '大消费'), '新药获批上市': ('医药', '大消费'),
    '农业种植': ('农业', '大消费'), '种业': ('农业', '大消费'),
    '体育': ('体育', '大消费'), '服装': ('服装', '大消费'),
    '家电': ('家电', '大消费'), '化妆品': ('化妆品', '大消费'),
    '教育': ('教育', '大消费'), '传媒': ('传媒', '大消费'),
    '游戏': ('传媒', '大消费'), '影视': ('传媒', '大消费'),
    # ===== 电网 =====
    '智能电网': ('电网', '电网'), '电网设备': ('电网', '电网'),
    '特高压': ('特高压', '电网'), '电缆': ('电缆', '电网'),
    '电力': ('电力', '电网'), '绿电': ('电力', '电网'),
    '核聚变': ('核电', '电网'), '核电': ('核电', '电网'),
    '储能': ('储能', '电网'), '光伏': ('光伏', '电网'),
    '锂电池': ('储能', '电网'), '钠电池': ('储能', '电网'),
    '充电桩': ('充电桩', '电网'), '风电': ('风电', '电网'),
    '氢能': ('氢能', '电网'), '基建': ('电网', '电网'),
}

MAINLINE_NAMES = ['人工智能', '大周期', '商业航天', '半导体', '大消费', '电网']
DACHOUQI_SUBS = ['有色', '化工', '油气', '天然气', '煤炭', '黑色']

# baostock行业代码 → (细分板块, 大主线) 回退映射
INDUSTRY_TO_SECTOR = {
    'C39计算机、通信和其他电子设备制造业': ('AI应用', '人工智能'),
    'I65软件和信息技术服务业': ('AI应用', '人工智能'),
    'I64互联网和相关服务': ('AI应用', '人工智能'),
    'C40仪器仪表制造业': ('机器人', '人工智能'),
    'C38电气机械和器材制造业': ('电网', '电网'),
    'C37铁路、船舶、航空航天和其他运输设备制造业': ('航天机械', '商业航天'),
    'C35专用设备制造业': ('军工', '商业航天'),
    'C34通用设备制造业': ('军工', '商业航天'),
    'C36汽车制造业': ('军工', '商业航天'),
    'B06石油和天然气开采业': ('油气', '大周期'),
    'B07金属矿采选业': ('有色', '大周期'),
    'B09有色金属矿采选业': ('有色', '大周期'),
    'C32有色金属冶炼和压延加工业': ('有色', '大周期'),
    'C31黑色金属冶炼和压延加工业': ('黑色', '大周期'),
    'C26化学原料和化学制品制造业': ('化工', '大周期'),
    'C25石油加工、炼焦和核燃料加工业': ('油气', '大周期'),
    'B08非金属矿采选业': ('化工', '大周期'),
    'B11煤炭开采和洗选业': ('煤炭', '大周期'),
    'D44电力、热力生产和供应业': ('电力', '电网'),
    'C27医药制造业': ('医药', '大消费'),
    'A01农业': ('农业', '大消费'),
    'C14食品制造业': ('食品', '大消费'),
    'C15酒、饮料和精制茶制造业': ('饮料', '大消费'),
    'N77生态保护和环境治理业': ('化工', '大周期'),
    'M74专业技术服务业': ('航天机械', '商业航天'),
    'C33金属制品业': ('军工', '商业航天'),
    'C30非金属矿物制品业': ('化工', '大周期'),
    'L72商务服务业': ('其他', ''),
}

# ============================================================
# CLS API
# ============================================================
def _cls_sign(params):
    ss = ''.join(sorted([f'{k}={v}' for k, v in params.items()])) + ',cailianpressPcANBfjw'
    import hashlib
    return hashlib.md5(ss.encode()).hexdigest()

def fetch_cls_plate_data(date_str):
    """获取某天的CLS概念板块涨停数据"""
    params = {'date': date_str, 'os': 'android', 'sv': '8.3.5', 'ov': '28',
              'net': '', 'app': 'cailianpress', 'channel': '6', 'motif': '0',
              'province_code': '4108', 'token': '', 'mb': 'HUAWEI-ELE-AL00',
              'uid': '', 'sign': '', 'timestamp': str(int(time.time()))}
    params['sign'] = _cls_sign(params)
    url = 'https://x-quote.cls.cn/v2/quote/a/plate/up_down_analysis'
    try:
        r = requests.get(url, params=params, headers={'user-agent': 'okhttp/4.9.0'}, timeout=4)
        if r.status_code == 200:
            data = r.json()
            return data.get('data', {})
    except Exception:
        pass
    return {}

# ============================================================
# 热搜/热门股 API
# ============================================================
def fetch_cls_top20():
    url = "https://api3.cls.cn/v1/hot_stock"
    headers = {"User-Agent": "Mozilla/5.0"}
    params = {"app": "cailianpress", "os": "android", "sv": "835", "sign": "e89e141e1391c13c7d2b99d8c142848c"}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return [item["stock"]["name"] for item in data.get("data", [])[:20]]
    except: pass
    return []

def fetch_eastmoney_top20():
    url_rank = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
    headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
    payload = {"rankType": "1", "pageSize": 20, "pageIndex": 1}
    try:
        resp = requests.post(url_rank, headers=headers, json=payload, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            top20_codes = [item['sc'] for item in data.get('data', [])[:20]]
            secids = ",".join([("1."+c[2:] if c.startswith("SH") else "0."+c[2:]) for c in top20_codes])
            url_detail = "https://push2.eastmoney.com/api/qt/ulist/get"
            params = {
                "ut": "f057cbcbce2a86e2866ab8877db1d059",
                "fltt": 2, "invt": 2, "fields": "f12,f14", "secids": secids,
                "pi": 0, "pz": 20, "po": 1, "np": 1
            }
            em_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36", "Referer": "https://quote.eastmoney.com/"}
            r2 = requests.get(url_detail, params=params, headers=em_headers, timeout=5)
            if r2.status_code == 200:
                d2 = r2.json()
                data_dict = d2.get('data') or {}
                return [s['f14'] for s in data_dict.get('diff', [])]
    except: pass
    return []

def fetch_ths_top20():
    url = "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock"
    headers = {"User-Agent": "Mozilla/5.0"}
    params = {"stock_type": "a", "type": "hour", "list_type": "normal"}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return [item["name"] for item in data.get("data", {}).get("stock_list", [])[:20]]
    except: pass
    return []

_longhu_last_response = {}  # 用于检测 LongHu API 是否返回陈旧数据

def fetch_longhu_sentiment(day=None):
    """从龙虎榜API获取当日涨跌家数与涨跌停数据 (带陈旧检测 + akshare回退)"""
    global _longhu_last_response
    if not day:
        day = get_latest_date().strftime("%Y-%m-%d")
    
    result = None
    
    # === 策略1: LongHu API ===
    url = "https://apphwshhq.longhuvip.com/w1/api/index.php"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; PFEM10 Build/PQ3A.190605.003)",
    }
    params = {
        "a": "GetPlateInfo_w38", "st": "100", "c": "DailyLimitResumption",
        "PhoneOSNew": "1", "DeviceID": "20adcd18-9e93-3bb7-b4d5-c9fd5fa30b3f",
        "VerSion": "5.23.0.4", "Index": "0", "apiv": "w44", "Day": day
    }
    payload = "&".join([f"{k}={v}" for k, v in params.items()])
    try:
        response = requests.post(url, data=payload, headers=headers, timeout=10)
        if response.status_code == 200:
            res_json = response.json()
            nums = res_json.get("nums", {})
            if nums:
                up_val = int(nums.get("SZJS", 0))
                down_val = int(nums.get("XDJS", 0))
                zt_val = int(nums.get("ZT", 0))
                dt_val = int(nums.get("DT", 0))
                
                # 陈旧检测: 如果与上次调用返回完全相同的 up/down 值 → API 缓存失效
                response_key = f"{up_val}_{down_val}_{zt_val}_{dt_val}"
                if _longhu_last_response.get('key') == response_key and _longhu_last_response.get('day') != day:
                    # API 返回了相同的陈旧数据, 不使用 up/down, 但 plates 可能有效
                    result = {
                        "date": day.replace('-', ''),
                        "up": 0,  # 标记为无效, 触发下游补全
                        "down": 0,
                        "zt": zt_val,
                        "dt": dt_val,
                        "flat": 0,
                        "zbl": nums.get("ZBL", 0),
                        "plates": _optimize_fupan_plates(res_json.get("list", []))
                    }
                else:
                    result = {
                        "date": day.replace('-', ''),
                        "up": up_val,
                        "down": down_val,
                        "zt": zt_val,
                        "dt": dt_val,
                        "flat": 0,
                        "zbl": nums.get("ZBL", 0),
                        "plates": _optimize_fupan_plates(res_json.get("list", []))
                    }
                _longhu_last_response = {'key': response_key, 'day': day}
    except Exception:
        pass
    
    # === 策略2: akshare 回退获取 ZT/DT 计数 ===
    if result is None or (result['zt'] == 0 and result['dt'] == 0):
        try:
            import akshare as ak  # type: ignore
            date_str = day.replace('-', '')
            zt_df = ak.stock_zt_pool_em(date=date_str)
            zt_count = len(zt_df) if zt_df is not None and not zt_df.empty else 0
            dt_count = 0
            try:
                dt_df = ak.stock_zt_pool_dtgc_em(date=date_str)
                dt_count = len(dt_df) if dt_df is not None and not dt_df.empty else 0
            except: pass
            
            if result is None:
                result = {"date": date_str, "up": 0, "down": 0, "zt": zt_count, "dt": dt_count, "flat": 0, "zbl": 0, "plates": []}
            else:
                if zt_count > 0: result['zt'] = zt_count
                if dt_count > 0: result['dt'] = dt_count
        except Exception:
            pass
    
    return result

def _optimize_fupan_plates(raw_list):
    """优化涨停原因中的板块及个股数据 (从 fupan_report.py 迁移)"""
    plates = []
    for p in raw_list:
        plate_name = str(p.get("ZSName", ""))
        if "\\u" in plate_name:
            plate_name = plate_name.encode("utf-8").decode("unicode_escape")
            
        stock_list = []
        for s in p.get("StockList", []):
            time_raw = s[6] if len(s) > 6 else 0
            try:
                time_str = datetime.fromtimestamp(time_raw).strftime('%H:%M')
            except:
                time_str = "--:--"
            
            mv_str = f"{float(s[15])/100000000:.2f}亿" if len(s) > 15 and s[15] else "0.00亿"
            seal_str = f"{float(s[8])/100000000:.2f}亿" if len(s) > 8 and s[8] else "0.00亿"
            is_open_str = "是" if len(s) > 10 and int(s[10]) > 0 else "否"

            stock_list.append({
                "code": s[0], "name": s[1], "time": time_str,
                "status": s[9] if len(s) > 9 else "首板",
                "market_value": mv_str,
                "concept": str(s[11]).replace("、", "<br>"),
                "is_open": is_open_str, "seal_order": seal_str,
                "reason": s[17] if len(s) > 17 else "暂无原因"
            })
        plates.append({"plate_name": plate_name, "stocks": stock_list})
    return plates

def generate_wordclouds(plate_stock_data, output_dir):
    """生成热门股票和板块词云"""
    import base64
    from io import BytesIO
    try:
        from collections import Counter
        # pyrefly: ignore [missing-import]
        from wordcloud import WordCloud
        import platform
        
        if platform.system() == "Windows":
            FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"
        else:
            # Linux/GitHub Actions: 尝试多个常见 CJK 字体路径
            FONT_PATH = None
            for _fp in ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
                        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
                        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"]:
                if os.path.exists(_fp):
                    FONT_PATH = _fp
                    break
            if not FONT_PATH:
                raise FileNotFoundError("未找到CJK字体, 请安装 fonts-noto-cjk 或 fonts-wqy-zenhei")
            
        res = {'hot_stock_b64': '', 'plate_b64': ''}
            
        def _to_base64(wc):
            img = wc.to_image()
            buf = BytesIO()
            img.save(buf, format="PNG")
            return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode('utf-8')
            
        print("    [1/2] 抓取全网热门股...")
        cls = fetch_cls_top20()
        em = fetch_eastmoney_top20()
        ths = fetch_ths_top20()
        
        def weighted_list(names):
            weighted = []
            for i, name in enumerate(names):
                weight = len(names) - i
                weighted.extend([name] * weight)
            return weighted
            
        all_stocks = weighted_list(cls) + weighted_list(em) + weighted_list(ths)
        if all_stocks:
            counter = Counter(all_stocks)
            wc_stocks = WordCloud(width=800, height=400, background_color="#161b22", colormap="tab10", font_path=FONT_PATH).generate_from_frequencies(counter)
            res['hot_stock_b64'] = _to_base64(wc_stocks)
            res['top_stocks'] = {'cls': cls, 'em': em, 'ths': ths}  # type: ignore
        
        print("    [2/2] 生成当日涨停属性词云 & 提取Top强势板块...")
        all_concepts = []
        if plate_stock_data:
            for plate in plate_stock_data:
                for stock in plate.get('stock_list', []):
                    tags = stock.get('up_tags', [])
                    valid_tags = [t.strip() for t in tags if t.strip()]
                    if not valid_tags:
                        p_name = plate.get('secu_name', '') or plate.get('plate_name', '')
                        if p_name: valid_tags = [p_name]
                    all_concepts.extend(valid_tags)
                    
        if all_concepts:
            counter_concepts = Counter(all_concepts)
            wc_concepts = WordCloud(width=800, height=400, background_color="#161b22", font_path=FONT_PATH).generate_from_frequencies(counter_concepts)
            res['plate_b64'] = _to_base64(wc_concepts)
            res['top_plates'] = counter_concepts.most_common(20)  # type: ignore
            
        return res
    except Exception as e:
        print(f"  [警告] 词云生成失败 (请确保已安装 wordcloud): {e}")
        return {}

def classify_by_tags(up_tags):
    """根据CLS up_tags分类到 (细分板块, 大主线)"""
    if not up_tags:
        return None, None
    for tag in up_tags:
        if tag in CONCEPT_TO_SECTOR:
            return CONCEPT_TO_SECTOR[tag]
    for tag in up_tags:
        for k, v in CONCEPT_TO_SECTOR.items():
            if k in tag or tag in k:
                return v
    return None, None

def classify_by_plate_name(plate_name):
    """根据CLS板块名分类"""
    if plate_name in CONCEPT_TO_SECTOR:
        return CONCEPT_TO_SECTOR[plate_name]
    for k, v in CONCEPT_TO_SECTOR.items():
        if k in plate_name or plate_name in k:
            return v
    return None, None

# ============================================================
# 数据加载
# ============================================================
def load_and_classify_zt(n_days=60):
    """加载涨停缓存并用概念板块分类"""
    if not os.path.exists(ZT_CACHE_FILE):
        print("[错误] 未找到涨停缓存文件")
        return pd.DataFrame()
    
    df = pd.read_csv(ZT_CACHE_FILE, dtype={'日期': str, '代码': str, '名称': str})
    df = df[df['类型'] == 'ZT'].copy()
    df['连板数'] = pd.to_numeric(df['连板数'], errors='coerce').fillna(1).astype(int)  # type: ignore
    df['日期'] = df['日期'].astype(str).str.strip()
    df['代码'] = df['代码'].str.strip().str.replace('.', '', regex=False)
    
    all_dates = sorted(df['日期'].unique())
    if len(all_dates) > n_days:
        df = df[df['日期'].isin(all_dates[-n_days:])]
        all_dates = sorted(df['日期'].unique())
    
    cached_plates = {}
    if os.path.exists(CLS_PLATE_CACHE):
        try:
            cache_df = pd.read_csv(CLS_PLATE_CACHE, dtype=str)
            for _, row in cache_df.iterrows():
                key = (row['date'], row['code'])
                cached_plates[key] = {'sub': row.get('sub',''), 'mainline': row.get('mainline','')}
        except: pass
    
    stock_plate_map = {}
    # 检查每天是否在缓存中有任意记录（而不仅检查第一只股票）
    cached_dates_set = set()
    for key in cached_plates:
        cached_dates_set.add(key[0])
    dates_to_fetch = [d for d in all_dates if d not in cached_dates_set]
    
    if dates_to_fetch:
        print(f"  📥 抓取 {len(dates_to_fetch)} 天的板块分类数据...")
        
        from concurrent.futures import ThreadPoolExecutor, as_completed
        max_workers = min(10, len(dates_to_fetch))
        
        # 先收集所有结果到内存，避免并发写 CSV
        all_new_cache_rows = []
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_date = {executor.submit(fetch_cls_plate_data, d): d for d in dates_to_fetch}
            for future in as_completed(future_to_date):
                d_str = future_to_date[future]
                try:
                    day_data = future.result()
                    for plate in day_data.get('plate_stock', []):
                        p_name = plate.get('plate_name', '')
                        sub, mainline = classify_by_plate_name(p_name)
                        for stock in plate.get('stock_list', []):
                            code = stock.get('secu_code', '')
                            u_tags = stock.get('up_tags', [])
                            s_sub, s_main = classify_by_tags(u_tags)
                            final_sub = s_sub or sub or '其它'
                            final_main = s_main or mainline or '其它'
                            stock_plate_map[(d_str, code)] = {'sub': final_sub, 'mainline': final_main}
                            all_new_cache_rows.append({'date': d_str, 'code': code, 'sub': final_sub, 'mainline': final_main})
                except Exception as e:
                    print(f"  ⚠️ 抓取板块分类数据异常 {d_str}: {e}")
                time.sleep(0.1)
        
        # 线程池结束后统一写入一次 CSV，避免并发写入竞争
        if all_new_cache_rows:
            new_df = pd.DataFrame(all_new_cache_rows).drop_duplicates(subset=['date','code'])
            mode = 'a' if os.path.exists(CLS_PLATE_CACHE) else 'w'
            header = not os.path.exists(CLS_PLATE_CACHE)
            new_df.to_csv(CLS_PLATE_CACHE, mode=mode, index=False, header=header, encoding='utf-8-sig')
            trim_cache_file(CLS_PLATE_CACHE, date_col='date')

    stock_plate_map.update(cached_plates)
    
    def apply_cls(row):
        m = stock_plate_map.get((row['日期'], row['代码']), {'sub': '其它', 'mainline': '其它'})
        return pd.Series([m['sub'], m['mainline']])
    
    df[['细分板块', '大主线']] = df.apply(apply_cls, axis=1)
    return df

def build_echelon_table(cls_data):
    """构建涨停梯队属性梳理表"""
    clu = cls_data.get('continuous_limit_up', [])
    ps = cls_data.get('plate_stock', [])
    
    stock_plates = {}
    for plate in ps:
        pname = plate.get('secu_name', '') or plate.get('plate_name', '')
        for s in plate.get('stock_list', []):
            code = s.get('secu_code', '')
            tags = s.get('up_tags', [])
            stock_plates.setdefault(code, []).extend(tags)
            if pname and pname not in stock_plates[code]:
                stock_plates[code].append(pname)
    
    echelon = []
    
    for item in clu:
        height = item.get('height', 1)
        stocks = item.get('stock_list', [])
        count = len(stocks)
        
        plate_count = {}
        stock_names = []
        for s in stocks:
            code = s.get('secu_code', '')
            name = s.get('secu_name', '')
            stock_names.append(name)
            plates = stock_plates.get(code, [])
            for p in plates:
                sub, ml = classify_by_tags([p])
                if not ml:
                    sub, ml = classify_by_plate_name(p)
                label = p
                plate_count[label] = plate_count.get(label, 0) + 1
        
        sorted_plates = sorted(plate_count.items(), key=lambda x: -x[1])
        primary = f'{sorted_plates[0][0]}{int(sorted_plates[0][1]/count*100)}%' if sorted_plates else '/'
        secondary = f'{sorted_plates[1][0]}{int(sorted_plates[1][1]/count*100)}%' if len(sorted_plates) > 1 else '/'
        
        stock_details = []
        for s in stocks:
            code = s.get('secu_code', '')
            name = s.get('secu_name', '')
            plates = stock_plates.get(code, [])
            best_sub, best_ml = None, None
            for p in plates:
                sub, ml = classify_by_tags([p])
                if not ml: sub, ml = classify_by_plate_name(p)
                if ml:
                    best_sub, best_ml = sub, ml
                    break
            if not best_ml and plates: best_sub = plates[0]
            stock_details.append({'name': name, 'sub': best_sub or '', 'ml': best_ml or ''})

        echelon.append({
            'height': f'{height}连板', 'count': count,
            'primary': primary, 'secondary': secondary,
            'stocks': stock_names[:5],
            'stock_details': stock_details
        })
    
    first_board_plates = {}
    first_board_count = 0
    first_board_stocks = []
    for plate in ps:
        pname = plate.get('secu_name', '') or plate.get('plate_name', '')
        for s in plate.get('stock_list', []):
            code = s.get('secu_code', '')
            name = s.get('secu_name', '')
            is_lianban = False
            for item in clu:
                for cs in item.get('stock_list', []):
                    if cs.get('secu_code') == code:
                        is_lianban = True
                        break
            if not is_lianban:
                first_board_count += 1
                first_board_stocks.append(name)
                first_board_plates[pname] = first_board_plates.get(pname, 0) + 1
    
    if first_board_count > 0:
        sorted_fp = sorted(first_board_plates.items(), key=lambda x: -x[1])
        primary = f'{sorted_fp[0][0]}{int(sorted_fp[0][1]/first_board_count*100)}%' if sorted_fp else '/'
        secondary = f'{sorted_fp[1][0]}{int(sorted_fp[1][1]/first_board_count*100)}%' if len(sorted_fp) > 1 else '/'
        
        stock_details = []
        for plate in ps:
            pname = plate.get('secu_name', '') or plate.get('plate_name', '')
            for s in plate.get('stock_list', []):
                code = s.get('secu_code', '')
                name = s.get('secu_name', '')
                if name in first_board_stocks:
                    plates = stock_plates.get(code, [])
                    best_sub, best_ml = None, None
                    for p in plates:
                        sub, ml = classify_by_tags([p])
                        if not ml: sub, ml = classify_by_plate_name(p)
                        if ml:
                            best_sub, best_ml = sub, ml
                            break
                    if not best_ml and plates: best_sub = plates[0]
                    stock_details.append({'name': name, 'sub': best_sub or '', 'ml': best_ml or ''})
                    
        unique_details = {d['name']: d for d in stock_details}.values()

        echelon.append({
            'height': '首板', 'count': first_board_count,
            'primary': primary, 'secondary': secondary,
            'stocks': first_board_stocks[:6],
            'stock_details': list(unique_details)
        })
    
    return echelon


# ============================================================
# 强度计算 (逐日百分比归一化)
# ============================================================
def calc_daily_strength(df, group_col):
    """每日各分组强度 = sum(连板数²), 然后按日归一化为百分比占比"""
    df = df.copy()
    df['strength'] = df['连板数'] ** 2
    daily = df.groupby(['日期', group_col])['strength'].sum().reset_index()
    pivot = daily.pivot_table(index='日期', columns=group_col, values='strength', fill_value=0)
    # 归一化: 每日各板块占比 (当日总强度 = 100%), 使图表更均匀协调
    row_sums = pivot.sum(axis=1)
    row_sums = row_sums.replace(0, 1)  # 避免除零
    pivot = pivot.div(row_sums, axis=0) * 100
    return pivot.sort_index()

def calc_ma(strength_df, periods=[5, 10, 20, 30]):
    result = {}
    for col in strength_df.columns:
        result[col] = {}
        for p in periods:
            result[col][f'*{p}'] = strength_df[col].rolling(p, min_periods=1).mean().round(1).tolist()
    return result

def calc_threshold(n_points, thresh1=15.0, thresh2=30.0):
    """固定水平阈值线"""
    return {
        '10日阈值': [thresh1] * n_points,
        '30日阈值': [thresh2] * n_points,
    }

def calc_subsector_returns(classified_df, price_df, dates, periods=[5, 10, 20, 30]):
    """
    计算细分板块的Top N平均累计涨幅 (增强补全版)
    """
    if price_df.empty:
        return {}, {}

    # 1. 准备全量股票-板块映射 (数据补全的核心)
    # 优先级: cls_plate_cache (概念) > classified_df (涨停记录) > INDUSTRY_TO_SECTOR (行业回退)
    global_code_to_sector = {}
    global_code_to_name = {}
    
    # (A) 从行业缓存加载全量基础映射
    if os.path.exists(INDUSTRY_CACHE):
        try:
            idf = pd.read_csv(INDUSTRY_CACHE, dtype=str)
            for _, row in idf.iterrows():
                code, name, ind = row['code'], row['name'], row.get('industry', '')
                global_code_to_name[code] = name
                # 行业回退映射
                sub, ml = INDUSTRY_TO_SECTOR.get(ind, (None, None))
                if sub: global_code_to_sector[code] = sub
        except: pass

    # (B) 从涨停分类加载映射 (覆盖行业映射，更精准)
    if not classified_df.empty:
        c_map = classified_df.drop_duplicates('代码').set_index('代码')
        for code, row in c_map.iterrows():
            if row['细分板块'] and row['细分板块'] != '其它':
                global_code_to_sector[code] = row['细分板块']
            global_code_to_name[code] = row['名称']

    # (C) 建立最终板块-代码列表
    sector_to_codes = {}
    for code, sector in global_code_to_sector.items():
        sector_to_codes.setdefault(sector, []).append(code)

    # 2. 准备价格矩阵
    p_df = price_df.pivot(index='date', columns='code', values='close')
    # 核心：双向填充。先前向填充(停牌)，再后向填充(上市初期或窗口起点缺失)
    p_df = p_df.ffill().bfill()
    
    dt_dates = [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates]
    dt_dates = [d for d in dt_dates if d in p_df.index]
    if not dt_dates: return {}, {}
    
    p_df = p_df.loc[dt_dates].sort_index()
    
    # 核心：基准价格。取每个股票在当前窗口内的第一个非空价格
    # 这样即使股票在窗口中间才出现，也能计算涨幅
    base_prices = p_df.iloc[0]
    base_prices = base_prices.replace(0, np.nan)
    cum_ret_df = (p_df.div(base_prices, axis=1) - 1) * 100
    # 再次填充，确保即使基准日没价格，后续有了价格也能显示
    cum_ret_df = cum_ret_df.ffill().fillna(0)
    
    all_dates_str = [d.replace('-', '') for d in p_df.index]
    
    result = {}
    leaders = {}
    
    # 获取所有定义过的细分板块 (保持数据完整性)
    defined_subs = set(global_code_to_sector.values())
    for s in defined_subs:
        result[s] = {f'*{p}': [0.0] * len(all_dates_str) for p in periods}

    for sector, codes in sector_to_codes.items():
        valid_codes = [c for c in codes if c in cum_ret_df.columns]
        if not valid_codes: continue
        
        sector_rets = cum_ret_df[valid_codes]
        
        for i, d_str in enumerate(all_dates_str):
            day_rets = sector_rets.iloc[i].dropna().sort_values(ascending=False)
            
            for p in periods:
                top_n = day_rets.head(p)
                avg_val = top_n.mean() if not top_n.empty else 0
                result[sector][f'*{p}'][i] = round(float(avg_val), 2)
            
            if not day_rets.empty:
                top_code = day_rets.index[0]
                top_val = day_rets.iloc[0]
                s_name = global_code_to_name.get(top_code, top_code).strip()  # type: ignore
                leaders.setdefault(d_str, {})[sector] = {
                    'name': s_name, 'ret': round(top_val, 2)
                }
                
    return result, leaders

def get_leaders(df, group_col='细分板块'):
    leaders = {}
    for (date, sector), group in df.groupby(['日期', group_col]):
        top = group.loc[group['连板数'].idxmax()]
        leaders.setdefault(date, {})[sector] = {
            'name': top['名称'].strip(), 'lianban': int(top['连板数'])
        }
    return leaders

def get_nday_leaders(classified_df, price_df, group_col='细分板块'):
    if price_df.empty or classified_df.empty:
        return {}
    
    t0 = time.time()
    p_df = price_df.pivot(index='date', columns='code', values='close')
    p_df = p_df.ffill()
    
    code_to_name = classified_df.drop_duplicates('代码').set_index('代码')['名称'].to_dict()
    code_to_sector = classified_df[pd.notna(classified_df[group_col])].drop_duplicates('代码').set_index('代码')[group_col].to_dict()
    all_dates = sorted(classified_df['日期'].unique())
    all_dates_dt = [d[:4]+'-'+d[4:6]+'-'+d[6:] for d in all_dates]
    valid_dates_set = set(all_dates_dt)
    
    result = {}
    periods = [5, 10, 20, 30]
    
    for p in periods:
        result[p] = {}
        ret_df = (p_df / p_df.shift(p) - 1) * 100
        
        day_rets = ret_df[ret_df.index.isin(valid_dates_set)]
        if day_rets.empty: continue
        
        stacked = day_rets.stack().reset_index()
        stacked.columns = ['date', 'code', 'ret']
        
        stacked['sector'] = stacked['code'].map(code_to_sector)
        stacked = stacked.dropna(subset=['sector'])
        
        if stacked.empty: continue
        
        idx = stacked.groupby(['date', 'sector'])['ret'].idxmax()
        top_stocks = stacked.loc[idx]
        
        for _, row in top_stocks.iterrows():
            d_str = row['date'].replace('-', '')
            if d_str not in result[p]: result[p][d_str] = {}
            result[p][d_str][row['sector']] = {
                'name': code_to_name.get(row['code'], row['code']).strip(),
                'ret': round(row['ret'], 2)
            }
            
    print(f"    [性能] get_nday_leaders 完成 (耗时: {time.time()-t0:.2f}s)")
    return result

def rate_mainline(series, threshold=100):
    """基于均值的强度评级 (迁移 11111.py 逻辑)"""
    avg = series.mean() if len(series) > 0 else 0
    if avg > threshold * 3: return 'S'
    elif avg > threshold * 1.5: return 'A'
    elif avg > threshold: return 'B'
    elif avg > threshold * 0.7: return 'C'
    elif avg > threshold * 0.3: return 'D'
    else: return 'N'

def rate_sub(series):
    """基于逐日百分比的细分板块评级"""
    avg = series.tail(5).mean() if len(series) >= 5 else series.mean()
    if avg > 20: return 'S'
    elif avg > 12: return 'A'
    elif avg > 8: return 'B+'
    elif avg > 5: return 'B'
    elif avg > 3: return 'C'
    elif avg > 1: return 'D'
    else: return 'E'


# ============================================================
# N日涨幅排名 (baostock)
# ============================================================
def load_price_cache():
    if os.path.exists(PRICE_CACHE):
        df = pd.read_csv(PRICE_CACHE, dtype={'code': str})
        if not df.empty:
            return df
    return pd.DataFrame()

def _fetch_em_daily_close(trade_date_str, all_codes, retry=3):
    """用东方财富批量API获取某日所有股票的收盘价 (一次请求获取全市场)
    trade_date_str: 'YYYY-MM-DD' 格式
    all_codes: 需要的股票代码列表 (如 ['sh600000', 'sz000001'])
    """
    import requests as _req  # type: ignore
    
    # 东方财富全市场日线数据 API — 按天批量获取
    # 方案: 直接用 akshare 的 stock_zh_a_spot_em 获取全市场快照，再filter
    for attempt in range(retry):
        try:
            # pyrefly: ignore [missing-import]
            import akshare as ak
            # 尝试获取全A股实时行情 (包含收盘价) — 一次请求获取4000+只
            spot_df = ak.stock_zh_a_spot_em()
            if spot_df is not None and not spot_df.empty:
                rows = []
                # 构建代码映射
                code_set = set(c[2:] for c in all_codes)  # 去掉sh/sz前缀
                prefix_map = {}
                for c in all_codes:
                    prefix_map[c[2:]] = c  # '600000' -> 'sh600000'
                
                for _, r in spot_df.iterrows():
                    code_6 = str(r.get('代码', ''))
                    if code_6 in code_set:
                        close_val = r.get('最新价', None)
                        if close_val is not None and float(close_val) > 0:
                            rows.append({
                                'date': trade_date_str,
                                'code': prefix_map.get(code_6, code_6),
                                'close': float(close_val)
                            })
                return rows
        except Exception as e:
            if attempt < retry - 1:
                time.sleep(1)
            continue
    return []

def _fetch_em_hist_batch(codes_batch, start_date, end_date, retry=2):
    """用东方财富接口批量获取一组股票的历史日线收盘价"""
    import requests as _req  # type: ignore
    
    rows = []
    session = _req.Session()
    # 注意: 不要设置 trust_env=False, 用户网络可能需要代理
    
    for code_str in codes_batch:
        symbol = code_str[2:]
        # 确定 secid: sh→1, sz→0
        prefix = code_str[:2]
        secid = f"1.{symbol}" if prefix == 'sh' else f"0.{symbol}"
        
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116",
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "klt": "101",  # 日线
            "fqt": "1",    # 前复权
            "secid": secid,
            "beg": start_date.replace('-', ''),
            "end": end_date.replace('-', ''),
        }
        
        for attempt in range(retry):
            try:
                resp = session.get(url, params=params, timeout=8,
                                   headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"})
                if resp.status_code == 200:
                    data = resp.json()
                    klines = data.get("data", {}).get("klines", [])
                    for kl in klines:
                        parts = kl.split(",")
                        if len(parts) >= 3:
                            rows.append({
                                'date': parts[0],  # YYYY-MM-DD
                                'code': code_str,
                                'close': float(parts[2])
                            })
                    break
            except Exception:
                if attempt < retry - 1:
                    time.sleep(0.5)
    
    session.close()
    return rows

def _fetch_sina_single(args):
    """用新浪API获取单只股票的收盘价 (做为备用数据源)"""
    code_str, start, end = args
    try:
        # pyrefly: ignore [missing-import]
        import akshare as ak
        start_fmt = start.replace('-', '')
        end_fmt = end.replace('-', '')
        # Sina接口需要带 sh/sz 前缀，如 sh600000
        df = ak.stock_zh_a_daily(symbol=code_str, start_date=start_fmt, end_date=end_fmt, adjust='qfq')
        if df is not None and not df.empty:
            rows = []
            for _, r in df.iterrows():
                # Sina 返回的 date 可能是 datetime.date
                d_str = str(r['date']).split(' ')[0]
                rows.append({
                    'date': d_str,
                    'code': code_str,
                    'close': float(r['close'])
                })
            return rows
    except Exception:
        pass
    return []

def _check_bs_login():
    """测试 baostock 登录连通性 (供多进程超时调用)"""

    # pyrefly: ignore [missing-import]
    import baostock as bs
    import os, sys
    old_stdout = sys.stdout
    sys.stdout = open(os.devnull, 'w')
    try:
        lg = bs.login()
        res = (lg.error_code == '0')
        bs.logout()
        return res
    except Exception:
        return False
    finally:
        sys.stdout.close()
        sys.stdout = old_stdout

def _check_bs_data_availability(args):
    """通过独立进程检查 baostock 数据可用性，防止 socket 挂起"""
    start_date_str, latest_zt_str = args
    import baostock as bs
    import sys, os, socket
    socket.setdefaulttimeout(10)
    old_stdout = sys.stdout
    sys.stdout = open(os.devnull, 'w')
    has_target_data = False
    try:
        bs.login()
        rs = bs.query_history_k_data_plus(
            "sh.600000", "date,close",
            start_date=start_date_str, end_date=latest_zt_str,
            frequency="d", adjustflag="2"
        )
        if rs and rs.error_code == '0':
            dates_returned = []
            while rs.next():
                dates_returned.append(rs.get_row_data()[0])
            if latest_zt_str in dates_returned:
                has_target_data = True
        bs.logout()
    except Exception:
        pass
    finally:
        sys.stdout.close()
        sys.stdout = old_stdout
    return has_target_data

def _fetch_bs_chunk(args):
    codes, start, end = args
    import socket
    socket.setdefaulttimeout(15)  # 防止 socket 挂起导致进程死锁
    # pyrefly: ignore [missing-import]
    import baostock as bs
    import sys, os
    
    old_stdout = sys.stdout
    sys.stdout = open(os.devnull, 'w')
    try:
        bs.login()
    finally:
        sys.stdout.close()
        sys.stdout = old_stdout
        
    rows = []
    for code_str in codes:
        if code_str.startswith('sh') or code_str.startswith('sz'):
            bs_code = code_str[:2] + '.' + code_str[2:]
        else:
            continue
        rs = bs.query_history_k_data_plus(
            bs_code, "date,code,close",
            start_date=start, end_date=end,
            frequency="d", adjustflag="2"
        )
        if rs and rs.error_code == '0':
            while rs.next():
                row = rs.get_row_data()
                rows.append({
                    'date': row[0],
                    'code': code_str,
                    'close': float(row[2])
                })
                
    old_stdout = sys.stdout
    sys.stdout = open(os.devnull, 'w')
    try:
        bs.logout()
    finally:
        sys.stdout.close()
        sys.stdout = old_stdout
        
    return rows

def update_price_cache(classified_df):
    price_df = load_price_cache()
    
    latest_zt_date = classified_df['日期'].max()
    latest_zt_dt = datetime.strptime(latest_zt_date, '%Y%m%d')
    latest_zt_str = latest_zt_dt.strftime('%Y-%m-%d')
    
    if not price_df.empty:
        max_price_date = price_df['date'].max()
        if max_price_date >= latest_zt_str:
            return price_df
        
        start_date_dt = datetime.strptime(max_price_date, '%Y-%m-%d') + timedelta(days=1)
        start_date_str = start_date_dt.strftime('%Y-%m-%d')
    else:
        start_date_dt = latest_zt_dt - timedelta(days=90)
        start_date_str = start_date_dt.strftime('%Y-%m-%d')
    
    # 获取全市场活跃股票以保证N日数据完整性
    all_codes = list(classified_df['代码'].unique())
    if os.path.exists(INDUSTRY_CACHE):
        try:
            idf = pd.read_csv(INDUSTRY_CACHE, dtype=str)
            if not idf.empty and 'code' in idf.columns:
                all_codes = idf['code'].unique().tolist()
        except: pass
    if not all_codes:
        all_codes = ['sh600000']
    
    print(f"  📥 价格数据落后, 需全量获取 {len(all_codes)} 只股票数据 ({start_date_str} 至 {latest_zt_str})...")
    
    t0 = time.time()
    GLOBAL_TIMEOUT = 300  # 整个价格更新不超过5分钟
    new_rows = []

    # === 策略1: baostock (首选, 稳定可靠) ===
    try:
        import multiprocessing
        print(f"    🔄 尝试 baostock 获取...")
        bs_ok = False
        try:
            with multiprocessing.Pool(1) as pool:
                res = pool.apply_async(_check_bs_login)
                bs_ok = res.get(timeout=5)
        except multiprocessing.context.TimeoutError:
            print(f"    ⚠️ baostock 登录超时, 服务器无响应")
        except Exception as e:
            print(f"    ⚠️ baostock 连通性检测异常: {e}")
        
        if not bs_ok:
            print(f"    ⚠️ baostock 不可用")
        else:
            # 快速检测 baostock 是否已更新目标日期的数据
            has_target_data = False
            try:
                with multiprocessing.Pool(1) as pool:
                    res = pool.apply_async(_check_bs_data_availability, ((start_date_str, latest_zt_str),))
                    has_target_data = res.get(timeout=10)
            except multiprocessing.context.TimeoutError:
                print(f"    ⚠️ baostock 数据预检超时 (服务器无响应)")
                has_target_data = False
            except Exception as e:
                print(f"    ⚠️ baostock 数据预检异常: {e}")
            
            if not has_target_data:
                print(f"    ⚠️ baostock 尚未更新 {latest_zt_str} 的价格数据，绕过全量获取")
                bs_ok = False
            
            if bs_ok:
                chunk_size = 200
                chunks = [all_codes[i:i + chunk_size] for i in range(0, len(all_codes), chunk_size)]
                cores = max(1, min(4, multiprocessing.cpu_count() - 1))
                print(f"    🚀 baostock + {cores} 进程, 共 {len(chunks)} 个任务块...")
                
                with multiprocessing.Pool(cores) as pool:
                    for i, res in enumerate(pool.imap_unordered(_fetch_bs_chunk, [(c, start_date_str, latest_zt_str) for c in chunks])):
                        new_rows.extend(res)
                        if (i + 1) % 5 == 0 or (i + 1) == len(chunks):
                            pc = min(len(all_codes), (i + 1) * chunk_size)
                            elapsed = time.time() - t0
                            print(f"    已获取 {pc}/{len(all_codes)} 只股票... ({elapsed:.1f}s)")
                        if time.time() - t0 > GLOBAL_TIMEOUT:
                            print(f"    ⚠️ baostock 获取超时, 使用已获取的部分数据")
                            break
                if new_rows:
                    print(f"    ✅ baostock 成功获取 {len(new_rows)} 条记录")
    except Exception as e:
        print(f"    ⚠️ baostock 异常: {e}")

    # === 策略2: 新浪 API (已根据用户要求停止运行，保留代码备查) ===
    if False and not new_rows:
        print(f"    🔄 baostock 无数据, 尝试 新浪 API (Sina) 作为备用...")
        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            max_workers = 30
            print(f"    🚀 新浪 API + {max_workers} 线程并发获取中...")
            
            args_list = [(code, start_date_str, latest_zt_str) for code in all_codes]
            done_count = 0
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {executor.submit(_fetch_sina_single, a): a[0] for a in args_list}
                for future in as_completed(future_map, timeout=GLOBAL_TIMEOUT):
                    try:
                        result = future.result(timeout=20)
                        if result:
                            new_rows.extend(result)
                    except Exception:
                        pass
                    done_count += 1
                    if done_count % 500 == 0 or done_count == len(all_codes):
                        elapsed = time.time() - t0
                        print(f"    已完成 {done_count}/{len(all_codes)} ({elapsed:.1f}s)")
            if new_rows:
                print(f"    ✅ 新浪 API 成功获取 {len(new_rows)} 条记录")
        except TimeoutError:
            print(f"    ⚠️ 新浪API获取超时 ({GLOBAL_TIMEOUT}s)")
        except Exception as e:
            print(f"    ⚠️ 新浪API获取异常: {e}")
    
    elapsed = time.time() - t0
    if new_rows:
        new_df = pd.DataFrame(new_rows)
        if not price_df.empty:
            combined_df = pd.concat([price_df, new_df], ignore_index=True)
            combined_df = combined_df.drop_duplicates(subset=['code', 'date'])
            combined_df = combined_df.sort_values(['code', 'date']).reset_index(drop=True)
        else:
            combined_df = new_df.sort_values(['code', 'date']).reset_index(drop=True)
            
        combined_df.to_csv(PRICE_CACHE, index=False)
        trim_cache_file(PRICE_CACHE, date_col='date', encoding='utf-8')
        print(f"  ✅ 价格缓存已更新, 新增 {len(new_df)} 条记录 (耗时 {elapsed:.1f}s)")
        return combined_df
    else:
        print("  ⚠️ 未获取到新价格数据, 使用现有缓存继续运行")
        return price_df

def calc_nday_returns(price_df, periods=[5, 10, 20, 60]):
    if price_df.empty:
        return pd.DataFrame()
    
    # 1. 快速透视，转换为以日期为行、代码为列的宽表
    p_df = price_df.pivot(index='date', columns='code', values='close').ffill()
    
    # 2. 提取最新日期和最新价格
    latest_date = p_df.index[-1]
    latest_closes = p_df.iloc[-1]
    
    # 3. 过滤有效股票 (最新价格大于0)
    valid_mask = (latest_closes > 0) & latest_closes.notna()
    valid_codes = latest_closes[valid_mask].index
    
    # 4. 构建结果 DataFrame
    res_df = pd.DataFrame(index=valid_codes)
    res_df['code'] = valid_codes
    res_df['date'] = latest_date
    res_df['close'] = latest_closes.loc[valid_codes]
    
    # 5. 向量化计算每个周期的涨幅
    for p in periods:
        col_name = f'{p}日涨幅'
        if len(p_df) > p:
            prev_closes = p_df.iloc[-(p + 1)]
            valid_prev_mask = (prev_closes > 0) & prev_closes.notna()
            
            # 取交集计算
            common_mask = valid_mask & valid_prev_mask
            common_codes = latest_closes[common_mask].index
            
            pcts = pd.Series(index=valid_codes, dtype=float)
            pcts.loc[common_codes] = ((latest_closes.loc[common_codes] / prev_closes.loc[common_codes]) - 1) * 100
            res_df[col_name] = pcts.round(2)
        else:
            res_df[col_name] = None
            
    return res_df.reset_index(drop=True)


# ============================================================
# AI 总结生成
# ============================================================
def generate_ai_summary(advance_decline, ml_strength, sub_strength, echelon, wc_data, sentiment_df, return_leaders=None):
    try:
        # --- 基础数据与排行 ---
        up = advance_decline.get('up', 0)
        down = advance_decline.get('down', 0)
        ad_ratio = up / max(down, 1)
        zt_max = advance_decline.get('zt_max_height', 0)
        
        top_ml_list = []
        if ml_strength is not None and not ml_strength.empty:
            sorted_ml = sorted(ml_strength.iloc[-1].to_dict().items(), key=lambda x: x[1], reverse=True)
            top_ml_list = [k for k, v in sorted_ml if v > 1.5]
            
        top_sub_list = []
        if sub_strength is not None and not sub_strength.empty:
            sorted_sub = sorted(sub_strength.iloc[-1].to_dict().items(), key=lambda x: x[1], reverse=True)
            top_sub_list = [k for k, v in sorted_sub if v > 0.5]

        core_ml = top_ml_list[0] if len(top_ml_list) > 0 else "当前无明确主线"
        core_ml2 = top_ml_list[1] if len(top_ml_list) > 1 else "结构性轮动分支"
        core_sub1 = top_sub_list[0] if len(top_sub_list) > 0 else "活跃细分"
        core_sub2 = top_sub_list[1] if len(top_sub_list) > 1 else "边缘试错分支"
        
        core_subs = "、".join(top_sub_list[:3]) if top_sub_list else "暂无细分聚集"
        ext_subs = top_sub_list[2:7] if len(top_sub_list) > 2 else []
        
        # 获取龙头
        dragon_stock = "核心标的"
        if echelon and '连板' in echelon[0].get('height', ''):
            dragon_stock = ' '.join(echelon[0].get('stocks', [])[:3])

        # --- 节点、风格、核心方向定调 ---
        if ad_ratio > 1.5 and zt_max >= 5:
            node_desc = "情绪与指数共振走强，市场做多意愿高涨，进入主升确认期。"
            style_desc = f"核心主线({core_ml})加速发酵 + 强连板情绪接力"
            title_desc = "情绪共振高潮，主线加速逼空"
        elif ad_ratio > 1.0 and zt_max >= 3:
            node_desc = "多头逐步占据主动，资金围绕核心方向稳步推升，情绪温和回暖。"
            style_desc = f"趋势主升 + {core_ml}方向卡位 + 赚钱效应扩散"
            title_desc = "趋势推升，主线结构性发散"
        elif ad_ratio < 0.5 and zt_max < 3:
            node_desc = "情绪与指数双杀，市场进入冰点或退潮期，资金极度萎缩与避险。"
            style_desc = "缩量防守 + 高位补跌 + 低位试错"
            title_desc = "情绪冰点退潮，市场防御抱团"
        elif ad_ratio < 0.8:
            node_desc = "高位连板亏钱效应显现，资金在老周期退潮与新周期试错中摇摆，呈现高低切。"
            style_desc = "缩量轮动 + 产业逻辑硬切 + 容量中军抱团"
            title_desc = "震荡高低切，防守与轮动并存"
        else:
            node_desc = "资金在多空分歧中博弈，无绝对主导力量，呈现出明显的存量博弈和结构性特征。"
            style_desc = f"泛结构性轮动 + {core_ml}与低位试错并存"
            title_desc = "存量博弈震荡，结构轮动为主"
            
        core_direction = f"以 {core_ml} （{core_subs}） 为核心绝对主线，辅以 {core_ml2} 方向轮动防御。"

        def get_sub_stocks(sub_name):
            stocks = []
            if return_leaders:
                latest_date = max(return_leaders.keys())
                if latest_date and sub_name in return_leaders[latest_date]:
                    stocks.append(return_leaders[latest_date][sub_name]['name'])
            
            unique_stocks = []
            for s in stocks:
                if s not in unique_stocks: unique_stocks.append(s)
                
            if not unique_stocks and echelon:
                for e in echelon:
                    primary = str(e.get('primary', ''))
                    secondary = str(e.get('secondary', ''))
                    if sub_name in primary or sub_name in secondary:
                        unique_stocks.extend(e.get('stocks', [])[:1])
                        if len(unique_stocks) >= 2: break
            
            res_str = []
            for s in unique_stocks[:2]:
                h_str = ""
                if echelon:
                    for e in echelon:
                        if s in e.get('stocks', []):
                            h_str = e.get('height', '')
                            break
                if not h_str: h_str = "容量趋势"
                
                if '连板' in h_str:
                    desc = f"{sub_name}核心，{h_str}身位，资金博弈高弹性接力"
                elif '首板' in h_str:
                    desc = f"{sub_name}新质挖掘，底部承接扎实"
                else:
                    desc = f"{sub_name}大容量中军，沿核心均线温和推升，盘口承接极强"
                res_str.append(f"{s} ({desc})")
                
            return " <br>&nbsp;&nbsp;&nbsp;&nbsp;* ".join(res_str) if res_str else f"核心标的 ({sub_name}方向活跃，筹码沉淀)"

        # --- 一、 核心基本盘 (绝对主线与底座) ---
        core_html = f'''
        <div style="font-size: 13px; color: #8b949e; margin-bottom: 15px;">当前市场资金围绕强逻辑与大容量核心展开深度博弈，以下为绝对主力运作方向：</div>
        <ul style="list-style-type: none; padding-left: 0; line-height: 2.2; font-size: 14px; margin-bottom: 0;">
            <li><b style="color: #ffcc66;">核心领军分支 ({core_sub1}):</b> <span style="color: #58a6ff;">* {get_sub_stocks(core_sub1)}</span> 围绕 <b style="color: #ff7b72;">{core_sub1}</b> 等前排核心方向，资金沿趋势持续推升，盘口承接极强，是当前赚钱效应锚点。代表弹性龙：{dragon_stock}。</li>
            <li><b style="color: #ffcc66;">主线容量中军 ({core_ml}方向):</b> <span style="color: #58a6ff;">* {get_sub_stocks(core_ml2)}</span> 以 <b style="color: #ff7b72;">{core_ml} / {core_ml2}</b> 为代表的大容量板块，承载高位避险与增量长线资金，底部爆量反转或沿均线温和吸筹。</li>
            <li><b style="color: #ffcc66;">侧翼延展与弹性接力 ({core_sub2}):</b> <span style="color: #58a6ff;">* {get_sub_stocks(core_sub2)}</span> 作为主线大容量分歧时的游资接力弹性标的，受资金轮动点火做局部修复。</li>
        </ul>
        '''
        
        # --- 二、 额外拓展的核心分支 (全景补图) ---
        ext_html = ""
        if ext_subs:
            ext_html = f'<div style="font-size: 13px; color: #8b949e; margin-bottom: 15px;">除了上述基石，盘面资金还在以下增量逻辑方向进行了猛攻，这些方向构成了题材轮动的完整拼图：</div><ul style="list-style-type: none; padding-left: 0; line-height: 2.4; font-size: 14px;">'
            for idx, sub in enumerate(ext_subs):
                if idx % 3 == 0:
                    desc = "受事件或政策催化爆发，游资接力与短线情绪修复的核心所在。"
                elif idx % 3 == 1:
                    desc = "作为主线剧烈分歧时的避险防守阵地，资金沿均线温和吸筹或低位试错。"
                else:
                    desc = "趋势容量品种抱团，叠加业绩与产业逻辑双重共振，持续性较好。"
                sub_stocks = get_sub_stocks(sub)
                ext_html += f"<li><b>{idx+1}. <span style='color: #58a6ff;'>{sub}</span></b> —— 逻辑：{desc} <br><span style='color:#8b949e;'>代表标的：<span style='color:#ff7b72;'>{sub_stocks}</span></span></li>"
            ext_html += "</ul>"
        else:
            ext_html = '<div style="font-size: 13px; color: #8b949e;">盘面资金极度集中于核心基石，暂无明显的额外拓展强分支。</div>'

        # --- 三、 盘面推演与系统化交易应对 ---
        plan_html = f'''
        <div style="font-size: 13px; color: #8b949e; margin-bottom: 15px;">综合以上全景，价格往往被量能和情绪裹挟，必须建立符合当前 <span style="color:#ff7b72;">{core_ml}</span> 周期阶段的机械化执行预案：</div>
        <ul style="list-style-type: none; padding-left: 0; line-height: 2.2; font-size: 14px; margin-bottom: 0;">
            <li><span style="background: #21262d; padding: 2px 6px; border-radius: 4px; color: #8b949e; font-size: 12px; margin-right: 8px;">对于绝对核心</span>
                <b>({core_sub1} / 领涨龙头):</b> 当前处于资金抱团共识最强阶段。操作上坚决摒弃主观抄底摸顶的念头。只要量价结构不出现<span style="color:#ff7b72;">“高位爆量滞涨”</span>或<span style="color:#ff7b72;">“放量跌破防守均线”</span>的客观破位信号，就必须死死咬住底仓，让利润在趋势中自然生长。
            </li>
            <li style="margin-top: 10px;"><span style="background: #21262d; padding: 2px 6px; border-radius: 4px; color: #8b949e; font-size: 12px; margin-right: 8px;">对于大容量中军</span>
                <b>({core_ml} 容量标的):</b> 这类票的特征是“进三退二”，不会连续逼空。操作上<span style="color:#ff7b72;">切忌在加速高潮日去追高</span>，应利用盘中分歧回踩核心均线时，进行右侧分批建仓。它们是账户应对高波动的定海神针。
            </li>
            <li style="margin-top: 10px;"><span style="background: #21262d; padding: 2px 6px; border-radius: 4px; color: #f85149; font-size: 12px; margin-right: 8px;">对于边缘轮动与防守支线</span>
                <b>({core_sub2} 等):</b> 严格定义为“轮动套利”或“底仓防守”。如果绝对主线持续吸血，防守线将面临抛压风险。执行上，必须预先设置极其严格的止损/止盈防线，一旦触发，<span style="color:#ff7b72;">系统必须机械化斩仓，绝不拖泥带水！</span>
            </li>
        </ul>
        '''

        html = f'''
        <div style="background: #0d1117; border: 1px solid #30363d; border-radius: 12px; padding: 25px; margin-bottom: 30px; color: #c9d1d9; font-family: sans-serif; box-shadow: 0 4px 15px rgba(0,0,0,0.2);">
            <h2 style="color: #ff4444; font-size: 20px; border-bottom: 2px solid #30363d; padding-bottom: 10px; margin-bottom: 20px; display: flex; align-items: center; gap: 10px;">
                <span style="font-size: 24px;">🎯</span> 盘面深度复盘 ({title_desc})
            </h2>
            
            <div style="background: rgba(22, 27, 34, 0.5); padding: 15px 20px; border-radius: 8px; border-left: 4px solid #58a6ff; margin-bottom: 25px;">
                <div style="margin-bottom: 8px;"><span style="color: #8b949e; display: inline-block; width: 80px; font-weight: bold;">节点:</span> <span style="color: #e6edf3;">{node_desc}</span></div>
                <div style="margin-bottom: 8px;"><span style="color: #8b949e; display: inline-block; width: 80px; font-weight: bold;">风格:</span> <span style="color: #e6edf3;">{style_desc}</span></div>
                <div><span style="color: #8b949e; display: inline-block; width: 80px; font-weight: bold;">核心方向:</span> <b style="color: #ffcc66;">{core_direction}</b></div>
            </div>

            <h3 style="color: #58a6ff; font-size: 16px; margin-top: 25px; margin-bottom: 15px; border-bottom: 1px dashed #30363d; padding-bottom: 8px;">一、 核心基本盘 (绝对主线与底座)</h3>
            {core_html}

            <h3 style="color: #58a6ff; font-size: 16px; margin-top: 30px; margin-bottom: 15px; border-bottom: 1px dashed #30363d; padding-bottom: 8px;">二、 额外拓展的核心分支 (全景补图)</h3>
            {ext_html}

            <div style="background: rgba(248, 81, 73, 0.05); border: 1px solid rgba(248, 81, 73, 0.2); padding: 20px; margin-top: 35px; border-radius: 8px;">
                <h3 style="color: #f85149; font-size: 16px; margin-top: 0; margin-bottom: 15px; display: flex; align-items: center; gap: 8px;">
                    <span style="font-size: 18px;">🛡️</span> 三、 盘面推演与系统化交易应对
                </h3>
                {plan_html}
            </div>
        </div>
        '''
        return html
    except Exception as e:
        print(f"  [警告] AI 总结生成失败: {e}")
        return ""

# ============================================================
# HTML 生成
# ============================================================
def generate_html(ml_strength, sub_strength, ml_ma, sub_ma, ml_thresh, sub_thresh,
                  leaders, dates, dc_strength, ratings, sub_ratings,
                  echelon, top30_data, advance_decline, nday_leaders=None, wc_data=None, sentiment_df=None, plates=None, classified_df=None, return_leaders=None):
    
    if len(dates) > 65:
        dates = dates[-65:]
    if ml_strength is not None:
        ml_strength = ml_strength.tail(65)
    if sub_strength is not None:
        sub_strength = sub_strength.tail(65)
    if ml_ma is not None:
        ml_ma = {k: {p: vals[-65:] for p, vals in d.items()} for k, d in ml_ma.items()}
    if sub_ma is not None:
        sub_ma = {k: {p: vals[-65:] for p, vals in d.items()} for k, d in sub_ma.items()}
    if sentiment_df is not None:
        sentiment_df = sentiment_df.tail(65).copy()

    def fmt(d): return f"{d[:4]}/{d[4:6]}/{d[6:]}" if len(d) == 8 else d
    dates_fmt = [fmt(d) for d in dates]
    
    ml_colors = {'人工智能':'#ff4444','大周期':'#ffcc00','商业航天':'#aa44ff',
                 '半导体':'#44cc44','大消费':'#ff88cc','电网':'#4488ff'}
    ma_colors = {'*5':'#4488ff','*10':'#ff4444','*20':'#cc44cc','*30':'#00cccc'}
    dc_colors = {'有色':'#6688ff','化工':'#ee88cc','油气':'#ff9944','天然气':'#66cc66','煤炭':'#ccaa44','黑色':'#aaaaaa'}

    MATRIX_COLS = {ml: [] for ml in MAINLINE_NAMES}
    for sub, ml in CONCEPT_TO_SECTOR.values():
        if ml in MATRIX_COLS and sub not in MATRIX_COLS[ml]:
            MATRIX_COLS[ml].append(sub)
    if 'PCB' not in MATRIX_COLS['人工智能']: MATRIX_COLS['人工智能'].insert(2, 'PCB')
    if '燃气轮机' not in MATRIX_COLS['人工智能']: MATRIX_COLS['人工智能'].insert(3, '燃气轮机')
    for ml in MATRIX_COLS:
        MATRIX_COLS[ml].append('其它')
        
    def render_matrix_table(title, row_labels, data_provider):
        active_cols = {ml: [] for ml in MAINLINE_NAMES}
        has_any_other = False
        
        for ml in MAINLINE_NAMES:
            for sub in MATRIX_COLS[ml]:
                has_data = False
                for row in row_labels:
                    if data_provider(row, ml, sub):
                        has_data = True
                        break
                if has_data:
                    active_cols[ml].append(sub)
        
        for row in row_labels:
            if data_provider(row, '其它主线', ''):
                has_any_other = True
                break

        html = f'<div class="matrix-wrap"><table class="matrix-table"><tr><th rowspan="2" style="width:70px;">{title}</th>'
        
        for ml in MAINLINE_NAMES:
            colspan = len(active_cols[ml])
            if colspan > 0:
                html += f'<th colspan="{colspan}">{ml}</th>'
        
        if has_any_other:
            html += '<th rowspan="2" class="other-ml-col">其它主线</th>'
            
        html += '</tr><tr>'
        
        for ml in MAINLINE_NAMES:
            for sub in active_cols[ml]:
                html += f'<th class="sub-th">{sub}</th>'
        html += '</tr>'
        
        for row in row_labels:
            html += f'<tr><td class="row-title">{row}</td>'
            for ml in MAINLINE_NAMES:
                for sub in active_cols[ml]:
                    items = data_provider(row, ml, sub)
                    cell_content = '<br>'.join(items) if items else ''
                    html += f'<td>{cell_content}</td>'
            
            if has_any_other:
                other_items = data_provider(row, '其它主线', '')
                html += f'<td>{"<br>".join(other_items) if other_items else ""}</td>'
            html += '</tr>'
            
        html += '</table></div>'
        return html

    hot_stock_html = ''
    if wc_data:
        hot_stock_html += '<h2 class="section-title">🌟 热点股票 & 属性词云</h2><div style="display:flex;gap:20px;flex-wrap:wrap;margin-bottom:20px;">'
        
        top_s = wc_data.get('top_stocks', {})
        if top_s:
            hot_stock_html += '<div style="flex:1;min-width:300px;background:#161b22;padding:15px;border-radius:8px;border:1px solid #21262d;">'
            hot_stock_html += '<h3 style="color:#e0e0e0;margin-bottom:10px;text-align:center;">全网热门互动榜 Top 20</h3>'
            hot_stock_html += '<div class="top30-table" style="height:350px;overflow-y:auto;"><table><tr><th>排名</th><th>财联社</th><th>东方财富</th><th>同花顺</th></tr>'
            c_cls = top_s.get('cls', [])
            c_em = top_s.get('em', [])
            c_ths = top_s.get('ths', [])
            for i in range(20):
                v1 = c_cls[i] if i < len(c_cls) else ''
                v2 = c_em[i] if i < len(c_em) else ''
                v3 = c_ths[i] if i < len(c_ths) else ''
                hot_stock_html += f'<tr><td>{i+1}</td><td>{v1}</td><td>{v2}</td><td>{v3}</td></tr>'
            hot_stock_html += '</table></div></div>'
            
        if wc_data.get('hot_stock_b64'):
            hot_stock_html += '<div style="flex:1;min-width:380px;background:#161b22;padding:15px;border-radius:8px;border:1px solid #21262d;display:flex;flex-direction:column;align-items:center;">'
            hot_stock_html += '<h3 style="color:#e0e0e0;margin-bottom:10px;">🔥 热门股票词云</h3>'
            hot_stock_html += f'<img src="{wc_data["hot_stock_b64"]}" style="max-width:100%;object-fit:contain;border-radius:4px;"></div>'

        tp = wc_data.get('top_plates', [])
        tp_html = ''
        if tp:
            tp_html += '<div style="margin-top:15px;background:#21262d;border-radius:6px;padding:10px;"><h4 style="color:#58a6ff;margin-bottom:8px;">📌 涨停核心概念 Top 20 (含行业Fallback)</h4>'
            tp_html += '<div style="display:flex;flex-wrap:wrap;gap:8px;">'
            for idx, (pname, pcount) in enumerate(tp):
                color = '#ffaa00' if idx < 5 else ('#ff4444' if idx < 10 else '#44aa44')
                tp_html += f'<span style="background:#0d1117;border:1px solid {color};color:{color};padding:3px 8px;border-radius:4px;font-size:12px;">{pname} <b style="color:#fff">{pcount}只</b></span>'
            tp_html += '</div></div>'

        if wc_data.get('plate_b64'):
            hot_stock_html += '<div style="flex:1;min-width:380px;background:#161b22;padding:15px;border-radius:8px;border:1px solid #21262d;display:flex;flex-direction:column;align-items:center;">'
            hot_stock_html += '<h3 style="color:#e0e0e0;margin-bottom:10px;">📋 当日涨停属性词云</h3>'
            hot_stock_html += f'<img src="{wc_data["plate_b64"]}" style="max-width:100%;object-fit:contain;border-radius:4px;">'
            hot_stock_html += tp_html
            hot_stock_html += '</div>'
            
        hot_stock_html += '</div>'

    echelon_html = ''
    if echelon:
        echelon_html = '<h2 class="section-title">🏆 涨停梯队属性梳理</h2>'
        echelon_html += '<div class="echelon-desc">该表按每天连板高度分组，梳理最高高度以上的连板属性。主属性为占比>20%，次属性为占比<20%，主推高度为5</div>'
        echelon_html += '<table class="echelon-table"><tr><th>连板高度</th><th>数量</th><th>主属性</th><th>次属性</th><th>核心成分股</th></tr>'
        import re
        for e in echelon:
            h = e['height']
            c = e['count']
            p = e['primary']
            s = e['secondary']
            
            p_class = ''
            if p != '/' and h != '首板':
                if '100%' in p:
                    p_class = ' class="pct-red"'
                else:
                    p_class = ' class="pct-yellow"'
            
            p_fmt = re.sub(r'(\d+%)$', r'<br>\1', p) if p != '/' else p
            s_fmt = re.sub(r'(\d+%)$', r'<br>\1', s) if s != '/' else s
            
            p_html = f'<div{p_class}>{p_fmt}</div>' if p_class else p_fmt
            
            details = e.get('stock_details', [])
            names = ' '.join([d['name'] for d in details])
            
            echelon_html += f'<tr><td class="height-cell">{h}</td><td class="count-cell">{c}</td><td>{p_html}</td><td>{s_fmt}</td><td>{names}</td></tr>'
        echelon_html += '</table>'

        # ===== 追加矩阵表格 =====
        stock_reason_map = {}
        if plates:
            for p in plates:
                for s in p.get('stocks', []):
                    reason_text = str(s.get('reason', '')).replace('"', '&quot;').replace('\n', ' ')
                    concept_text = str(s.get('concept', '')).replace('"', '&quot;').replace('\n', ' ')
                    if reason_text:
                        tooltip = f"[{s.get('status', '')}] {concept_text} | {reason_text}"
                        if len(tooltip) > 300: tooltip = tooltip[:297] + '...'
                        stock_reason_map[s['name']] = tooltip
                        
        echelon_html += '<h2 class="section-title">🏆 涨停板天梯 (按主线矩阵分布)</h2>'
        ech_map = {e['height']: e.get('stock_details', []) for e in echelon}
        max_h = 1
        for k in ech_map.keys():
            if k != '首板':
                try: max_h = max(max_h, int(k.replace('连板', '')))
                except: pass
        heights_order = [f'{i}连板' for i in range(max_h, 1, -1)] + ['首板']
        
        def ech_provider(row, ml, sub):
            matched = []
            details = ech_map.get(row, [])
            for d in details:
                d_ml = d.get('ml', '')
                d_sub = d.get('sub', '')
                d_name = d["name"]
                
                reason = stock_reason_map.get(d_name, '')
                title_attr = f' title="{reason}"' if reason else ''
                style_attr = ' style="cursor:help;"' if reason else ''
                
                if ml == '其它主线':
                    if d_ml not in MAINLINE_NAMES:
                        tag = f'<span class="ml-tag-other">({d_sub})</span>' if d_sub else ''
                        matched.append(f'<div{title_attr}{style_attr}><b>{d_name}</b><br>{tag}</div>')
                else:
                    if d_ml == ml:
                        if sub == '其它' and d_sub not in MATRIX_COLS[ml]:
                            tag = f'<span class="ml-tag-other">({d_sub})</span>' if d_sub else ''
                            matched.append(f'<div{title_attr}{style_attr}><b>{d_name}</b><br>{tag}</div>')
                        elif d_sub == sub:
                            matched.append(f'<div{title_attr}{style_attr}><b>{d_name}</b></div>')
            return matched
            
        echelon_html += render_matrix_table("分支", heights_order, ech_provider)

    rating_html = ''
    for n in MAINLINE_NAMES:
        r = ratings.get(n, 'N')
        rating_html += f'<span class="rating-item rating-{r}">{n}: {r}级</span>\n'

    ad_html = ''
    if advance_decline:
        up = advance_decline.get("up",0)
        down = advance_decline.get("down",0)
        ad_ratio = round(up / max(down, 1), 2)
        ad_html = f'''<div class="ad-stats">
            <span class="ad-up">上涨 {up}</span>
            <span class="ad-flat">平盘 {advance_decline.get("flat",0)}</span>
            <span class="ad-down">下跌 {down}</span>
            <span class="ad-zt">涨停 {advance_decline.get("zt",0)}</span>
            <span class="ad-dt">跌停 {advance_decline.get("dt",0)}</span>
            <span style="background:rgba(210,153,34,0.1);color:var(--accent-yellow);border:1px solid var(--accent-yellow);padding:8px 20px;border-radius:12px;font-weight:bold;font-size:15px;backdrop-filter:blur(5px);">涨跌比 {up}:{down} ({ad_ratio})</span>
        </div>'''

    R_INT = {
        'S': '极强 (主升浪确立/建议重配领头)',
        'A': '转强 (进攻信号明显/持仓观望)',
        'B+': '活跃 (多空均衡偏多/择机博弈)',
        'B': '蓄势 (震荡格局/等待底部抬升)',
        'C': '转弱 (资金持续撤离/注意仓位)',
        'D': '极弱 (退潮冰点/建议轻仓防守)',
        'N': '潜伏 (横盘整理/暂无明显信号)'
    }

    mainline_table_html = ''
    if sub_ratings:
        mainline_table_html = '<h2 class="section-title">📊 主线数据</h2><div class="ml-table-wrap"><table class="ml-data-table"><tr><th>方向</th>'
        for ml in MAINLINE_NAMES:
            subs = [s for s, (_, m) in sub_ratings.items() if m == ml]
            colspan = max(len(subs), 1)
            r = ratings.get(ml, 'N')
            desc = R_INT.get(r, '')
            mainline_table_html += f'<th colspan="{colspan}" class="ml-header rating-{r}">{ml} ({r}级)<br><span class="rating-desc">{desc}</span></th>'
        mainline_table_html += '</tr><tr><th>分支</th>'
        for ml in MAINLINE_NAMES:
            subs = [(s, r) for s, (r, m) in sub_ratings.items() if m == ml]
            if not subs:
                mainline_table_html += '<td>-</td>'
            for s, r in subs:
                mainline_table_html += f'<td class="sub-cell rating-{r}">{s}<br><small>{r}级</small></td>'
        mainline_table_html += '</tr></table></div>'

    hot_sectors_html = ''

    ml_series: list = []
    ml_leaders = get_leaders(classified_df, '大主线') if classified_df is not None and not classified_df.empty else {}
    for n in MAINLINE_NAMES:
        if n in ml_strength.columns:
            rich_data = []
            for i, d in enumerate(dates):
                val = ml_strength[n].tolist()[i]
                pt = {'value': round(val, 1)}
                
                leader_zt = ml_leaders.get(d, {}).get(n)
                if leader_zt and leader_zt['lianban'] >= 2:
                    pt['label'] = {
                        'show': True, 
                        'formatter': f"{leader_zt['name']}",
                        'position': 'top',
                        'fontSize': 10,
                        'color': ml_colors.get(n, '#fff')
                    }
                rich_data.append(pt)

            ml_series.append({'name': n, 'type': 'line', 'smooth': True,
                'data': rich_data,
                'lineStyle': {'width': 2.5}, 'itemStyle': {'color': ml_colors.get(n, '#fff')},
                'symbol': 'circle', 'symbolSize': 4})
    for tn, td in ml_thresh.items():
        ml_series.append({'name': tn, 'type': 'line', 'smooth': True,  # type: ignore
            'data': [round(v, 1) for v in td],  # type: ignore
            'lineStyle': {'width': 1.5, 'type': 'dashed', 'color': '#ff6666'},  # type: ignore
            'itemStyle': {'color': '#ff6666'}, 'symbol': 'none'})  # type: ignore

    # === 百分比归一化堆叠柱状图 (大主线) ===
    ml_bar = []
    ml_raw_data = {}  # 存储原始值用于tooltip
    for n in MAINLINE_NAMES:
        if n in ml_strength.columns:
            ml_raw_data[n] = [round(v, 1) for v in ml_strength[n].tolist()]
    # 计算每日总值
    ml_day_totals = []
    for i in range(len(dates)):
        total = sum(ml_raw_data.get(n, [0]*len(dates))[i] for n in MAINLINE_NAMES)
        # pyrefly: ignore [bad-argument-type]
        ml_day_totals.append(max(total, 0.01))  # 避免除零
    for n in MAINLINE_NAMES:
        if n in ml_raw_data:
            pct_data = [round(ml_raw_data[n][i] / ml_day_totals[i] * 100, 1) for i in range(len(dates))]
            ml_bar.append({'name': n, 'type': 'bar', 'stack': 't',
                'data': pct_data,
                'itemStyle': {'color': ml_colors.get(n, '#fff')},
                '_raw': ml_raw_data[n]})

    sub_charts_html = ''
    mk_to_period = {'*5': 5, '*10': 10, '*20': 20, '*30': 30}
    
    # 收集所有有数据的板块
    all_sectors_with_data = set()
    if nday_leaders:
        for period_data in nday_leaders.values():
            if not isinstance(period_data, dict): continue
            for date_data in period_data.values():
                if isinstance(date_data, dict):
                    all_sectors_with_data.update(date_data.keys())
    # 也包含sub_ma中的板块 (fallback)
    if isinstance(sub_ma, dict):
        all_sectors_with_data.update(sub_ma.keys())
    
    for idx, sector in enumerate(sorted(all_sectors_with_data)):
        chart_id = f'sub_{idx}'
        s_series: list = []
        
        if nday_leaders:
            # 使用实际N日涨幅数据
            for mk in ['*5', '*10', '*20', '*30']:
                period = mk_to_period[mk]
                label_positions = {'*5': 'top', '*10': 'right', '*20': 'bottom', '*30': 'left'}
                label_pos = label_positions.get(mk, 'top')
                
                rich = []
                for d in dates:
                    leader_info = nday_leaders.get(period, {}).get(d, {}).get(sector) if isinstance(nday_leaders, dict) and period in nday_leaders and isinstance(nday_leaders[period], dict) and d in nday_leaders[period] and isinstance(nday_leaders[period][d], dict) else None
                    if leader_info:
                        pt = {'value': round(leader_info['ret'], 1)}
                        pt['label'] = {'show': True, 'formatter': leader_info['name'],
                            'fontSize': 9, 'color': ma_colors[mk], 'position': label_pos}
                    else:
                        pt = {'value': 0}
                    rich.append(pt)
                s_series.append({'name': f'{sector}{mk}', 'type': 'line', 'smooth': True,
                    'data': rich, 'lineStyle': {'width': 2, 'color': ma_colors[mk]},
                    'itemStyle': {'color': ma_colors[mk]}, 'symbol': 'circle', 'symbolSize': 5})
        else:
            # 回退: 用旧的强度MA数据
            for mk in ['*5', '*10', '*20', '*30']:
                # pyrefly: ignore [unsupported-operation]
                if sector in sub_ma and mk in sub_ma[sector]:
                    # pyrefly: ignore [unsupported-operation]
                    data = sub_ma[sector][mk]
                    s_series.append({'name': f'{sector}{mk}', 'type': 'line', 'smooth': True,
                        'data': data, 'lineStyle': {'width': 2, 'color': ma_colors[mk]},
                        'itemStyle': {'color': ma_colors[mk]}, 'symbol': 'circle', 'symbolSize': 5})
        
        # 固定水平阈值线 (10日=100%, 30日=200%)
        for tn, td in sub_thresh.items():
            clr = '#ffaa44' if '10' in tn else '#4466aa'
            s_series.append({'name': tn, 'type': 'line', 'smooth': False,
                'data': td,
                'lineStyle': {'width': 1.5, 'type': 'dashed', 'color': clr},
                'itemStyle': {'color': clr}, 'symbol': 'none'})  # type: ignore
        
        sr = sub_ratings.get(sector, ('N', ''))[0] if sector in sub_ratings else 'N'
        sub_charts_html += f'''
        <div class="chart-container" id="{chart_id}" style="height:500px;"></div>
        <script>(function(){{
            var c=echarts.init(document.getElementById('{chart_id}'),'dark');
            c.setOption({{title:{{text:'{sector} 涨幅统计 ({sr}级)',left:'center',textStyle:{{color:'#e0e0e0',fontSize:16}}}},
                tooltip:{{trigger:'axis'}},legend:{{data:{json.dumps([s['name'] for s in s_series],ensure_ascii=False)},top:30,textStyle:{{fontSize:11}}}},
                grid:{{left:60,right:30,top:80,bottom:50}},
                xAxis:{{type:'category',data:{json.dumps(dates_fmt)},axisLabel:{{rotate:45,fontSize:10}}}},
                yAxis:{{type:'value',name:'涨幅(%)',axisLabel:{{formatter:'{{value}}%'}}}},
                dataZoom:[{{type:'inside'}},{{type:'slider',bottom:5,height:20}}],
                series:{json.dumps(s_series,ensure_ascii=False)}}});
            window.addEventListener('resize',function(){{c.resize();}});
        }})();</script>'''

    # === 百分比归一化堆叠柱状图 (大周期细分) ===
    dc_bar: list = []
    dc_raw_data = {}
    for s in DACHOUQI_SUBS:
        if s in dc_strength.columns:
            dc_raw_data[s] = [round(v, 1) for v in dc_strength[s].tolist()]
    dc_day_totals = []
    for i in range(len(dates)):
        total = sum(dc_raw_data.get(s, [0]*len(dates))[i] for s in DACHOUQI_SUBS)
        # pyrefly: ignore [bad-argument-type]
        dc_day_totals.append(max(total, 0.01))
    for s in DACHOUQI_SUBS:
        if s in dc_raw_data:
            pct_data = [round(dc_raw_data[s][i] / dc_day_totals[i] * 100, 1) for i in range(len(dates))]
            dc_bar.append({'name': s, 'type': 'bar', 'stack': 't',
                'data': pct_data,
                'itemStyle': {'color': dc_colors.get(s, '#888')},
                '_raw': dc_raw_data[s]})

    top30_html = ''
    if top30_data:
        top30_html += '<h2 class="section-title">【主线数据】N日强势股梯队</h2>'
        
        flat_stocks = {}
        for period, rows in top30_data.items():
            if period == 'top_plates_5d': continue
            for r in rows:
                c = r['code']
                if c not in flat_stocks or r['pct'] > flat_stocks[c]['pct']:
                    flat_stocks[c] = r
                    
        pct_bins = ['150%', '100%', '70%', '50%', '40%', '30%', '挖掘']
        pct_data = {b: [] for b in pct_bins}
        
        for r in flat_stocks.values():
            p = r['pct']
            b = '挖掘'
            if p >= 150: b = '150%'
            elif p >= 100: b = '100%'
            elif p >= 70: b = '70%'
            elif p >= 50: b = '50%'
            elif p >= 40: b = '40%'
            elif p >= 30: b = '30%'
            if b != '挖掘':
                pct_data[b].append(r)
                
        def gain_provider(row, ml, sub):
            matched = []
            for d in pct_data.get(row, []):
                d_ml = d.get('mainline', '')
                d_sub = d.get('sub_sector', '')
                
                if ml == '其它主线':
                    if d_ml not in MAINLINE_NAMES:
                        tag = f'<span class="ml-tag-other">({d_sub})</span>' if d_sub else ''
                        matched.append(f'<div style="color:#ffcc66"><b>{d["name"]}</b><br>{tag}</div>')
                else:
                    if d_ml == ml:
                        if sub == '其它' and d_sub not in MATRIX_COLS[ml]:
                            tag = f'<span class="ml-tag-other">({d_sub})</span>' if d_sub else ''
                            matched.append(f'<div style="color:#ffcc66"><b>{d["name"]}</b><br>{tag}</div>')
                        elif d_sub == sub:
                            matched.append(f'<div style="color:#ffcc66"><b>{d["name"]}</b></div>')
            return matched
            
        active_pct_bins = [b for b in pct_bins[:-1] if pct_data.get(b)]
        top30_html += render_matrix_table("热度梯队", active_pct_bins, gain_provider)
        
        if '5日' in top30_data and 'top_plates_5d' in top30_data:
            top30_html += '<div style="margin:20px 0;padding:15px;background:#161b22;border:1px solid #21262d;border-radius:8px;">'
            top30_html += '<h3 style="color:#58a6ff;margin-bottom:12px;">🌟 5日最强细分板块 (平均涨幅)</h3>'
            top30_html += '<div style="display:flex;gap:12px;flex-wrap:wrap;">'
            for pname, pavg in top30_data['top_plates_5d']:
                chip_cls = "chip-red" if pavg > 0 else "chip-green"
                top30_html += f'<span class="glass-chip {chip_cls}"><b>{pname}</b> : {pavg:+} %</span>'
            top30_html += '</div></div>'

    sentiment_charts_html = ''
    if sentiment_df is not None and not sentiment_df.empty and 'ad_mood' in sentiment_df.columns:
        s_dates = sentiment_df['日期'].tolist()
        s_dates_fmt = [fmt(d) for d in s_dates]
        
        mood_vals = sentiment_df['ad_mood'].tolist()
        mood_raw = [f"{v}%" for v in mood_vals]
        
        up_vals = sentiment_df['up'].tolist()
        dn_vals = [-v for v in sentiment_df['down'].tolist()] 
        
        mood_series: list = []
        mood_series.append({
            'name': '市场情绪', 'type': 'line', 'yAxisIndex': 0,
            'data': [{'value': v, 'mood': r} for v, r in zip(mood_vals, mood_raw)],
            'lineStyle': {'width': 3, 'color': '#d29922'},
            'itemStyle': {'color': '#d29922'},
            'symbol': 'circle', 'symbolSize': 8
        })
        mood_series.append({  # type: ignore
            'name': '上涨家数', 'type': 'bar', 'yAxisIndex': 1, 'data': up_vals,  # type: ignore
            'itemStyle': {'color': '#ff7b72', 'opacity': 0.8}  # type: ignore
        })  # type: ignore
        mood_series.append({  # type: ignore
            'name': '下跌家数', 'type': 'bar', 'yAxisIndex': 1, 'data': dn_vals,  # type: ignore
            'itemStyle': {'color': '#3fb950', 'opacity': 0.8}  # type: ignore
        })  # type: ignore
        
        # === 明日情绪预测 & 仓位建议 计算 ===
        predict_direction = '震荡'
        predict_emoji = '⚖️'
        predict_range_lo = 40
        predict_range_hi = 60
        predict_stars = 2
        position_advice = '半仓观望'
        position_pct = '5成'
        position_color = '#d29922'
        predict_reasons = []
        
        if len(mood_vals) >= 3:
            latest_mood = mood_vals[-1]
            prev_mood = mood_vals[-2]
            prev2_mood = mood_vals[-3]
            mood_delta = latest_mood - prev_mood
            mood_trend = latest_mood - prev2_mood
            
            # 因子1: 情绪动量
            if mood_delta > 5 and mood_trend > 8:
                predict_direction = '偏多'
                predict_emoji = '📈'
                predict_stars += 1
                predict_reasons.append('情绪连续上行,赚钱效应扩散')
            elif mood_delta < -5 and mood_trend < -8:
                predict_direction = '偏空'
                predict_emoji = '📉'
                predict_stars += 1
                predict_reasons.append('情绪加速下行,亏钱效应蔓延')
            elif abs(mood_delta) <= 3:
                predict_reasons.append('情绪波动收敛,多空分歧')
            else:
                predict_reasons.append(f'情绪变化{mood_delta:+.0f},方向待确认')
            
            # 因子2: 绝对位置
            if latest_mood > 75:
                predict_reasons.append('情绪处于高位,注意过热回调')
                if mood_delta < 0:
                    predict_direction = '偏空'
                    predict_emoji = '📉'
                    predict_stars = max(predict_stars, 3)
            elif latest_mood < 25:
                predict_reasons.append('情绪处于冰点,关注超跌反弹')
                if mood_delta > 0:
                    predict_direction = '偏多'
                    predict_emoji = '📈'
                    predict_stars = max(predict_stars, 3)
            
            # 因子3: 涨停数变化
            if len(up_vals) >= 2:
                up_delta = up_vals[-1] - up_vals[-2]
                if up_delta > 200:
                    predict_reasons.append(f'上涨家数大增{int(up_delta)},资金回暖')
                    if predict_direction != '偏空': predict_direction = '偏多'; predict_emoji = '📈'
                elif up_delta < -200:
                    predict_reasons.append(f'上涨家数骤降{int(up_delta)},资金退潮')
                    if predict_direction != '偏多': predict_direction = '偏空'; predict_emoji = '📉'
            
            # 预测区间
            base = latest_mood + mood_delta * 0.5
            predict_range_lo = max(0, int(base - 10))
            predict_range_hi = min(100, int(base + 10))
            
            # 仓位建议
            if predict_direction == '偏多' and latest_mood > 50:
                position_advice = '积极加仓'; position_pct = '7-8成'; position_color = '#ff7b72'
            elif predict_direction == '偏多':
                position_advice = '逢低布局'; position_pct = '6成'; position_color = '#ffcc66'
            elif predict_direction == '偏空' and latest_mood < 30:
                position_advice = '空仓等待'; position_pct = '0-2成'; position_color = '#3fb950'
            elif predict_direction == '偏空':
                position_advice = '减仓防守'; position_pct = '3-4成'; position_color = '#3fb950'
            else:
                position_advice = '半仓观望'; position_pct = '5成'; position_color = '#d29922'
            
            predict_stars = min(predict_stars, 5)
        
        stars_html = '★' * predict_stars + '☆' * (5 - predict_stars)
        reasons_html = '<br>'.join(f'• {r}' for r in predict_reasons[:4]) if predict_reasons else '• 数据不足,无法生成预测'
        
        sentiment_charts_html = f'''
        <h2 class="section-title">🔥 冰火之歌：短线情绪周期统计 <span class="help-icon" data-tip="0-100分制。反映市场短线投机活跃度，20分以下为极冷冰点，80分以上为极热高潮。">?</span></h2>
        <div style="display:flex;gap:20px;flex-wrap:wrap;align-items:stretch;">
        <div class="chart-container" id="sentimentChart" style="height:350px;flex:1;min-width:600px;"></div>
        <div style="min-width:260px;max-width:320px;background:var(--card-bg);border:1px solid var(--border-color);border-radius:12px;padding:20px;backdrop-filter:var(--glass-blur);box-shadow:0 4px 24px rgba(0,0,0,0.3);display:flex;flex-direction:column;gap:12px;">
            <div style="text-align:center;font-size:16px;font-weight:800;color:#58a6ff;border-bottom:1px solid var(--border-color);padding-bottom:10px;">🔮 明日情绪预测</div>
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="color:var(--text-secondary);font-size:12px;">预测方向</span>
                <span style="font-size:20px;font-weight:800;color:{position_color};">{predict_emoji} {predict_direction}</span>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="color:var(--text-secondary);font-size:12px;">预测区间</span>
                <span style="font-size:16px;font-weight:700;color:#e6edf3;">{predict_range_lo}-{predict_range_hi}%</span>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="color:var(--text-secondary);font-size:12px;">信号强度</span>
                <span style="font-size:16px;color:#d29922;">{stars_html}</span>
            </div>
            <div style="border-top:1px solid var(--border-color);padding-top:10px;margin-top:4px;">
                <div style="font-size:13px;font-weight:700;color:#58a6ff;margin-bottom:8px;">📋 今日尾盘仓位建议</div>
                <div style="background:rgba(0,0,0,0.3);border-left:4px solid {position_color};padding:10px 12px;border-radius:0 8px 8px 0;">
                    <div style="font-size:18px;font-weight:800;color:{position_color};margin-bottom:4px;">🏦 {position_advice} ({position_pct})</div>
                    <div style="font-size:11px;color:var(--text-secondary);line-height:1.6;">{reasons_html}</div>
                </div>
            </div>
        </div>
        </div>
        <script>
        (function(){{
            var c=echarts.init(document.getElementById('sentimentChart'),'dark');
            c.setOption({{
                backgroundColor: '#161b22',
                title:{{show:false}},
                tooltip:{{
                    trigger:'axis',
                    backgroundColor: 'rgba(22, 27, 34, 0.95)',
                    borderColor: '#30363d',
                    borderWidth: 1,
                    textStyle: {{ color: '#e6edf3', fontSize: 13 }},
                    formatter: function(params) {{
                        var html = '<b style="color:#58a6ff">' + params[0].name + '</b><br/>';
                        params.forEach(function(p) {{
                            if (p.seriesName === '市场情绪') {{
                                var v = p.value;
                                var tier = v > 80 ? '🔥亢奋' : v > 60 ? '🌤️强势' : v > 40 ? '⚖️平衡' : v > 20 ? '☁️弱势' : '❄️冰点';
                                html += p.marker + p.seriesName + ': <b style="color:#d29922">' + v + '</b> <span style="color:#8b949e">(' + tier + ')</span><br/>';
                            }} else {{
                                html += p.marker + p.seriesName + ': <b style="color:#e6edf3">' + Math.abs(p.value) + '</b> 家<br/>';
                            }}
                        }});
                        return html;
                    }}
                }},
                legend:{{data:['市场情绪','上涨家数','下跌家数'],top:15,textStyle:{{fontSize:11, color:'#8b949e'}}}},
                grid:{{left:50,right:50,top:70,bottom:50}},
                xAxis:{{type:'category',data:{json.dumps(s_dates_fmt,ensure_ascii=False)},axisLabel:{{rotate:45,fontSize:10,color:'#8b949e'}},axisLine:{{lineStyle:{{color:'#30363d'}}}}}},
                yAxis:[
                    {{
                        type:'value', name:'情绪值', position:'left', min:0, max:100,
                        interval: 20,
                        axisLabel: {{ 
                            interval: 0,
                            formatter: function(value) {{
                                var texts = {{0:'冰点', 20:'弱势', 40:'平衡', 60:'强势', 80:'亢奋', 100:'极热'}};
                                return texts[value] || value;
                            }},
                            color:'#8b949e' 
                        }},
                        axisLine:{{show:false}},
                        splitLine:{{show:true, lineStyle:{{color:'#30363d', type:'dashed'}}}}
                    }},
                    {{
                        type:'value', name:'家数', position:'right',
                        axisLabel:{{formatter: function(val){{return Math.abs(val);}}, color:'#8b949e' }},
                        axisLine:{{show:false}},
                        splitLine:{{show:false}}
                    }}
                ],
                dataZoom:[{{type:'inside', start: 0}}, {{type:'slider',bottom:5,height:18,backgroundColor:'#0d1117',borderColor:'#30363d'}}],
                series:[
                    {{
                        name: '市场情绪', type: 'line', yAxisIndex: 0,
                        data:{json.dumps(mood_vals)},
                        connectNulls: true,
                        lineStyle: {{ width: 4, color: '#d29922' }},
                        itemStyle: {{ color: '#d29922' }},
                        symbol: 'circle', symbolSize: 6,
                        areaStyle: {{
                            color: 'rgba(210, 153, 34, 0.1)'
                        }},
                        markArea: {{
                            silent: true,
                            data: [
                                [{{ yAxis: 0, itemStyle: {{ color: 'rgba(63, 185, 80, 0.08)' }} }}, {{ yAxis: 20 }}],
                                [{{ yAxis: 20, itemStyle: {{ color: 'rgba(63, 185, 80, 0.04)' }} }}, {{ yAxis: 40 }}],
                                [{{ yAxis: 40, itemStyle: {{ color: 'rgba(139, 148, 158, 0.04)' }} }}, {{ yAxis: 60 }}],
                                [{{ yAxis: 60, itemStyle: {{ color: 'rgba(255, 123, 114, 0.04)' }} }}, {{ yAxis: 80 }}],
                                [{{ yAxis: 80, itemStyle: {{ color: 'rgba(255, 90, 90, 0.08)' }} }}, {{ yAxis: 100 }}]
                            ]
                        }}
                    }},
                    {{
                        name: '上涨家数', type: 'bar', yAxisIndex: 1, data: {json.dumps(up_vals)},
                        itemStyle: {{ color: '#ff7b72', opacity: 0.6 }}
                    }},
                    {{
                        name: '下跌家数', type: 'bar', yAxisIndex: 1, data: {json.dumps(dn_vals)},
                        itemStyle: {{ color: '#3fb950', opacity: 0.6 }}
                    }}
                ]
            }});
            window.addEventListener('resize',function(){{c.resize();}});
        }})();
        </script>
        '''

    # --- 连板高度分析 (上半部) ---
    lianban_height_html = ''
    if sentiment_df is not None and not sentiment_df.empty and '连板高度' in sentiment_df.columns:
        # 同步日期范围到 dates
        if dates:
            date_set = set(str(d) for d in dates)
            sentiment_df = sentiment_df[sentiment_df['日期'].astype(str).isin(date_set)].copy()
        
        sentiment_df = sentiment_df.reset_index(drop=True)
        lb_dates = sentiment_df['日期'].astype(str).tolist()
        lb_dates_parsed = pd.to_datetime(lb_dates, format='%Y%m%d', errors='coerce')
        weekdays = ["一", "二", "三", "四", "五", "六", "日"]
        lb_dates_fmt = [d.strftime('%m/%d') + '/' + weekdays[d.weekday()] if pd.notnull(d) else str(orig) for d, orig in zip(lb_dates_parsed, lb_dates)]  # type: ignore
        
        lb_data = sentiment_df['连板高度'].fillna(0).tolist()
        db_data = sentiment_df['断板高度'].fillna(0).tolist()
        db_data = [d if d > 0 else None for d in db_data]
        
        # 1. 核心修改：完全按照图一逻辑重构“压力高度”
        # 逻辑：维持前高，遇断板确认新高，遇突破更新新高
        pr_data = []
        curr_pr = lb_data[0] if len(lb_data) > 0 else 0
        for i in range(len(lb_data)):
            if i == 0:
                pr_data.append(curr_pr)
                continue
            if lb_data[i] < lb_data[i-1]:
                curr_pr = lb_data[i-1]
            elif lb_data[i] > curr_pr:
                curr_pr = lb_data[i]
            pr_data.append(curr_pr)
        
        lb_labels = []
        for _, row in sentiment_df.iterrows():
            name = str(row.get('连板股', '')).split(',')[0].strip()
            val_raw = row.get('连板高度', 0)
            lb_val = int(val_raw) if pd.notnull(val_raw) else 0
            if lb_val < 0: lb_val = 0
            lb_labels.append(f"{name} {lb_val}" if name and name != 'nan' else str(lb_val))
            
        db_labels = []
        for _, row in sentiment_df.iterrows():
            val_raw = row.get('断板高度', 0)
            db_val = int(val_raw) if pd.notnull(val_raw) else 0
            if db_val > 0:
                name = '断:' + str(row.get('断板股', '')).split(',')[0].strip()
                db_labels.append(f"{name} {db_val}" if name and name != '断:nan' and name[-1] != ':' else str(db_val))
            else:
                db_labels.append('')
                
        td_details = []
        for _, row in sentiment_df.iterrows():
            lb_raw, db_raw = row.get('连板高度', 0), row.get('断板高度', 0)
            td_details.append({
                'date': str(row.get('日期', '')),
                'lb': int(lb_raw) if pd.notnull(lb_raw) else 0,
                'lb_name': str(row.get('连板股', '')),
                'db': int(db_raw) if pd.notnull(db_raw) else 0,
                'db_name': str(row.get('断板股', '')),
                'mood': str(row.get('情绪', '')).replace('nan', ''),
                'mood_clr': str(row.get('情绪颜色', '')).replace('nan', ''),
            })
            
        # 2. 核心修改：图一逻辑的龙头识别与连线计算
        # 逻辑：一段时间内的最高连板（局部峰值）。直接用数学方法找峰值，并用 高度反推首板日。
        lt_marks = []
        n_lb = len(lb_data)
        for i in range(n_lb):
            h = lb_data[i]
            if h >= 3:  # 设定最小连板数为3板才算作具备连线价值的龙头
                is_peak = False
                if i == n_lb - 1:
                    is_peak = True
                elif lb_data[i] > lb_data[i+1]:
                    is_peak = True
                    
                if is_peak:
                    # 核心突破：直接通过索引减去(高度-1)精准反推首板日，免去模糊查询
                    start_idx = max(0, i - (int(h) - 1))
                    name = str(sentiment_df.iloc[i].get('连板股', '')).split(',')[0].strip()
                    if not name or name == 'nan':
                        name = f"{int(h)}连板"
                    lt_marks.append({
                        'name': name,
                        'sb_idx': start_idx,
                        'peak_idx': i,
                        'peak_h': int(h),
                        'sb_date': str(lb_dates[start_idx]) if start_idx < len(lb_dates) else '',
                        'peak_date': str(lb_dates[i]) if i < len(lb_dates) else ''
                    })
        
        lianban_height_html = f'''
        <h2 class="section-title">🚀 连板高度分析 (市场高度) <span class="help-icon" data-tip="连板数为连续涨停的天数。该图表展示了市场投机高度的溢出与回撤，是情绪周期的核心指标。">?</span></h2>
        <div class="chart-container" id="lianbanChart" style="height:450px;"></div>
        <script>
        var lb_dates_raw = {json.dumps(lb_dates)}; 
        var LBL_lb = {json.dumps(lb_labels, ensure_ascii=False)};
        var DBL_lb = {json.dumps(db_labels, ensure_ascii=False)};
        var TD_lb = {json.dumps(td_details, ensure_ascii=False)};
        var LTM_lb = {json.dumps(lt_marks, ensure_ascii=False)};
        
        (function(){{
            var c=echarts.init(document.getElementById('lianbanChart'),'dark');
            var opt = {{
                backgroundColor: '#161b22',
                grid: {{ left: 50, right: 20, top: 40, bottom: 40 }},
                tooltip: {{
                    trigger: 'axis',
                    backgroundColor: 'rgba(22, 27, 34, 0.95)',
                    borderColor: '#30363d',
                    borderWidth: 1,
                    textStyle: {{ color: '#e6edf3', fontSize: 13 }},
                    formatter: function(p) {{
                        var i = p[0].dataIndex, d = TD_lb[i];
                        if(!d) return '';
                        var h = '<b style="color:#58a6ff">' + d.date + '</b>  <span style="color:' + (d.mood_clr||'#fff') + '">' + (d.mood||'') + '</span><br>';
                        h += '<span style="color:#58a6ff">● 连板高度 ' + d.lb + '板  ' + (d.lb_name && d.lb_name !== 'nan'?d.lb_name:'') + '</span><br>';
                        if (d.db > 0) h += '<span style="color:#ff7b72">● 断板高度 ' + d.db + '板  ' + (d.db_name && d.db_name !== 'nan'?d.db_name:'') + '</span><br>';

                        var day_lts = LTM_lb.filter(m => String(m.peak_date || m.date) === String(d.date));
                        if (day_lts.length > 0) {{
                            day_lts.forEach(function(m){{
                                h += '<span style="color:#ff8800">▲ 龙头首板: ' + m.name + ' @ ' + m.sb_date + '</span><br>';
                            }});
                        }}
                        return h;
                    }}
                }},
                legend: {{ show: true, data: ['连板高度', '压力高度', '断板高度'], top: 10, right: 30, textStyle: {{ fontSize: 12, color: '#8b949e' }} }},
                xAxis: {{
                    type: 'category', data: {json.dumps(lb_dates_fmt, ensure_ascii=False)},
                    axisLine: {{ lineStyle: {{ color: '#333' }} }},
                    axisLabel: {{ color: '#666', fontSize: 10, interval: 'auto' }},
                    axisTick: {{ show: true, lineStyle: {{ color: '#222' }} }},
                }},
                yAxis: {{
                    type: 'value', min: 0, minInterval: 1,
                    axisLine: {{ show: false }},
                    axisLabel: {{ color: '#555', fontSize: 11 }},
                    splitLine: {{ show: true, lineStyle: {{ color: '#161616' }} }}
                }},
                dataZoom: [
                    {{ type: 'inside', xAxisIndex: 0, start: 0, end: 100 }},
                    {{ type: 'slider', xAxisIndex: 0, start: 0, end: 100, height: 16, bottom: 4,
                       backgroundColor: '#0d1117', borderColor: '#30363d', fillerColor: 'rgba(88, 166, 255, 0.15)' }}
                ],
                series: [
                    {{
                        name: '连板高度', type: 'line', data: {json.dumps(lb_data)}, z: 10,
                        symbol: 'circle', symbolSize: 8,
                        lineStyle: {{ color: '#58a6ff', width: 3 }},
                        itemStyle: {{ color: '#58a6ff', borderColor: '#e6edf3', borderWidth: 1 }},
                        label: {{
                            show: true, position: 'top', color: '#58a6ff', fontSize: 11, fontWeight: 'bold',
                            backgroundColor: 'rgba(13, 17, 23, 0.7)', padding: [2, 4], borderRadius: 4,
                            formatter: function(p) {{ return LBL_lb[p.dataIndex]; }}
                        }}
                    }},
                    {{
                        // 新增：图一逻辑的压力高度 (青色实线)
                        name: '压力高度', type: 'line', data: {json.dumps(pr_data)}, z: 8,
                        symbol: 'circle', symbolSize: 4,
                        lineStyle: {{ color: '#00e5ff', width: 2 }},
                        itemStyle: {{ color: '#00e5ff' }}
                    }},
                    {{
                        name: '断板高度', type: 'line', data: {json.dumps(db_data)}, z: 9,
                        symbol: 'rect', symbolSize: 6, connectNulls: false,
                        lineStyle: {{ color: '#ff7b72', width: 2, type: 'dotted' }},
                        itemStyle: {{ color: '#ff7b72' }},
                        label: {{
                            show: true, position: 'bottom', color: '#ff7b72', fontSize: 10,
                            backgroundColor: 'rgba(13, 17, 23, 0.7)', padding: [2, 4], borderRadius: 4,
                            formatter: function(p) {{ return DBL_lb[p.dataIndex]; }}
                        }}
                    }}
                ]
            }};
            
            // 新增：图一逻辑的纯正首板起涨连线 (粗红实线)
            if (LTM_lb && LTM_lb.length > 0) {{
                var sbScatterData = [];
                var markLineData = [];
                for (var k = 0; k < LTM_lb.length; k++) {{
                    var m = LTM_lb[k];
                    // 直接使用 Python 端计算好的精准索引
                    var s_idx = m.sb_idx;
                    var p_idx = m.peak_idx;
                    
                    sbScatterData.push({{
                        value: [s_idx, 0], // 首板起点从底部开始画
                        name: m.name,
                        peak_h: m.peak_h,
                        sb_date: m.sb_date
                    }});
                    markLineData.push([
                        {{ coord: [s_idx, 0] }},
                        {{ coord: [p_idx, m.peak_h] }}
                    ]);
                }}
                opt.series.push({{
                    name: '龙头主升连线',
                    type: 'scatter',
                    xAxisIndex: 0,
                    yAxisIndex: 0,
                    data: sbScatterData,
                    symbol: 'circle',
                    symbolSize: 6,
                    z: 15,
                    itemStyle: {{ color: '#ff3333' }},
                    label: {{
                        show: true, position: 'bottom', color: '#ff3333', fontSize: 10, 
                        backgroundColor: 'rgba(22, 27, 34, 0.8)', padding: [1, 2], borderRadius: 2,
                        formatter: function(p) {{ return p.data.name; }}
                    }},
                    markLine: {{
                        silent: true,
                        symbol: ['none', 'none'],
                        // 使用实线，模拟图一从首板直插云霄的效果
                        lineStyle: {{ color: '#ff3333', width: 2, type: 'solid' }},
                        label: {{ show: false }},
                        data: markLineData
                    }}
                }});
            }}
            
            c.setOption(opt);
            window.addEventListener('resize',function(){{c.resize();}});
        }})();
        </script>
        '''

    fupan_html = ""

    sentiment_val_str = '---'
    sentiment_color = '#58a6ff'
    sentiment_text = '❄️ 情绪冰点 (Panic/Frozen)'
    if sentiment_df is not None and not sentiment_df.empty:
        sentiment_val = sentiment_df['ad_mood'].iloc[-1]
        sentiment_val_str = f"{sentiment_val}%"
        sentiment_color = (
            '#ff5a5a' if sentiment_val > 85 else
            '#ff7b72' if sentiment_val > 65 else
            '#ffcc66' if sentiment_val > 35 else
            '#aff5b4' if sentiment_val > 15 else
            '#58a6ff'
        )
        sentiment_text = (
            '🔥 极度亢奋 (High Overheat)' if sentiment_val > 85 else
            '🌤️ 赚钱效应活跃 (Active)' if sentiment_val > 65 else
            '⚖️ 震荡分歧 (Neutral)' if sentiment_val > 35 else
            '☁️ 亏钱效应加剧 (Weakening)' if sentiment_val > 15 else
            '❄️ 情绪冰点 (Panic/Frozen)'
        )

    ai_summary_html = generate_ai_summary(advance_decline, ml_strength, sub_strength, echelon, wc_data, sentiment_df, return_leaders)

    # 动态生成量化择时模块
    timing_res = generate_timing_signal(sentiment_df, advance_decline)
    timing_html = f'''
    <div style="background: rgba(0,0,0,0.5); border: 2px solid {timing_res['color']}; border-radius: 12px; padding: 20px; margin-bottom: 30px; display: flex; align-items: center; justify-content: space-between; box-shadow: 0 0 15px {timing_res['color']}40;">
        <div style="flex: 1;">
            <div style="color: #8b949e; font-size: 13px; font-weight: bold; margin-bottom: 5px; text-transform: uppercase;">量化择时雷达 (Quant Timing Radar)</div>
            <div style="font-size: 26px; font-weight: 800; color: {timing_res['color']}; margin-bottom: 8px;">{timing_res['action']}</div>
            <div style="color: #e6edf3; font-size: 14px;">{timing_res['desc']}</div>
        </div>
        <div style="text-align: right; background: {timing_res['color']}20; padding: 15px 25px; border-radius: 10px; border: 1px solid {timing_res['color']}60;">
            <div style="color: {timing_res['color']}; font-size: 16px; font-weight: bold; margin-bottom: 5px;">预警级别: {timing_res['level']}</div>
            <div style="color: #fff; font-size: 18px; font-weight: 800;">建议仓位: {timing_res['position']}</div>
        </div>
    </div>
    '''

    html = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>主线强度追踪系统 V3 - 量化投研决策终端</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
    :root {{
        --bg-color: #0d1117;
        --card-bg: rgba(22, 27, 34, 0.7);
        --glass-bg: rgba(22, 27, 34, 0.4);
        --border-color: rgba(48, 54, 61, 0.8);
        --text-primary: #e6edf3;
        --text-secondary: #8b949e;
        --accent-red: #f85149;
        --accent-red-deep: #ee4444;
        --accent-green: #3fb950;
        --accent-yellow: #d29922;
        --accent-blue: #58a6ff;
        --glass-blur: blur(12px);
    }}
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
        background: var(--bg-color);
        color: var(--text-primary);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji";
        padding: 40px 20px;
        line-height: 1.5;
    }}
    .wrapper {{ max-width: 1400px; margin: 0 auto; }}
    
    .dashboard-header {{ text-align: center; margin-bottom: 40px; border-bottom: 1px solid var(--border-color); padding-bottom: 30px; }}
    .dashboard-header h1 {{ font-size: 32px; color: var(--accent-red-deep); display: flex; align-items: center; justify-content: center; gap: 12px; }}
    .dashboard-header .subtitle {{ color: var(--text-secondary); font-size: 14px; margin-top: 8px; }}
    
    .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 20px; margin-bottom: 40px; }}
    .summary-card {{ 
        background: var(--card-bg); 
        backdrop-filter: var(--glass-blur);
        border: 1px solid var(--border-color); 
        border-radius: 16px; 
        padding: 24px; 
        text-align: center; 
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); 
        box-shadow: 0 4px 24px rgba(0,0,0,0.2);
    }}
    .summary-card:hover {{ 
        transform: translateY(-5px); 
        border-color: var(--accent-red-deep); 
        background: rgba(238, 68, 68, 0.05);
        box-shadow: 0 8px 32px rgba(238, 68, 68, 0.15); 
    }}
    .summary-card .label {{ font-size: 12px; color: var(--text-secondary); margin-bottom: 10px; text-transform: uppercase; letter-spacing: 1px; }}
    .summary-card .value {{ font-size: 24px; font-weight: 800; }}
    
    .section-title {{ font-size: 20px; color: var(--accent-red-deep); margin: 40px 0 20px; padding-left: 14px; border-left: 4px solid var(--accent-red-deep); display: flex; align-items: center; gap: 10px; }}
    .section-title small {{ font-weight: normal; font-size: 12px; color: var(--text-secondary); margin-left: auto; }}

    .chart-container {{ width: 100%; margin-bottom: 30px; border: 1px solid var(--border-color); border-radius: 12px; background: var(--card-bg); padding: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }}
    
    .rating-bar {{ display: flex; justify-content: center; gap: 8px; margin-bottom: 25px; flex-wrap: wrap; }}
    .rating-item {{ padding: 6px 14px; border-radius: 6px; font-size: 13px; font-weight: 600; text-transform: uppercase; }}
    .rating-S {{ background: #ff4444; color: #fff; }} .rating-A {{ background: #ff8800; color: #fff; }}
    .rating-B\\+, .rating-B {{ background: #44aa44; color: #fff; }}
    .rating-C {{ background: #cccc00; color: #333; }} .rating-D {{ background: #666; color: #ddd; }}
    .rating-E, .rating-N {{ background: #333; color: #888; }}

    .ml-table-wrap {{ overflow-x: auto; margin: 10px 0 20px; }}
    .ml-data-table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    .ml-data-table th, .ml-data-table td {{ padding: 6px 8px; border: 1px solid #333; text-align: center; }}
    .ml-data-table .ml-header {{ font-weight: bold; font-size: 14px; }}
    .ml-data-table .sub-cell {{ font-size: 11px; }}

    .ad-stats {{ display: flex; justify-content: center; gap: 15px; margin: 25px 0; flex-wrap: wrap; }}
    .ad-stats span {{ padding: 10px 24px; border-radius: 10px; font-weight: 700; font-size: 16px; box-shadow: 0 2px 4px rgba(0,0,0,0.2); }}
    .ad-up {{ background: #2d1a1a; color: #f85149; border: 1px solid #da3633; }}
    .ad-down {{ background: #1a2d1a; color: #3fb950; border: 1px solid #238636; }}
    .ad-flat {{ background: #2d2d1a; color: #d29922; border: 1px solid #9e6a03; }}
    .ad-zt {{ background: #4a1a1a; color: #ff7b72; border: 1px solid #da3633; }}
    .ad-dt {{ background: #1a4a1a; color: #56d364; border: 1px solid #238636; }}

    table {{ width: 100%; border-collapse: collapse; font-size: 14px; color: var(--text-primary); }}
    th, td {{ padding: 12px 10px; border: 1px solid var(--border-color); text-align: center; }}
    th {{ background: #1f242c; color: var(--accent-red-deep); font-weight: 600; font-size: 13px; }}
    tr:nth-child(even) {{ background: #1b2128; }}
    tr:hover {{ background: #21262d; }}

    .echelon-table th {{ background: #b22222; color: #fff; padding: 8px 10px; text-align: center; border: 1px solid #444; font-weight: bold; }}
    .echelon-table td {{ padding: 6px 8px; border: 1px solid #333; text-align: center; }}
    .echelon-table .height-cell {{ font-weight: bold; background: #1a1a2e; color: #58a6ff; }}
    .pct-red {{ background: #b22222; color: #fff; font-weight: bold; display: inline-block; padding: 2px 6px; border-radius: 4px; }}
    .pct-yellow {{ background: #ccaa00; color: #333; font-weight: bold; display: inline-block; padding: 2px 6px; border-radius: 4px; }}
    
    .matrix-wrap {{ overflow-x: auto; margin: 20px 0; border-radius: 12px; border: 1px solid var(--border-color); }}
    .matrix-table {{ font-size: 12px; }}
    .matrix-table th {{ background: #21262d; }}
    .matrix-table .sub-th {{ font-size: 10px; opacity: 0.8; background: #161b22; }}
    .matrix-table .row-title {{ background: #21262d; font-weight: 800; color: var(--accent-red-deep); }}
    .matrix-table td {{ background: var(--card-bg); vertical-align: top; }}
    .matrix-table td div {{ margin: 4px 0; padding: 4px; border-bottom: 1px dashed #30363d; transition: background 0.2s; border-radius: 4px; }}
    .matrix-table td div:hover {{ background: #30363d; }}
    .matrix-table td div:last-child {{ border-bottom: none; }}
    .ml-tag-other {{ color: var(--text-secondary); font-size: 10px; font-style: italic; }}

    .rating-desc {{ font-size: 11px; font-weight: normal; margin-left: 5px; opacity: 0.8; }}
    


    .glass-chip {{ background: rgba(255,255,255,0.05); border: 1px solid var(--border-color); padding: 5px 12px; border-radius: 20px; font-size: 12px; color: var(--text-primary); backdrop-filter: blur(4px); transition: all 0.2s; }}
    .glass-chip:hover {{ border-color: var(--accent-red-deep); background: rgba(238,68,68,0.1); }}
    .chip-red {{ border-color: #f85149; color: #ff7b72; background: rgba(248,81,73,0.05); }}
    .chip-green {{ border-color: #3fb950; color: #56d364; background: rgba(63,185,80,0.05); }}

    .hot-sectors {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px; margin: 20px 0; }}
    .hot-period {{ background: var(--card-bg); border: 1px solid var(--border-color); border-radius: 12px; padding: 18px; }}
    .hot-period h4 {{ color: var(--text-primary); margin-bottom: 15px; font-size: 15px; display: flex; align-items: center; justify-content: space-between; }}
    .hot-bar {{ display: flex; align-items: center; gap: 10px; margin: 8px 0; }}
    .hot-name {{ width: 80px; font-size: 12px; text-align: right; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .hot-fill {{ height: 20px; border-radius: 4px; min-width: 6px; box-shadow: inset 0 0 5px rgba(0,0,0,0.2); }}
    .hot-count {{ font-size: 12px; color: var(--text-secondary); font-weight: 600; min-width: 25px; }}

    .plate-section {{ background: var(--card-bg); margin-bottom: 25px; border-radius: 12px; overflow: hidden; border: 1px solid var(--border-color); }}
    .p-title {{ padding: 14px 20px; font-weight: 700; color: #fff; background: linear-gradient(135deg, #238636, #2ea043); display: flex; justify-content: space-between; align-items: center; }} 
    .p-title.red {{ background: linear-gradient(135deg, #da3633, #f85149); }}
    .reason-item {{ padding: 20px; border-bottom: 1px solid var(--border-color); }} 
    .r-header {{ display: grid; grid-template-columns: 1.2fr 0.8fr 0.8fr 0.9fr 1.5fr 0.6fr 0.9fr; gap: 10px; font-size: 12px; margin-bottom: 12px; align-items: center; text-align: center; color: var(--text-secondary); }} 
    .r-header.list-header {{ background: #21262d; padding: 10px 20px; font-weight: 600; color: var(--accent-blue); }} 
    .r-name {{ text-align: left; font-size: 15px; font-weight: 800; color: #ffcc66; }} 
    .r-status {{ color: var(--accent-blue); font-weight: 700; }} 
    .r-concept {{ color: var(--accent-yellow); line-height: 1.4; }} 
    .r-body {{ font-size: 14px; color: var(--text-primary); background: #010409; padding: 15px; border-radius: 10px; line-height: 1.7; border-left: 5px solid #30363d; margin-top: 5px; }}
    
    .help-icon {{ display: inline-flex; align-items: center; justify-content: center; width: 16px; height: 16px; background: var(--text-secondary); color: var(--bg-color); border-radius: 50%; font-size: 11px; font-weight: 800; margin-left: 6px; cursor: help; vertical-align: middle; position: relative; }}
    .help-icon:hover {{ background: var(--accent-red-deep); }}
    .help-icon:hover::after {{
        content: attr(data-tip);
        position: absolute;
        bottom: 125%;
        left: 50%;
        transform: translateX(-50%);
        background: #1f242c;
        color: #fff;
        padding: 8px 12px;
        border-radius: 6px;
        font-size: 12px;
        white-space: normal;
        width: 220px;
        box-shadow: 0 5px 15px rgba(0,0,0,0.4);
        border: 1px solid var(--border-color);
        z-index: 100;
        font-weight: normal;
        line-height: 1.4;
    }}

    .glossary {{ background: var(--card-bg); border: 1px solid var(--border-color); border-radius: 12px; padding: 25px; margin-top: 50px; margin-bottom: 40px; }}
    .glossary h3 {{ margin-bottom: 15px; color: var(--accent-red-deep); border-bottom: 1px solid var(--border-color); padding-bottom: 10px; }}
    .glossary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }}
    .glossary-item b {{ color: var(--accent-yellow); display: block; margin-bottom: 4px; }}
    .glossary-item p {{ font-size: 13px; color: var(--text-secondary); }}
    
    @media (max-width: 1000px) {{
        .r-header {{ grid-template-columns: 1fr 1fr; gap: 5px; }}
        .list-header {{ display: none; }}
    }}
</style>
</head>
<body>
<div class="wrapper">
    <div class="dashboard-header">
        <h1>🔍 主线强度追踪终端 <small style="font-size: 12px; background: #238636; color: #fff; padding: 2px 8px; border-radius: 10px; vertical-align: middle;">V3.0 专业版</small></h1>
        <div class="subtitle">量化维度: 财联社CLS全域概念 / 涨停能量矩阵 / 多周期强度映射</div>
        <div class="subtitle" style="margin-top: 15px;">数据更新时间: <span style="color: var(--accent-blue); font-weight: bold;">{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</span>  |  交易日: <span style="color: var(--accent-yellow); font-weight: bold;">{dates[-1] if dates else '---'}</span></div>
    </div>

    {timing_html}

    <div class="summary-grid">
        <div class="summary-card">
            <div class="label">盘面情绪指数 <span class="help-icon" data-tip="基于全市场涨跌家数比率计算。反映整体赚钱效应，数值越高代表市场氛围越好。">?</span></div>
            <div class="value" style="color: var(--accent-yellow);">{sentiment_val_str}</div>
            <div style="font-size: 11px; color: var(--text-secondary); margin-top: 5px;">
                状态: <b style="color:{sentiment_color}; font-size: 14px; text-shadow: 0 0 10px rgba(210,153,34,0.3);">{sentiment_text}</b>
            </div>
        </div>
        <div class="summary-card">
            <div class="label">连板最高高度 <span class="help-icon" data-tip="当前全市场个股连续涨停的最大天数，反映短线资金的风险偏好上限。">?</span></div>
            <div class="value" style="color: var(--accent-red);">{advance_decline.get('zt_max_height', '---')}板</div>
            <div style="font-size: 11px; color: var(--text-secondary); margin-top: 5px;">(昨日最高板: {advance_decline.get('zt_max_height_prev', '---')}板)</div>
        </div>
        <div class="summary-card">
            <div class="label">市场涨停总数</div>
            <div class="value">{advance_decline.get('zt', 0)}家</div>
            <div style="font-size: 11px; color: var(--text-secondary); margin-top: 5px;">(昨日: {advance_decline.get('zt_prev', '---')}家)</div>
        </div>
    </div>

    <div class="rating-bar">{rating_html}</div>
    {ad_html}

    {ai_summary_html}

{sentiment_charts_html}

    {hot_stock_html}

    {lianban_height_html}

    {echelon_html}

{mainline_table_html}

{hot_sectors_html}

<h2 class="section-title">📊 大主线强度走势 <span class="help-icon" data-tip="展示核心主线板块的相对强度变化趋势。强度越高代表板块在该时间点的赚钱效应越强。">?</span></h2>
<div class="chart-container" id="mlChart" style="height:450px;"></div>

<h2 class="section-title">📡 细分板块强度追踪 <span class="help-icon" data-tip="监控更细颗粒度的行业板块强度，用于寻找主线下的分支补涨逻辑。">?</span></h2>
{sub_charts_html}

<h2 class="section-title">📈 N日涨幅排行榜 Top30 <span class="help-icon" data-tip="统计个股在5/10/20/60日内的累计涨幅，不仅展示当前热门，更能发现中期趋势走强的‘超预期’品种。">?</span></h2>
{top30_html}

{fupan_html}

    <div class="glossary">
        <h3>📖 核心名词解释 (Metric Glossary)</h3>
        <div class="glossary-grid">
            <div class="glossary-item">
                <b>○ 盘面情绪指数 (AD Mood)</b>
                <p>基于全市场涨跌家数比率计算。>50% 表示上涨家数多于下跌家数，反映市场整体赚钱效应。数值越高，市场氛围越活跃。</p>
            </div>
            <div class="glossary-item">
                <b>○ N日相对强度 (Relative Strength)</b>
                <p>衡量板块在相应周期内的表现与全市场平均水平的对比。100% 为市场基准，超过 100% 表示强于大盘，数值越大代表资金关注度越高。</p>
            </div>
            <div class="glossary-item">
                <b>○ 连板高度 (Echelon Height)</b>
                <p>反映当前市场短线投机的高度上限。最高连板数代表了市场最强资金的风险偏好，是短线情绪的风向标。</p>
            </div>
            <div class="glossary-item">
                <b>○ 涨停板天梯 (Mainline Matrix)</b>
                <p>将涨停个股按行业主线和连板高度进行矩阵排列，直观展示资金在各板块的分布深度与进攻节奏。</p>
            </div>
        </div>
    </div>
</div>


<script>
(function(){{var c=echarts.init(document.getElementById('mlChart'),'dark');
c.setOption({{title:{{show:false}},
tooltip:{{trigger:'axis'}},
legend:{{data:{json.dumps([s['name'] for s in ml_series],ensure_ascii=False)},top:20,textStyle:{{fontSize:11}}}},
grid:{{left:60,right:30,top:70,bottom:50}},
xAxis:{{type:'category',data:{json.dumps(dates_fmt)},axisLabel:{{rotate:45,fontSize:10}}}},
yAxis:{{type:'value',name:'占比(%)',axisLabel:{{formatter:'{{value}}%'}}}},
dataZoom:[{{type:'inside'}},{{type:'slider',bottom:5,height:18}}],
series:{json.dumps(ml_series,ensure_ascii=False)}}});
window.addEventListener('resize',function(){{c.resize();}});}})();


</script></body></html>'''

    with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
        f.write(html)

# ============================================================
# 主入口
# ============================================================
def main():
    print("=" * 60)
    print("  主线强度追踪系统 V3 — 概念板块版")
    print("=" * 60)

    # 0. 启动时检查并清理所有缓存文件大小
    print("\n[0/7] 检查缓存文件大小...")
    trim_cache_file(ZT_CACHE_FILE, date_col='日期')
    trim_cache_file(PRICE_CACHE, date_col='date', encoding='utf-8')
    trim_cache_file(CLS_PLATE_CACHE, date_col='date')
    trim_cache_file(SENTIMENT_CACHE, date_col='日期', encoding='utf-8')

    # 1. 更新涨停数据 & 获取连板分析情绪
    sentiment_df = None
    try:
        from lianban_analysis import fetch_zt_pool_data, analyze_lianban
        print("\n[1/7] 更新涨停池数据...")
        zt_data, dt_data = fetch_zt_pool_data(n_trading_days=120)
        
        if zt_data is not None and dt_data is not None:
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] 生成市场短线情绪历史...")
            sentiment_df = analyze_lianban(zt_data, dt_data)
            
            # 安全检查: analyze_lianban 产出的 up/down 可能来自 price_cache 回退,
            # 连续2天以上相同的 up/down 值 = 陈旧数据, 清零以触发下游 API 补全
            if sentiment_df is not None and not sentiment_df.empty and 'up' in sentiment_df.columns:
                _dk = sentiment_df['up'].astype(str) + '_' + sentiment_df['down'].astype(str)
                _dg = (_dk != _dk.shift(1)).cumsum()
                _ds = _dg.map(_dg.value_counts())
                _nonzero = (sentiment_df['up'] != 0) | (sentiment_df['down'] != 0)
                _stale = (_ds >= 2) & _nonzero
                n_stale = _stale.sum()
                if n_stale > 0:
                    print(f"  🔧 检测到 {n_stale} 条陈旧涨跌数据 (连续重复值), 清零待API补全")
                    sentiment_df.loc[_stale, ['up', 'down']] = 0
            
    except Exception as e:
        print(f"[警告] 涨停数据更新失败 ({e})")

    # 2. 加载并分类涨停数据
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] [2/7] 加载并分类涨停股票...")
    classified = load_and_classify_zt(n_days=90)
    
    # === 新增：过滤掉非交易日（规避节假日及周末） ===
    try:
        import akshare as ak
        trade_df = ak.tool_trade_date_hist_sina()
        trade_dates_set = set(trade_df['trade_date'].astype(str).apply(lambda x: x.replace('-', '')).tolist())
        
        classified = classified[classified['日期'].isin(trade_dates_set)]
        if sentiment_df is not None and not sentiment_df.empty:
            sentiment_df = sentiment_df[sentiment_df['日期'].isin(trade_dates_set)]
            
        print(f"  🧹 节假日剔除: 保留了 {len(classified['日期'].unique())} 个有效交易日")
    except Exception as e:
        print(f"  ⚠️ 获取交易日历失败，未能剔除非交易日数据: {e}")

    if classified.empty:
        print("[错误] 无已分类涨停数据 (或剔除节假日后为空)")
        return

    dates = sorted(classified['日期'].unique())
    latest_date = dates[-1]

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] [交叉校验] 更新价格以验证收盘状态...")
    price_df = update_price_cache(classified)
    
    latest_date_dt = f"{latest_date[:4]}-{latest_date[4:6]}-{latest_date[6:]}"
    if not price_df.empty and latest_date_dt not in price_df['date'].unique():
        if len(dates) > 1:
            print(f"  [⚠️回退] {latest_date} 尚未获取到Baostock收盘数据，系统整体回退至 {dates[-2]} 进行展示！")
            classified = classified[classified['日期'] != latest_date]
            if sentiment_df is not None and not sentiment_df.empty:
                sentiment_df = sentiment_df[sentiment_df['日期'] != latest_date]
            dates = sorted(classified['日期'].unique())
            latest_date = dates[-1]

    # 3. 构建盘面详情与词云
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] [3/7] 构建盘面详情与词云...")
    echelon = []
    wc_data = {}
    plates_data = None
    advance_decline = {}

    fupan_api = FuPanZhangTingYuanYin()
    f_date = f"{latest_date[:4]}-{latest_date[4:6]}-{latest_date[6:]}"
    
    cls_today = fetch_cls_plate_data(latest_date)
    f_data = fupan_api.get_data(f_date)

    if cls_today:
        echelon = build_echelon_table(cls_today)
        wc_data = generate_wordclouds(cls_today.get('plate_stock', []), CACHE_DIR)
    
    if f_data and f_data.get('reason'):
        plates_data = f_data['reason'].get('plates', [])
        f_summary = f_data['reason'].get('summary', {})
        advance_decline = {
            'up': f_summary.get('szjs', 0),  # type: ignore
            'down': f_summary.get('xdjs', 0),  # type: ignore
            'zt': f_summary.get('ztjs', 0),  # type: ignore
            'dt': f_summary.get('dtjs', 0),  # type: ignore
            'date': latest_date,
            'zt_max_height': 0 
        }
    
    # 如果 LongHu API 数据缺失或可疑, 尝试从 akshare 获取实时涨跌家数
    if advance_decline.get('up', 0) == 0 or (
        advance_decline.get('up', 0) == advance_decline.get('down', 0) == 0):
        try:
            import akshare as ak
            print("  📊 尝试从 akshare 获取实时涨跌家数...")
            spot = ak.stock_zh_a_spot_em()
            if spot is not None and not spot.empty:
                up_count = len(spot[spot['涨跌幅'] > 0])
                down_count = len(spot[spot['涨跌幅'] < 0])
                if up_count > 0:
                    advance_decline['up'] = up_count
                    advance_decline['down'] = down_count
                    print(f"  ✅ akshare 实时: 涨{up_count} 跌{down_count}")
        except Exception as e:
            print(f"  ⚠️ akshare 实时数据获取失败: {e}")

    zt_max_height = 0
    if echelon:
        for e in echelon:
            h_str = e.get('height', '0')
            if '连板' in h_str:  # type: ignore
                try:
                    h = int(h_str.replace('连板', ''))  # type: ignore
                    if h > zt_max_height: zt_max_height = h
                except: pass
            elif h_str == '首板' and zt_max_height == 0:
                zt_max_height = 1
    advance_decline['zt_max_height'] = zt_max_height

    # 从 sentiment_df 获取昨日涨停数和昨日最高板
    if sentiment_df is not None and not sentiment_df.empty and len(sentiment_df) >= 2:
        prev_row = sentiment_df.iloc[-2]
        # 昨日涨停数 (兼容 'zt' 和 '涨停数' 两种列名)
        zt_col = 'zt' if 'zt' in sentiment_df.columns else ('涨停数' if '涨停数' in sentiment_df.columns else None)
        if zt_col:
            zt_prev = prev_row.get(zt_col, 0)
            try:
                zt_prev = int(float(zt_prev)) if pd.notnull(zt_prev) else 0
            except:
                zt_prev = 0
            advance_decline['zt_prev'] = zt_prev if zt_prev > 0 else '---'
        # 昨日最高板
        if '连板高度' in sentiment_df.columns:
            lb_prev = prev_row.get('连板高度', 0)
            try:
                lb_prev = int(float(lb_prev)) if pd.notnull(lb_prev) else 0
            except:
                lb_prev = 0
            advance_decline['zt_max_height_prev'] = lb_prev if lb_prev > 0 else '---'

    # 4. 计算强度与价格
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] [4/7] 计算强度值与价格历史...")
    ml_strength = calc_daily_strength(classified, '大主线')
    for n in MAINLINE_NAMES:
        if n not in ml_strength.columns: ml_strength[n] = 0
    ml_strength = ml_strength[[c for c in MAINLINE_NAMES if c in ml_strength.columns]]

    sub_strength = calc_daily_strength(classified, '细分板块')
    all_subs = set(s for s, m in CONCEPT_TO_SECTOR.values())
    for s in all_subs:
        if s not in sub_strength.columns:
            sub_strength[s] = 0

    dc_cols = [c for c in DACHOUQI_SUBS if c in sub_strength.columns]
    dc_strength = sub_strength[dc_cols].copy() if dc_cols else pd.DataFrame(index=sub_strength.index)
    for s in DACHOUQI_SUBS:
        if s not in dc_strength.columns: dc_strength[s] = 0

    ml_ma = calc_ma(ml_strength)
    sub_ma = calc_ma(sub_strength)
    
    leaders = get_leaders(classified, '细分板块')
    
    # 计算细分板块累计涨幅 (高保真版)
    print(f"  📈 计算细分板块累计涨幅与领涨股...")
    sub_returns, return_leaders = calc_subsector_returns(classified, price_df, dates)
    
    nday_leaders = get_nday_leaders(classified, price_df, '细分板块') if not price_df.empty else {}

    ratings = {n: rate_mainline(ml_strength[n].tail(10)) for n in MAINLINE_NAMES if n in ml_strength.columns}
    sub_ratings = {}
    for col in sub_strength.columns:
        r = rate_sub(sub_strength[col])
        sub_ratings[col] = (r, '') 
        for tag, (sub, ml) in CONCEPT_TO_SECTOR.items():
            if sub == col: sub_ratings[col] = (r, ml); break

    # 5. N日涨幅排名 & 情感历史
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] [5/7] 计算N日涨幅排行与情感历史...")
    # ... (保持原样)
    top30_data = {}
    if not price_df.empty:
        industry_map = {}
        if os.path.exists(INDUSTRY_CACHE):
            idf = pd.read_csv(INDUSTRY_CACHE, dtype=str)
            for _, row in idf.iterrows():
                industry_map[row['code']] = {'name': row['name'], 'industry': row['industry']}
        
        returns_df = calc_nday_returns(price_df)
        if not returns_df.empty:
            returns_df['name'] = returns_df['code'].map(lambda c: industry_map.get(c, {}).get('name', ''))
            returns_df['industry'] = returns_df['code'].map(lambda c: industry_map.get(c, {}).get('industry', ''))
            INDUSTRY_TO_ML = {
                'B09有色金属矿采选业': '大周期', 'C32有色金属冶炼和压延加工业': '大周期',
                'C26化学原料和化学制品制造业': '大周期', 'B07石油和天然气开采业': '大周期',
                'I65软件和信息技术服务业': '人工智能', 'I64互联网和相关服务': '人工智能',
                'C39计算机、通信和其他电子设备制造业': '半导体',
                'C37铁路、船舶、航空航天和其他运输设备制造业': '商业航天',
                'C38电气机械和器材制造业': '电网', 'D44电力、热力生产和供应业': '电网',
                'C14食品制造业': '大消费', 'C15酒、饮料和精制茶制造业': '大消费',
            }
            returns_df['mainline'] = returns_df['industry'].map(lambda i: INDUSTRY_TO_ML.get(i, ''))
            def get_subsector(ind):
                for k, v in INDUSTRY_TO_SECTOR.items():
                    if k == ind: return v[0]
                return ''
            returns_df['sub_sector'] = returns_df['industry'].map(get_subsector)
            
            for period in [5, 10, 20, 60]:
                col = f'{period}日涨幅'
                if col in returns_df.columns:
                    sorted_df = returns_df.dropna(subset=[col]).sort_values(col, ascending=False)
                    top30_data[f'{period}日'] = [{'code': r['code'], 'name': r['name'],'pct': r[col], 'industry': r['industry'],'mainline': r['mainline'], 'sub_sector': r['sub_sector']} for _, r in sorted_df.head(100).iterrows()]

    # === 涨跌数据补全 (V4: 数据完整性校验 + 全量补全) ===
    sent_cache_df = pd.DataFrame()
    if os.path.exists(SENTIMENT_CACHE):
        try: sent_cache_df = pd.read_csv(SENTIMENT_CACHE, dtype={'日期': str})
        except: pass
    
    # 检测并清理缓存中的"重复值污染" (连续多天数据完全相同 = 脏数据)
    # 降低阈值: 连续2天涨跌家数完全相同在A股中极为罕见，视为污染
    if not sent_cache_df.empty and 'up' in sent_cache_df.columns and 'down' in sent_cache_df.columns:
        sent_cache_df['up'] = pd.to_numeric(sent_cache_df['up'], errors='coerce').fillna(0)
        sent_cache_df['down'] = pd.to_numeric(sent_cache_df['down'], errors='coerce').fillna(0)
        sent_cache_df['_dup_key'] = sent_cache_df['up'].astype(str) + '_' + sent_cache_df['down'].astype(str)
        sent_cache_df['_dup_group'] = (sent_cache_df['_dup_key'] != sent_cache_df['_dup_key'].shift(1)).cumsum()
        dup_sizes = sent_cache_df.groupby('_dup_group').transform('size')
        dirty_mask = dup_sizes >= 2  # 连续2天以上完全相同 = 脏数据
        # 排除 up=0 且 down=0 的行 (本身就是缺失值，不算重复)
        zero_mask = (sent_cache_df['up'] == 0) & (sent_cache_df['down'] == 0)
        dirty_mask = dirty_mask & ~zero_mask
        n_dirty = dirty_mask.sum()
        if n_dirty > 0:
            print(f"  🧹 检测到 {n_dirty} 条重复值污染的缓存数据, 清零待API补全...")
            # pyrefly: ignore [unsupported-operation]
            sent_cache_df.loc[dirty_mask, ['up', 'down']] = 0
            if 'zt' in sent_cache_df.columns:
                # pyrefly: ignore [unsupported-operation]
                sent_cache_df.loc[dirty_mask, ['zt', 'dt']] = 0
        sent_cache_df.drop(columns=['_dup_key', '_dup_group'], inplace=True)

    if sentiment_df is None or sentiment_df.empty:
        sentiment_df = sent_cache_df.copy()
    elif not sent_cache_df.empty:
        # 合并策略: sentiment_df (来自 analyze_lianban) 为主, 
        # 仅当主数据 up=0 且 down=0 时, 才用缓存中的有效值替代
        needed = [c for c in ['日期', 'up', 'down', 'zt', 'dt'] if c in sent_cache_df.columns]
        # 过滤掉缓存中本身就是0的行, 只保留有有效数据的缓存行
        valid_cache = sent_cache_df[needed].copy()
        valid_cache['up'] = pd.to_numeric(valid_cache['up'], errors='coerce').fillna(0)
        valid_cache['down'] = pd.to_numeric(valid_cache['down'], errors='coerce').fillna(0)
        valid_cache = valid_cache[(valid_cache['up'] > 0) | (valid_cache['down'] > 0)]
        
        if not valid_cache.empty:
            sentiment_df = pd.merge(sentiment_df, valid_cache, on='日期', how='left', suffixes=('', '_cache'))
            for col in ['up', 'down', 'zt', 'dt']:
                c_cache = f'{col}_cache'
                if c_cache in sentiment_df.columns:
                    sentiment_df[col] = pd.to_numeric(sentiment_df[col], errors='coerce').fillna(0)
                    sentiment_df[c_cache] = pd.to_numeric(sentiment_df[c_cache], errors='coerce').fillna(0)
                    # 仅当主数据为 0 时才用缓存填充
                    fill_mask = sentiment_df[col] == 0
                    sentiment_df.loc[fill_mask, col] = sentiment_df.loc[fill_mask, c_cache]
                    sentiment_df.drop(columns=[c_cache], inplace=True)
    
    # 补全缺失数据 (up=0 且 down=0 的天 + 最新日期的 intraday 数据)
    if sentiment_df is not None and not sentiment_df.empty:
        # 如果最新的一天是在 advance_decline 中获取到的，优先填入
        latest_row_idx = sentiment_df.index[-1]
        if str(sentiment_df.at[latest_row_idx, '日期']) == str(latest_date):
            # pyrefly: ignore [bad-argument-type]
            if float(sentiment_df.at[latest_row_idx, 'up'] or 0) == 0 and advance_decline.get('up', 0) > 0:
                sentiment_df.at[latest_row_idx, 'up'] = advance_decline['up']
                sentiment_df.at[latest_row_idx, 'down'] = advance_decline['down']
                sentiment_df.at[latest_row_idx, 'zt'] = advance_decline.get('zt', 0)
                sentiment_df.at[latest_row_idx, 'dt'] = advance_decline.get('dt', 0)

        # 检查是否还有缺失 (up=0 且 down=0 的天)
        sentiment_df['up'] = pd.to_numeric(sentiment_df['up'], errors='coerce').fillna(0)
        sentiment_df['down'] = pd.to_numeric(sentiment_df['down'], errors='coerce').fillna(0)
        missing_mask = (sentiment_df['up'] == 0) & (sentiment_df['down'] == 0)
        missing_dates = sentiment_df[missing_mask]['日期'].tolist()
        
        if missing_dates:
            # 补全所有缺失的天 (不再限制30天, 确保数据完整)
            print(f"  📥 补全 {len(missing_dates)} 天的涨跌数据 (LongHu API 逐日抓取)...")
            filled_count = 0
            for d_str in missing_dates:
                d_str_val = str(d_str)
                d_api = f"{d_str_val[:4]}-{d_str_val[4:6]}-{d_str_val[6:]}" if '-' not in d_str_val else d_str_val
                res = fetch_longhu_sentiment(d_api)
                if res and res['up'] > 0:  # 确保API返回了有效数据
                    idx = sentiment_df[sentiment_df['日期'] == d_str].index[0]
                    sentiment_df.at[idx, 'up'] = res['up']
                    sentiment_df.at[idx, 'down'] = res['down']
                    if 'zt' in sentiment_df.columns:
                        sentiment_df.at[idx, 'zt'] = res['zt']
                        sentiment_df.at[idx, 'dt'] = res['dt']
                    filled_count += 1
                time.sleep(0.15)  # API限速
            print(f"  ✅ 成功补全 {filled_count}/{len(missing_dates)} 天")
        else:
            print(f"  ✅ 涨跌数据完整, 无需补全 (共 {len(sentiment_df)} 天)")
        
        # === 最终数据完整性校验: 再次检查是否有连续重复值 ===
        _check_up = sentiment_df['up'].astype(str) + '_' + sentiment_df['down'].astype(str)
        _dup_groups = (_check_up != _check_up.shift(1)).cumsum()
        _dup_lens = _dup_groups.map(_dup_groups.value_counts())
        _still_dirty = (_dup_lens >= 2) & (sentiment_df['up'] > 0)
        if _still_dirty.sum() > 0:
            print(f"  ⚠️ 数据校验: 仍有 {_still_dirty.sum()} 条可疑重复数据 (可能是API返回了陈旧值)")

        # 统一更新缓存 (一次性写入, 用最新的完整数据覆盖)
        try:
            save_cols = ['日期', 'up', 'down']
            if 'zt' in sentiment_df.columns: save_cols += ['zt', 'dt']
            if 'flat' in sentiment_df.columns: save_cols.append('flat')
            if 'date_str' in sentiment_df.columns: save_cols.append('date_str')
            save_df = sentiment_df[save_cols].copy()
            if not sent_cache_df.empty:
                combined = pd.concat([sent_cache_df, save_df], ignore_index=True)
                combined.drop_duplicates(subset=['日期'], keep='last', inplace=True)
                combined = combined.sort_values('日期').reset_index(drop=True)
            else:
                combined = save_df.sort_values('日期').reset_index(drop=True)
            combined.to_csv(SENTIMENT_CACHE, index=False)
            trim_cache_file(SENTIMENT_CACHE, date_col='日期', encoding='utf-8')
        except Exception as e:
            print(f"  ⚠️ 缓存更新失败: {e}")
    
    if sentiment_df is not None and not sentiment_df.empty:
        if 'up' in sentiment_df.columns and 'down' in sentiment_df.columns:
            def _calc_mood(r):
                up = float(r['up']) if pd.notnull(r['up']) else 0
                down = float(r['down']) if pd.notnull(r['down']) else 0
                return round((up / max(up + down, 1)) * 100, 1)
            sentiment_df['ad_mood'] = sentiment_df.apply(_calc_mood, axis=1)
        elif '涨跌比' in sentiment_df.columns:
            sentiment_df['ad_mood'] = sentiment_df['涨跌比'].fillna(0.5) * 100
        else:
            sentiment_df['ad_mood'] = 50.0

    # 6. 生成HTML
    print("\n[6/6] 生成可视化...")
    generate_html(
        ml_strength=ml_strength, sub_strength=sub_strength,
        ml_ma=ml_ma, sub_ma=sub_returns, 
        ml_thresh=calc_threshold(len(dates)), 
        sub_thresh=calc_threshold(len(dates), 10.0, 20.0),
        leaders=leaders, dates=dates, dc_strength=dc_strength,
        ratings=ratings, sub_ratings=sub_ratings,
        echelon=echelon, top30_data=top30_data, advance_decline=advance_decline,
        nday_leaders=nday_leaders, wc_data=wc_data, sentiment_df=sentiment_df,
        plates=plates_data, classified_df=classified, return_leaders=return_leaders
    )

    # 7. 自动化生成选股池与交易预案
    focus_pool_path = os.path.join(os.path.dirname(OUTPUT_HTML), "focus_pool.csv")
    generate_focus_pool(ml_strength, echelon, top30_data, sentiment_df, focus_pool_path)

    print(f"\n{'='*60}")
    print(f"  ✅ V3 同步版已完成!")
    print(f"  → 打开 {OUTPUT_HTML} 查看")
    print(f"{'='*60}")

    # 8. 自动发送邮件
    if EMAIL_ENABLE:
        try:
            if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECEIVERS:
                print("  ⚠️ 邮件尚未配置 (需设置环境变量 EMAIL_SENDER/EMAIL_PASSWORD/EMAIL_RECEIVERS)，跳过发送。")
            else:
                print(f"\n  📧 正在自动发送报告邮件至 {', '.join(EMAIL_RECEIVERS)} ...")
                msg = MIMEMultipart()
                msg['From'] = EMAIL_SENDER
                msg['To'] = ", ".join(EMAIL_RECEIVERS)
                report_date = datetime.now().strftime('%Y-%m-%d')
                msg['Subject'] = f"【主线强度追踪报告】{report_date}"

                body = f"您好，附件为 {report_date} 的主线强度追踪报告，请查收。"
                msg.attach(MIMEText(body, 'plain', 'utf-8'))

                if os.path.exists(OUTPUT_HTML):
                    with open(OUTPUT_HTML, 'rb') as f:
                        part = MIMEApplication(f.read())
                        part.add_header('Content-Disposition', 'attachment', filename=os.path.basename(OUTPUT_HTML))
                        msg.attach(part)
                    
                    # GitHub Actions: 强制 IPv4 解析 (避免 IPv6 [Errno 101] Network is unreachable)
                    _orig_getaddrinfo = socket.getaddrinfo
                    if IS_GITHUB_ACTIONS:
                        socket.getaddrinfo = lambda h, p, f=0, t=0, pr=0, fl=0: _orig_getaddrinfo(h, p, socket.AF_INET, t, pr, fl)  # type: ignore
                    try:
                        import ssl
                        _sent = False
                        # 策略1: SSL(465) + 自定义上下文 (解决 UNEXPECTED_EOF_WHILE_READING)
                        try:
                            ctx = ssl.create_default_context()
                            ctx.set_ciphers('DEFAULT@SECLEVEL=1')  # 降低安全级别以兼容QQ邮箱
                            server = smtplib.SMTP_SSL(EMAIL_SMTP_SERVER, EMAIL_SMTP_PORT, timeout=30, context=ctx)
                            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
                            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVERS, msg.as_string())
                            server.quit()
                            _sent = True
                        except Exception as e1:
                            print(f"    策略1 SSL(465)+自定义上下文 失败: {e1}")
                        
                        # 策略2: STARTTLS(587)
                        if not _sent:
                            try:
                                print(f"    尝试策略2 STARTTLS(587)...")
                                server = smtplib.SMTP(EMAIL_SMTP_SERVER, 587, timeout=30)
                                server.ehlo()
                                ctx2 = ssl.create_default_context()
                                ctx2.set_ciphers('DEFAULT@SECLEVEL=1')
                                server.starttls(context=ctx2)
                                server.ehlo()
                                server.login(EMAIL_SENDER, EMAIL_PASSWORD)
                                server.sendmail(EMAIL_SENDER, EMAIL_RECEIVERS, msg.as_string())
                                server.quit()
                                _sent = True
                            except Exception as e2:
                                print(f"    策略2 STARTTLS(587) 失败: {e2}")

                        # 策略3: SSL(465) + 跳过证书验证 (最后手段)
                        if not _sent:
                            try:
                                print(f"    尝试策略3 SSL(465)+跳过验证...")
                                ctx3 = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                                ctx3.check_hostname = False
                                ctx3.verify_mode = ssl.CERT_NONE
                                ctx3.set_ciphers('DEFAULT@SECLEVEL=0')
                                server = smtplib.SMTP_SSL(EMAIL_SMTP_SERVER, EMAIL_SMTP_PORT, timeout=30, context=ctx3)
                                server.login(EMAIL_SENDER, EMAIL_PASSWORD)
                                server.sendmail(EMAIL_SENDER, EMAIL_RECEIVERS, msg.as_string())
                                server.quit()
                                _sent = True
                            except Exception as e3:
                                print(f"    策略3 SSL+跳过验证 失败: {e3}")
                                raise e3
                    finally:
                        socket.getaddrinfo = _orig_getaddrinfo
                    if _sent:
                        print("  ✅ 邮件发送成功！")
                else:
                    print(f"  [错误] 附件文件不存在: {OUTPUT_HTML}，未发送邮件。")
        except Exception as e:
            print(f"  [错误] 邮件发送失败: {e}")


if __name__ == '__main__':
    main()
