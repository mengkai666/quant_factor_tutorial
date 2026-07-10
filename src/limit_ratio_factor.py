# pyright: reportMissingTypeStubs=false
# pyright: reportUnnecessaryCast=false
# pyright: reportUnnecessaryTypeIgnoreComment=false
"""
市场综合情绪因子 (Market Sentiment Factor)
基于全市场 涨跌家数比 (A/D Ratio) 来衡量市场整体情绪

数据来源: 
  - 涨停历史缓存.csv (涨跌停家数)
  - price_history_cache.csv (全市场涨跌家数)
"""

import pandas as pd
import numpy as np
import os
import requests
import json
import urllib3
from datetime import datetime, timedelta
from time_utils import get_latest_date

# 禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# 缓存文件路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# 缓存统一在仓库根的 data/ 目录 (src/ 的上一级)
DATA_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), 'data')
ZT_CACHE_FILE = os.path.join(DATA_DIR, '涨停历史缓存.csv')
PRICE_CACHE_FILE = os.path.join(DATA_DIR, 'price_history_cache.csv')


class MarketSentimentFactor:
    """
    市场综合情绪因子
    
    量化维度：
    1. 涨跌家数比 (A/D Ratio, 市场普涨/普跌情况) — 核心指标
    
    情绪层级 (5级):
    - [0.0, 0.20): 极弱/冰点 ❄️ (跌多涨少，恐慌寻底)
    - [0.20, 0.45): 弱势/低迷 ☁️ (多头退守，震荡探底)
    - [0.45, 0.55): 中性/平衡 ⚖️ (多空对峙，方向模糊)
    - [0.55, 0.80): 强势/活跃 🌤️ (涨多跌少，赚钱回暖)
    - [0.80, 1.0]: 极强/高潮 🔥 (普涨井喷，注意过热)
    """
    
    def __init__(self):
        self._zt_cache = None
        self._ad_cache = None
        self._composite_cache = None
        self.api_url = "https://apphwshhq.longhuvip.com/w1/api/index.php"
        self.headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; PFEM10 Build/PQ3A.190605.003)",
        }
    
    def _fetch_longhu_sentiment(self, day=None):
        """从龙虎榜API获取当日涨跌家数与涨跌停数据"""
        if not day:
            day = get_latest_date().strftime("%Y-%m-%d")
        
        params = {
            "a": "GetPlateInfo_w38",
            "st": "100",
            "c": "DailyLimitResumption",
            "PhoneOSNew": "1",
            "DeviceID": "20adcd18-9e93-3bb7-b4d5-c9fd5fa30b3f",
            "VerSion": "5.23.0.4",
            "Index": "0",
            "apiv": "w44",
            "Day": day
        }
        payload = "&".join([f"{k}={v}" for k, v in params.items()])
        try:
            response = requests.post(self.api_url, data=payload, headers=self.headers, verify=False, timeout=10)
            res_json = response.json()
            nums = res_json.get("nums", {})
            if not nums:
                return None
            
            return {
                "date": day.replace('-', ''),
                "up": int(nums.get("SZJS", 0)),
                "down": int(nums.get("XDJS", 0)),
                "zt": int(nums.get("ZT", 0)),
                "dt": int(nums.get("DT", 0)),
                "ad_ratio": float(nums.get("SZJS", 0)) / (int(nums.get("SZJS", 0)) + int(nums.get("XDJS", 0))) if (int(nums.get("SZJS", 0)) + int(nums.get("XDJS", 0))) > 0 else 0.5
            }
        except Exception as e:
            print(f"  ⚠️ LongHu API 请求失败: {e}")
            return None
    
    def _load_zt_cache(self):
        """加载涨跌停计数缓存"""
        if self._zt_cache is not None:
            return self._zt_cache
            
        if not os.path.exists(ZT_CACHE_FILE):
            self._zt_cache = {}
            return {}
            
        try:
            df = pd.read_csv(ZT_CACHE_FILE, encoding='utf-8-sig', dtype={'日期': str})
            result = {}
            for date_str, gdf in df.groupby('日期'):
                zt_count = len(gdf[gdf['类型'] == 'ZT'])
                dt_count = len(gdf[gdf['类型'] == 'DT'])
                result[date_str] = {'limit_up': zt_count, 'limit_down': dt_count}
            self._zt_cache = result
            return result
        except Exception as e:
            print(f"  ⚠️ 涨停缓存加载失败: {e}")
            self._zt_cache = {}
            return {}

    def _load_ad_cache(self):
        """从价格缓存计算A/D家数比 (带内部内存缓存)"""
        if self._ad_cache is not None:
            return self._ad_cache
            
        if not os.path.exists(PRICE_CACHE_FILE):
            self._ad_cache = {}
            return {}
            
        try:
            # 性能优化：避免 pivot (在大数据集上极慢), 改用 groupby + shift
            df = pd.read_csv(PRICE_CACHE_FILE, dtype={'code': str, 'date': str})
            df['date_clean'] = df['date'].str.replace('-', '')
            df = df.sort_values(['code', 'date_clean'])
            
            # 在每只股票内计算涨跌幅
            df['prev_close'] = df.groupby('code')['close'].shift(1)
            df['chg_pct'] = (df['close'] / df['prev_close'] - 1) * 100
            df = df.dropna(subset=['chg_pct'])
            
            # 按日统计涨跌家数
            daily_up = df[df['chg_pct'] > 0.1].groupby('date_clean').size()
            daily_dn = df[df['chg_pct'] < -0.1].groupby('date_clean').size()
            
            all_dates = df['date_clean'].unique()
            result = {}
            for d in all_dates:
                up = int(daily_up.get(d, 0))
                dn = int(daily_dn.get(d, 0))
                total = up + dn
                result[d] = {
                    'date': d,
                    'up': up,
                    'down': dn,
                    'ad_ratio': up / total if total > 0 else 0.5
                }
            
            self._ad_cache = result
            return result
        except Exception as e:
            print(f"  ⚠️ 价格缓存(AD)加载失败: {e}")
            self._ad_cache = {}
            return {}

    def _get_composite_data(self):
        """合并涨跌停与AD比例，并进行EMA平滑处理"""
        if self._composite_cache is not None:
            return self._composite_cache
            
        zt = self._load_zt_cache()
        ad = self._load_ad_cache()
        
        all_dates = sorted(set(zt.keys()) | set(ad.keys()))
        data = []
        for d in all_dates:
            z_data = zt.get(d, {'limit_up': 0, 'limit_down': 0})
            a_data = ad.get(d, {'up': 0, 'down': 0, 'ad_ratio': 0.5})
            
            # 使用 API 覆盖最新一天的数据 (如果可用)
            today_str = get_latest_date().strftime("%Y%m%d")
            if d == today_str:
                api_data = self._fetch_longhu_sentiment(get_latest_date().strftime("%Y-%m-%d"))
                if api_data:
                    a_data = {'up': api_data['up'], 'down': api_data['down'], 'ad_ratio': api_data['ad_ratio']}
                    z_data = {'limit_up': api_data['zt'], 'limit_down': api_data['dt']}
            
            # 纯粹基于 A/D 比例 (Breadth only)
            raw_score = a_data['ad_ratio']
            
            data.append({
                'date': d,
                'limit_up': z_data['limit_up'],
                'limit_down': z_data['limit_down'],
                'market_up': a_data['up'],
                'market_down': a_data['down'],
                'ad_ratio': a_data['ad_ratio'],
                'raw_score': raw_score
            })
            
        if not data:
            self._composite_cache = pd.DataFrame()
            return self._composite_cache
            
        df = pd.DataFrame(data)
        # 用3日EMA进行平滑，更快反映日内转势
        df['score_ema'] = df['raw_score'].ewm(span=3, adjust=False).mean()
        
        self._composite_cache = df
        return df

    def calculate_factor(self, date=None):
        """
        计算市场综合情绪因子
        返回: 5级情绪分类 + 综合评分
        """
        df = self._get_composite_data()
        if df.empty:
            return self._fallback_result('无数据可用')
            
        # 确定目标行
        exact_match = False
        if date is None:
            row = df.iloc[-1]
            exact_match = True
        else:
            d_str = date.strftime('%Y%m%d') if hasattr(date, 'strftime') else str(date).replace('-', '')
            exact = df[df['date'] == d_str]
            if not exact.empty:
                row = exact.iloc[-1]
                exact_match = True
            else:
                # 日期不在价格缓存中: 用最近日期的 score 做情绪评级,
                # 但 market_up/market_down 返回0 以触发下游API补全
                matches = df[df['date'] <= d_str]
                if matches.empty:
                    row = df.iloc[0]
                else:
                    row = matches.iloc[-1]
        
        score = row['score_ema']
        
        # 5级情绪层级分类 (基于AD比例优化的阈值)
        if score >= 0.8:
            lvl = 5
            sentiment = "极强/高潮 🔥🔥🔥"
            interpretation = "市场普涨喷发，关注短线是否超买过热"
        elif score >= 0.55:
            lvl = 4
            sentiment = "强势/活跃 🌤️"
            interpretation = "多头占据主动，赚钱效应良好"
        elif score >= 0.45:
            lvl = 3
            sentiment = "中性/平衡 ⚖️"
            interpretation = "涨跌互现，多空力量暂时处于动态平衡"
        elif score >= 0.20:
            lvl = 2
            sentiment = "弱势/低迷 ☁️"
            interpretation = "跌多涨少，观望为主，控制仓位"
        else:
            lvl = 1
            sentiment = "极弱/冰点 ❄️"
            interpretation = "普跌杀跌，关注市场何时出现冰点反转"
        
        # 关键修复: 仅当精确匹配到日期时才返回真实的 market_up/market_down
        # 否则返回0, 让调用方知道需要从API补全
        return {
            'score': float(score),
            'level': lvl,
            'sentiment': sentiment,
            'interpretation': interpretation,
            'ad_ratio': float(row['ad_ratio']) if exact_match else 0.5,
            'market_up': int(row['market_up']) if exact_match else 0,
            'market_down': int(row['market_down']) if exact_match else 0,
            'date': row['date'],
            'status': 'OK' if exact_match else 'fallback'
        }

    def _fallback_result(self, reason):
        return {
            'score': 0.5,
            'level': 3,
            'sentiment': f'中性 ({reason})',
            'interpretation': '暂无有效数据，使用中性参考值',
            'status': '降级'
        }

    def get_factor_name(self):
        return "市场综合情绪因子"

    def get_factor_description(self):
        return "基于市场涨跌家数比(A/D Ratio)的5级量化情绪因子 (含3日EMA平滑)"


def demo():
    print("=" * 60)
    print("市场综合情绪因子演示 (5层级优化版)")
    print("=" * 60)
    
    factor = MarketSentimentFactor()
    res = factor.calculate_factor()
    
    print(f"\n1. 概览 (日期: {res.get('date', 'N/A')}):")
    print(f"   综合评分: {res['score']:.3f} | 情绪层级: {res['level']}层")
    print(f"   当前情绪: {res['sentiment']}")
    print(f"   综合解读: {res['interpretation']}")
    
    print(f"\n2. 细分指标:")
    print(f"   A/D 家数比: {res.get('ad_ratio', 0):.2%} (涨{res.get('market_up', 0)}/跌{res.get('market_down', 0)})")
    
    # 历史趋势截取
    df = factor._get_composite_data()
    if not df.empty:
        print(f"\n3. 最近5日情绪趋势:")
        for _, r in df.tail(5).iterrows():
            f_res = factor.calculate_factor(r['date'])
            print(f"   {r['date']} | 评分: {r['score_ema']:.3f} | {f_res['sentiment']}")
            
    print("\n" + "=" * 60)


if __name__ == '__main__':
    demo()
