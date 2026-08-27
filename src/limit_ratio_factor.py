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

import numpy as np
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


MIN_MARKET_BREADTH = 4000


# ─────────────────────────────────────────────────────────────
# 进程级 A/D 记忆 (按价格缓存文件指纹)
# ─────────────────────────────────────────────────────────────
# 一次日报运行里 MarketSentimentFactor 会被独立构造 3+ 次
# (主报告最新日校准 / A/D 对账 / lianban 分析), 每个实例的 self._ad_cache
# 只对自己有效, 于是 10MB 价格缓存被反复 parse + groupby, 实测每次约 19s。
# 这里按 (mtime_ns, size) 指纹做进程级共享: 文件没变就复用, 文件被重写
# (如价格抓取后 to_csv) 指纹立即失效, 自动重算, 不会读到旧值。
# 所有调用方都只读该 dict (.get), 故共享同一对象是安全的。
_AD_CACHE_MEMO: dict = {}


def _price_cache_fingerprint():
    """价格缓存文件指纹; 取不到 (文件不存在/权限) 返回 None 表示不可缓存。"""
    try:
        st = os.stat(PRICE_CACHE_FILE)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


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
        """从价格缓存计算 A/D 家数比 (实例缓存 → 进程级指纹缓存 → 真正计算)。"""
        if self._ad_cache is not None:
            return self._ad_cache

        fp = _price_cache_fingerprint()
        if fp is not None and fp in _AD_CACHE_MEMO:
            self._ad_cache = _AD_CACHE_MEMO[fp]
            return self._ad_cache

        result = self._compute_ad_cache()
        if fp is not None:
            # 用计算前的指纹入库: 计算过程只读不写价格缓存, 指纹仍代表这份输入。
            _AD_CACHE_MEMO[fp] = result
        return result

    def _compute_ad_cache(self):
        """从价格缓存实际计算 A/D 家数比 (无进程级缓存, 供包装器调用)。"""
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

            # 若缓存明确提供交易状态，只允许实际成交证券进入市场宽度。
            # 旧缓存没有该列时保持兼容，避免把停牌股的静态价格误算为上涨/下跌。
            if 'trade_status' in df.columns:
                status = df['trade_status'].fillna('').astype(str).str.lower().str.strip()
                df = df[status.eq('traded')].copy()

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

            # 按 raw → qfq → legacy 优先级取第一个 >0 的收盘价。
            # ⚠️ 向量化实现 (np.select 天然"首个命中优先"), 不要退回 df.apply(axis=1):
            #    18 万行逐行 Python 调用实测占整轮 17.6s, 这里 <0.1s, 结果逐值等价。
            _priority = (
                ('close_raw', 'raw'),
                ('close_qfq', 'qfq_fallback'),
                ('close_legacy', 'legacy_mixed'),
                ('close', 'legacy_mixed'),
            )
            _conds = [(df[column] > 0).to_numpy() for column, _ in _priority]
            df['ad_close'] = np.select(
                _conds, [df[column].to_numpy(dtype=float) for column, _ in _priority],
                default=np.nan)
            df['ad_basis'] = np.select(
                _conds, [basis for _, basis in _priority], default='unavailable')
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

            # 在每只股票内计算涨跌幅。只有价格行恰好来自全市场相邻交易日，
            # 才允许进入 A/D；不能把更早日期冒充上一交易日。
            market_dates = sorted(df['date_clean'].dropna().unique())
            expected_previous = {
                market_dates[index]: market_dates[index - 1]
                for index in range(1, len(market_dates))
            }
            df['prev_date_clean'] = df.groupby('code')['date_clean'].shift(1)
            df['expected_prev_date'] = df['date_clean'].map(expected_previous)
            for _column, _ in _priority:
                df['_prev_' + _column] = df.groupby('code')[_column].shift(1)
            df = df[
                df['prev_date_clean'].eq(df['expected_prev_date'])
            ].copy()

            # 跨口径的两天不能相减 (2026-08-27 加)。
            # 缓存里 raw/qfq/legacy 三种口径按日成片: 本机深度回补的 20260326~0703
            # 只有 raw, 更晚的 0706~0805 只有 legacy(旧单列 close 迁移来的未知口径),
            # 0806 起又是 raw。口径交界那天会拿"今天的 raw ÷ 昨天的 legacy", 而同一只
            # 股票两个口径的价位差着复权因子 —— 实测 20260806 raw 与 legacy 中位差
            # 0.83%, 已是日均波动量级, 足以把大批股票推过 ±0.1% 判据。该日因此得出
            # 2397/2542, 而同口径(legacy→legacy)算出的是 1778/3237, 差 620 只。
            # 岛内同口径的日对日比值本身是可信的 (与当年线上 FuPan 值 10/10 同向,
            # 且逐日"涨幅≥9.8%只数 ≥ 涨停缓存只数"的超集关系在 21/24 天成立),
            # 坏的只有交界那一天的口径混用。故要求前后两天口径一致, 否则弃用该行:
            # 交界日若两天共有某个口径 (0806 两天都有 legacy) 就走那个口径,
            # 一个共同口径都没有 (0706 的前一天 0703 只有 raw) 则该日整天判为未覆盖,
            # 交给"真源未覆盖"分支, 绝不用混口径的数冒充权威 A/D。
            # 逐股票重挑口径: 只认"当天和前一天都有值"的那一列, 仍按 raw → qfq →
            # legacy → close 优先级 (np.select 天然首个命中优先, 同上不要退回 apply)。
            _paired = [
                ((df[_column] > 0) & (df['_prev_' + _column] > 0)).to_numpy()
                for _column, _ in _priority
            ]
            df['ad_close'] = np.select(
                _paired, [df[_column].to_numpy(dtype=float) for _column, _ in _priority],
                default=np.nan)
            df['prev_close'] = np.select(
                _paired,
                [df['_prev_' + _column].to_numpy(dtype=float) for _column, _ in _priority],
                default=np.nan)
            df['ad_basis'] = np.select(
                _paired, [_basis for _, _basis in _priority], default='unavailable')
            df['prev_basis'] = df['ad_basis']
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
                    , 'eligible': market_covered
                    , 'breadth_total': up + dn
                    , 'quality': 'ok' if up + dn >= MIN_MARKET_BREADTH else 'degraded'
                    , 'ad_available': up + dn > 0
                    , 'ad_status': 'available' if up + dn > 0 else 'missing'
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
        latest_date = get_latest_date()
        today_str = latest_date.strftime("%Y%m%d")
        for d in all_dates:
            z_data = zt.get(d, {'limit_up': 0, 'limit_down': 0})
            limit_source = 'zt_cache' if d in zt else 'unavailable'
            a_data = ad.get(d, {
                'up': None, 'down': None, 'flat': None, 'ad_ratio': None,
                'ad_available': False, 'ad_status': 'missing',
                'source': 'unavailable',
            })

            # API 仅作为当日本地数据不足时的补充。完整的全市场价格缓存优先，
            # 且 API 必须明确返回同一报告日，防止陈旧快照覆盖本地宽度。
            if d == today_str:
                api_data = self._fetch_longhu_sentiment(latest_date.strftime("%Y-%m-%d"))
                api_date = str((api_data or {}).get('date') or '').replace('-', '')[:8]
                if api_data and api_date == d:
                    local_width = max(
                        int(a_data.get('breadth_total') or 0),
                        int(a_data.get('market_covered') or 0),
                        int(a_data.get('eligible') or 0),
                    )
                    api_up = int(api_data.get('up') or 0)
                    api_down = int(api_data.get('down') or 0)
                    api_flat = api_data.get('flat')
                    api_width = api_up + api_down + (
                        int(api_flat) if api_flat not in (None, '') else 0
                    )
                    if local_width < MIN_MARKET_BREADTH and api_width > local_width:
                        a_data = {
                            'up': api_up, 'down': api_down,
                            'flat': api_flat, 'ad_ratio': api_data.get('ad_ratio'),
                            'ad_available': api_data.get('ad_available', api_width > 0),
                            'ad_status': api_data.get('ad_status', 'api'),
                            'source': api_data.get('source', 'longhu_api'),
                            'eligible': api_width,
                            'breadth_total': api_up + api_down,
                            'quality': 'ok' if api_width >= MIN_MARKET_BREADTH else 'degraded',
                        }
                    if d not in zt:
                        z_data = {
                            'limit_up': int(api_data.get('zt') or 0),
                            'limit_down': int(api_data.get('dt') or 0),
                        }
                        limit_source = api_data.get('source', 'longhu_api')

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
                'ad_eligible': a_data.get('eligible', a_data.get('market_covered', 0)),
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
