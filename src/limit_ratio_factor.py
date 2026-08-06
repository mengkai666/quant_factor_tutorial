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
from paths import ZT_CACHE_FILE, PRICE_CACHE as PRICE_CACHE_FILE


# up+down 低于该值时，A/D 只能作为低置信观察，不能覆盖完整本地行情。
# 该阈值与主流程、审计脚本保持同义，覆盖沪深北全 A 股。
MIN_MARKET_BREADTH = 4000


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
        """从 canonical 价格缓存计算 A/D 家数比。

        A/D 必须使用未复权收盘价 ``close_raw``，并排除停牌行；``close``
        仅作为旧缓存迁移期的兼容字段，不能覆盖新契约。结果额外保留
        eligible/flat，供报表判断覆盖宽度和降级状态。
        """
        if self._ad_cache is not None:
            return self._ad_cache

        if not os.path.exists(PRICE_CACHE_FILE):
            self._ad_cache = {}
            return {}

        try:
            df = pd.read_csv(PRICE_CACHE_FILE, dtype={'code': str, 'date': str})
            df = filter_completed_rows(df, 'date')
            if 'code' not in df.columns or 'date' not in df.columns:
                raise ValueError('价格缓存缺少 code/date')

            # 新缓存的正式字段。旧 close 只用于兼容历史缓存，不作为生产写入口径。
            price_col = 'close_raw' if 'close_raw' in df.columns else 'close'
            if price_col not in df.columns:
                raise ValueError('价格缓存缺少 close_raw（且无旧 close 兼容列）')
            df[price_col] = pd.to_numeric(df[price_col], errors='coerce')
            df['code'] = df['code'].astype(str).str.strip()
            df = df[df['code'].ne('') & df[price_col].gt(0)].copy()
            if 'trade_status' in df.columns:
                status = df['trade_status'].fillna('').astype(str).str.lower().str.strip()
                # 只将明确 traded 计入涨跌统计；旧缓存没有状态列时兼容为全量可交易。
                df = df[status.eq('traded')].copy()

            df['date_clean'] = df['date'].str.replace('-', '')
            df = df[df['date_clean'].str.fullmatch(r'\d{8}', na=False)]
            df = (df.sort_values(['code', 'date_clean'])
                    .drop_duplicates(['code', 'date_clean'], keep='last'))

            # 在每只股票内计算涨跌幅
            df['prev_close'] = df.groupby('code')[price_col].shift(1)
            df['chg_pct'] = (df[price_col] / df['prev_close'] - 1) * 100
            df = df[(df['prev_close'] > 0) & df['chg_pct'].notna()]

            # 按日统计涨跌家数
            daily_up = df[df['chg_pct'] > 0.1].groupby('date_clean').size()
            daily_dn = df[df['chg_pct'] < -0.1].groupby('date_clean').size()
            daily_eligible = df.groupby('date_clean').size()

            all_dates = df['date_clean'].unique()
            result = {}
            for d in all_dates:
                up = int(daily_up.get(d, 0))
                dn = int(daily_dn.get(d, 0))
                eligible = int(daily_eligible.get(d, 0))
                total = up + dn
                result[d] = {
                    'date': d,
                    'up': up,
                    'down': dn,
                    'eligible': eligible,
                    'flat': max(0, eligible - up - dn),
                    'breadth_total': total,
                    'quality': 'ok' if total >= MIN_MARKET_BREADTH else 'degraded',
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
        latest_day = get_latest_date().strftime("%Y%m%d")
        latest_api = None
        if latest_day in all_dates:
            latest_api = self._fetch_longhu_sentiment(get_latest_date().strftime("%Y-%m-%d"))

        data = []
        for d in all_dates:
            z_data = zt.get(d, {'limit_up': 0, 'limit_down': 0})
            local_ad = ad.get(d)
            a_data = local_ad or {
                'up': 0, 'down': 0, 'eligible': 0, 'flat': 0,
                'breadth_total': 0, 'quality': 'missing', 'ad_ratio': 0.5,
            }
            ad_source = 'price_cache_raw' if local_ad else 'none'
            limit_source = 'limit_cache' if d in zt else 'none'

            # LongHu 只补本地缺失/宽度不足，不能覆盖完整 canonical 本地真源。
            if d == latest_day and latest_api:
                api_date = str(latest_api.get('date') or '').replace('-', '')
                if api_date and api_date != d:
                    latest_api = None
                else:
                    api_width = int(latest_api.get('up', 0) or 0) + int(latest_api.get('down', 0) or 0)
                    local_width = int((local_ad or {}).get('breadth_total', 0) or 0)
                    if not local_ad or local_width < MIN_MARKET_BREADTH:
                        if api_width > local_width and api_width > 0:
                            a_data = {
                                'up': int(latest_api.get('up', 0) or 0),
                                'down': int(latest_api.get('down', 0) or 0),
                                'eligible': api_width,
                                'flat': 0,
                                'breadth_total': api_width,
                                'quality': 'ok' if api_width >= MIN_MARKET_BREADTH else 'degraded',
                                'ad_ratio': float(latest_api.get('ad_ratio', 0.5) or 0.5),
                            }
                            ad_source = 'longhu_fallback'
                    # 涨停缓存已有记录时保持本地口径；只有本地没有涨跌停数才补 API。
                    if d not in zt and (int(latest_api.get('zt', 0) or 0) or int(latest_api.get('dt', 0) or 0)):
                        z_data = {
                            'limit_up': int(latest_api.get('zt', 0) or 0),
                            'limit_down': int(latest_api.get('dt', 0) or 0),
                        }
                        limit_source = 'longhu_fallback'

            # 纯粹基于 A/D 比例 (Breadth only)
            raw_score = a_data['ad_ratio']

            data.append({
                'date': d,
                'limit_up': z_data['limit_up'],
                'limit_down': z_data['limit_down'],
                'market_up': a_data['up'],
                'market_down': a_data['down'],
                'ad_ratio': a_data['ad_ratio'],
                'ad_source': ad_source,
                'ad_eligible': a_data.get('eligible', 0),
                'ad_total': a_data.get('breadth_total', 0),
                'ad_quality': a_data.get('quality', 'missing'),
                'limit_source': limit_source,
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
