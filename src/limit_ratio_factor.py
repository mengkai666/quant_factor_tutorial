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
import os
import requests
import urllib3
from time_utils import filter_completed_rows, get_latest_date

# 禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# 缓存文件路径 (集中在 paths.py, 单一真源)
from paths import (
    SECURITY_MASTER_CACHE,
    ZT_CACHE_FILE,
    PRICE_CACHE as PRICE_CACHE_FILE,
)
from report_logic import normalize_stock_code


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
            
            def _count(key):
                value = nums.get(key)
                if value in (None, ""):
                    return None
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None

            up = _count("SZJS")
            down = _count("XDJS")
            if up is None or down is None:
                return None
            total = up + down
            return {
                "date": day.replace('-', ''),
                "up": up,
                "down": down,
                "flat": None,
                "zt": _count("ZT") or 0,
                "dt": _count("DT") or 0,
                "ad_ratio": up / total if total > 0 else None,
                "ad_available": total > 0,
                "ad_status": "api" if total > 0 else "empty",
                "source": "longhu_api",
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
            df = filter_completed_rows(df, '日期')
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
            # 性能优化：避免 pivot (在大数据集上极慢), 改用 groupby + shift。
            # 价格缓存可能同时存在 raw/qfq/legacy 三种历史口径；这里先按
            # code/date 折叠成一条“可计算价格”，并明确记录实际口径，避免
            # legacy_mixed 被误报成 raw/qfq 权威数据。
            df = pd.read_csv(PRICE_CACHE_FILE, dtype={'code': str, 'date': str})
            df = filter_completed_rows(df, 'date')
            if df.empty or 'code' not in df.columns or 'date' not in df.columns:
                self._ad_cache = {}
                return {}

            df['code'] = df['code'].map(normalize_stock_code)
            df['date_clean'] = (
                df['date'].astype(str).str.strip().str.replace('-', '', regex=False).str[:8]
            )
            df = df[
                df['code'].ne('')
                & df['date_clean'].str.fullmatch(r'\d{8}', na=False)
            ].copy()
            if df.empty:
                self._ad_cache = {}
                return {}

            universe_codes = set()
            market_scope_source = 'price_cache'
            scope_inferred = True
            same_cache_dir = (
                os.path.dirname(os.path.abspath(SECURITY_MASTER_CACHE))
                == os.path.dirname(os.path.abspath(PRICE_CACHE_FILE))
            )
            if same_cache_dir and os.path.exists(SECURITY_MASTER_CACHE):
                try:
                    master = pd.read_csv(SECURITY_MASTER_CACHE, dtype=str)
                    if 'code' in master.columns:
                        universe_codes = {
                            normalize_stock_code(code)
                            for code in master['code'].tolist()
                            if normalize_stock_code(code)
                        }
                        if universe_codes:
                            market_scope_source = 'security_master'
                            scope_inferred = False
                except Exception as exc:
                    print(f"  ⚠️ 证券主数据加载失败(A/D继续使用缓存范围): {exc}")
            if universe_codes:
                df = df[df['code'].isin(universe_codes)].copy()
            else:
                universe_codes = set(df['code'].unique())

            if df.empty or not universe_codes:
                self._ad_cache = {}
                return {}

            for column in ('close_raw', 'close_qfq', 'close_legacy', 'close'):
                if column not in df.columns:
                    df[column] = pd.NA
                df[column] = pd.to_numeric(df[column], errors='coerce')

            def choose_price(row):
                for column, basis in (
                    ('close_raw', 'raw'),
                    ('close_qfq', 'qfq_fallback'),
                    ('close_legacy', 'legacy_mixed'),
                    ('close', 'legacy_mixed'),
                ):
                    value = row[column]
                    if pd.notna(value) and float(value) > 0:
                        return float(value), basis
                return None, 'unavailable'

            selected = df.apply(choose_price, axis=1, result_type='expand')
            selected.columns = ['ad_close', 'ad_basis']
            df[['ad_close', 'ad_basis']] = selected
            df = df.dropna(subset=['ad_close']).copy()
            if df.empty:
                self._ad_cache = {}
                return {}

            # 同一股票同一日期可能同时有 raw/qfq/legacy 记录。优先 raw，
            # 其次 qfq，最后 legacy；这样补齐不会被旧行覆盖。
            basis_rank = {'raw': 0, 'qfq_fallback': 1, 'legacy_mixed': 2}
            df['_basis_rank'] = df['ad_basis'].map(basis_rank).fillna(9)
            df = (
                df.sort_values(['code', 'date_clean', '_basis_rank'])
                .drop_duplicates(['code', 'date_clean'], keep='first')
                .sort_values(['code', 'date_clean'])
            )

            # 在每只股票内计算涨跌幅。只保留当前和前一交易日都有可信价格的样本。
            df['prev_close'] = df.groupby('code')['ad_close'].shift(1)
            df['prev_basis'] = df.groupby('code')['ad_basis'].shift(1)
            df['chg_pct'] = (df['ad_close'] / df['prev_close'] - 1) * 100
            df = df.dropna(subset=['ad_close', 'prev_close', 'chg_pct'])
            
            # 按日统计涨跌家数
            daily_up = df[df['chg_pct'] > 0.1].groupby('date_clean').size()
            daily_dn = df[df['chg_pct'] < -0.1].groupby('date_clean').size()
            
            all_dates = df['date_clean'].unique()
            result = {}
            for d in all_dates:
                up = int(daily_up.get(d, 0))
                dn = int(daily_dn.get(d, 0))
                day_rows = df[df['date_clean'] == d]
                market_covered = int(day_rows['code'].nunique())
                flat = max(0, market_covered - up - dn)
                market_total = len(universe_codes) if not scope_inferred else None
                bases = set(day_rows['ad_basis'].dropna().astype(str)) | set(
                    day_rows['prev_basis'].dropna().astype(str)
                )
                if 'legacy_mixed' in bases:
                    basis = 'legacy_mixed'
                elif 'qfq_fallback' in bases:
                    basis = 'qfq_fallback'
                else:
                    basis = 'raw'
                result[d] = {
                    'date': d,
                    'up': up,
                    'down': dn,
                    'flat': flat,
                    'ad_ratio': up / (up + dn) if (up + dn) > 0 else 0.5
                    , 'market_covered': market_covered
                    , 'raw_market_covered': market_covered
                    , 'market_total': market_total
                    , 'coverage_pct': round(market_covered / market_total * 100, 2) if market_total else None
                    , 'market_scope_source': market_scope_source
                    , 'scope_inferred': scope_inferred
                    , 'price_basis': basis
                    , 'source': 'price_cache'
                    , 'source_timestamp': str(d)
                    , 'legacy_mixed': basis == 'legacy_mixed'
                    , 'used_fallback': basis != 'raw'
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
            a_data = ad.get(d, {
                'up': None, 'down': None, 'flat': None, 'ad_ratio': None,
                'ad_available': False, 'ad_status': 'missing',
                'source': 'unavailable',
            })
            
            # 使用 API 覆盖最新一天的数据 (如果可用)
            today_str = get_latest_date().strftime("%Y%m%d")
            if d == today_str:
                api_data = self._fetch_longhu_sentiment(get_latest_date().strftime("%Y-%m-%d"))
                if api_data:
                    a_data = {
                        'up': api_data['up'], 'down': api_data['down'],
                        'flat': api_data.get('flat'), 'ad_ratio': api_data['ad_ratio'],
                        'ad_available': api_data.get('ad_available', False),
                        'ad_status': api_data.get('ad_status', 'api'),
                        'source': api_data.get('source', 'longhu_api'),
                    }
                    z_data = {'limit_up': api_data['zt'], 'limit_down': api_data['dt']}
            
            # 纯粹基于 A/D 比例 (Breadth only)
            raw_score = a_data.get('ad_ratio')
            
            data.append({
                'date': d,
                'limit_up': z_data['limit_up'],
                'limit_down': z_data['limit_down'],
                'market_up': a_data.get('up'),
                'market_down': a_data.get('down'),
                'market_flat': a_data.get('flat'),
                'ad_ratio': a_data.get('ad_ratio'),
                'ad_available': bool(a_data.get('ad_available', a_data.get('ad_ratio') is not None)),
                'ad_status': a_data.get('ad_status', 'available' if a_data.get('ad_ratio') is not None else 'missing'),
                'ad_source': a_data.get('source', 'unknown'),
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
        score_available = pd.notna(score)
        ad_available = bool(row.get('ad_available', False))
        if not score_available:
            score = 0.5
        
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
            'score': float(score) if score_available else 0.5,
            'level': lvl,
            'sentiment': sentiment,
            'interpretation': interpretation,
            'ad_ratio': float(row['ad_ratio']) if exact_match and pd.notna(row['ad_ratio']) else None,
            'market_up': int(row['market_up']) if exact_match and pd.notna(row['market_up']) else None,
            'market_down': int(row['market_down']) if exact_match and pd.notna(row['market_down']) else None,
            'market_flat': int(row['market_flat']) if exact_match and pd.notna(row.get('market_flat')) else None,
            'date': row['date'],
            'status': 'OK' if exact_match and ad_available else ('fallback' if not exact_match else 'missing'),
            'ad_available': exact_match and ad_available,
            'ad_status': row.get('ad_status', 'missing'),
            'ad_source': row.get('ad_source', 'unknown')
        }

    def _fallback_result(self, reason):
        return {
            'score': 0.5,
            'level': 3,
            'sentiment': f'中性 ({reason})',
            'interpretation': '暂无有效数据，使用中性参考值',
            'status': '降级',
            'ad_available': False,
            'ad_status': 'unavailable',
            'market_up': None,
            'market_down': None,
            'market_flat': None,
            'ad_ratio': None
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
    
    print("\n2. 细分指标:")
    print(f"   A/D 家数比: {res.get('ad_ratio', 0):.2%} (涨{res.get('market_up', 0)}/跌{res.get('market_down', 0)})")
    
    # 历史趋势截取
    df = factor._get_composite_data()
    if not df.empty:
        print("\n3. 最近5日情绪趋势:")
        for _, r in df.tail(5).iterrows():
            f_res = factor.calculate_factor(r['date'])
            print(f"   {r['date']} | 评分: {r['score_ema']:.3f} | {f_res['sentiment']}")
            
    print("\n" + "=" * 60)


if __name__ == '__main__':
    demo()
