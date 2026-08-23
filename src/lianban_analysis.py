# pyright: reportMissingTypeStubs=false
# pyright: reportUnnecessaryCast=false
# pyright: reportUnnecessaryTypeIgnoreComment=false
"""
连板股分析可视化 — TDX通达信风格 v3
分析最近4个月的连板高度、断板高度、压力高度，含涨跌停家数与情绪判断

输出:
    1. 连板高度分析.html  — ECharts交互图表 (TDX终端风格，可缩放)
    2. 连板高度分析.xlsx  — Excel数据 + 内嵌折线图
    3. 连板高度分析.png   — 静态图表（备用）

用法:
    python lianban_analysis.py          # 默认分析最近80个交易日(约4个月)
    python lianban_analysis.py 60       # 分析最近60个交易日
"""

import sys
import os
import time
from functools import lru_cache
import json
import subprocess
import warnings
warnings.filterwarnings('ignore')

# Windows UTF-8 输出
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from data_sources.models import normalize_code

_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


import hashlib
import requests  # type: ignore

@lru_cache(maxsize=1 << 14)
def _normalize_date_token_text(text: str) -> str:
    """纯文本版 (被 lru_cache 记忆): 一轮报告调 3.7 万次, 但不同日期只 ~200 个。"""
    text = text.strip().replace("-", "").replace("/", "")[:8]
    if len(text) != 8 or not text.isdigit():
        return ""
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return ""
    return text


def _normalize_date_token(value):
    """将交易日值规范化为 YYYYMMDD；无法解析时返回空字符串。"""
    return _normalize_date_token_text(str(value or ""))


_TRADE_DATE_MARKER = "__CODEX_TRADE_DATES__"
_TRADE_DATE_TIMEOUT_SECONDS = 12


# 交易日清单记忆 (按涨停缓存文件指纹 + cutoff): 一轮报告调 2 次, 每次重读整份缓存
# (实测 0.59s/次)。⚠️ key 必须含文件指纹 —— 步骤1 会在运行中途写入当日涨停池,
# 只按 cutoff 记会把当天新写的交易日钉成看不见 (同 time_utils.get_latest_date 的事故)。
_TRADE_DATES_MEMO: dict = {}


def get_cached_trading_dates():
    """返回涨跌停缓存中不晚于报告截止日的已知交易日。"""
    if not os.path.exists(CACHE_FILE):
        return []
    try:
        from time_utils import get_latest_date, _cache_file_stamp

        cutoff = get_latest_date().strftime("%Y%m%d")
        stamp = _cache_file_stamp(CACHE_FILE)
        key = (stamp, cutoff)
        if stamp is not None and key in _TRADE_DATES_MEMO:
            return list(_TRADE_DATES_MEMO[key])

        frame = pd.read_csv(
            CACHE_FILE,
            encoding="utf-8-sig",
            dtype=str,
            usecols=["日期"],
        )
        if frame.empty:
            return []
        dates = {
            token
            for token in frame["日期"].map(_normalize_date_token)
            if token and token <= cutoff
        }
        result = sorted(dates)
        if stamp is not None:
            _TRADE_DATES_MEMO.clear()
            _TRADE_DATES_MEMO[key] = result
        return list(result)
    except Exception as exc:
        print(f"  ⚠️ 本地交易日缓存读取失败: {exc}")
        return []


def _fetch_external_trade_dates(timeout_seconds=_TRADE_DATE_TIMEOUT_SECONDS):
    """在隔离子进程中获取交易日历，给无超时的第三方 SDK 加硬超时。"""
    script = (
        "import json\n"
        "import akshare as ak\n"
        "df = ak.tool_trade_date_hist_sina()\n"
        "values = df['trade_date'].astype(str).tolist() if 'trade_date' in df.columns else []\n"
        f"print({_TRADE_DATE_MARKER!r} + json.dumps(values, ensure_ascii=False))\n"
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, int(timeout_seconds)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(f"  ⚠️ 外部交易日历超过 {timeout_seconds}s，已终止并回退本地/工作日历")
        return []
    except Exception as exc:
        print(f"  ⚠️ 外部交易日历进程启动失败: {exc}")
        return []

    marker_line = next(
        (line for line in reversed(completed.stdout.splitlines()) if _TRADE_DATE_MARKER in line),
        "",
    )
    if not marker_line:
        detail_lines = (completed.stderr or completed.stdout or "未知错误").strip().splitlines()
        detail = detail_lines[-1] if detail_lines else "未知错误"
        print(f"  ⚠️ 外部交易日历返回异常: {detail[:200]}")
        return []
    try:
        raw_values = json.loads(marker_line.split(_TRADE_DATE_MARKER, 1)[1])
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"  ⚠️ 外部交易日历解析失败: {exc}")
        return []
    return sorted({token for token in map(_normalize_date_token, raw_values) if token})


def get_trading_dates(n_days=120, now=None, calendar_provider=None):
    """Return the latest closed trading days with cache reuse and a hard network timeout."""
    from data_sources.calendar_provider import CalendarProvider
    from paths import CALENDAR_CACHE, FETCH_STATUS_CACHE
    from data_sources.fetch_status import FetchStatusStore
    from time_utils import get_report_cutoff

    explicit_now = now is not None
    if now is None:
        # GitHub Actions runner uses UTC; report cutoff follows A-share Beijing time.
        now = datetime.now(_SHANGHAI_TZ).replace(tzinfo=None)
    elif getattr(now, "tzinfo", None) is not None:
        now = now.astimezone(_SHANGHAI_TZ).replace(tzinfo=None)
    cached_dates = []
    if calendar_provider is not None:
        calendar = calendar_provider
    elif explicit_now:
        calendar = CalendarProvider(
            cache_path=CALENDAR_CACHE,
            status_store=FetchStatusStore(FETCH_STATUS_CACHE),
        )
    else:
        cached_dates = get_cached_trading_dates()
        # 涨停缓存本身不是交易日历。缓存可能仍有 120 天但停在旧日期；
        # 只有覆盖报告截止日时才允许直接复用，否则必须刷新交易日历。
        cutoff_token = get_report_cutoff(now=now).strftime("%Y%m%d")
        if len(cached_dates) >= n_days and cached_dates[-1] >= cutoff_token:
            return cached_dates[-n_days:][::-1]

        external_dates = _fetch_external_trade_dates()

        def bounded_source():
            if not external_dates:
                raise TimeoutError("external trading calendar unavailable within timeout")
            return pd.DataFrame({"trade_date": external_dates})

        calendar = CalendarProvider(
            source=bounded_source,
            cache_path=CALENDAR_CACHE,
            status_store=FetchStatusStore(FETCH_STATUS_CACHE),
        )
    try:
        today = now.strftime("%Y-%m-%d")
        dates = calendar.trading_days("1900-01-01", today)
        if cached_dates:
            dates = sorted(set(dates) | {f"{day[:4]}-{day[4:6]}-{day[6:8]}" for day in cached_dates})
        # 显式 now/REPORT_DATE 仍需遵守收盘边界；不能把盘中当天当成收盘日。
        if now.hour < calendar.close_hour and today in dates:
            dates.remove(today)
        if len(dates) >= n_days:
            return [day.replace("-", "") for day in dates[-n_days:]][::-1]
        if dates:
            return [day.replace("-", "") for day in dates[::-1]]
    except Exception as exc:
        print(f"  ⚠️ 获取交易日历失败, 回退到工作日历: {exc}")

    today = now if now.hour >= 16 else now - timedelta(days=1)
    dates = []
    for i in range(int(n_days * 2.0)):
        d = today - timedelta(days=i)
        if d.weekday() < 5:
            dates.append(d.strftime("%Y%m%d"))
    return dates

# ============================================================
# 财联社 API 数据获取 (支持6个月+历史数据)
# ============================================================
def _cls_generate_sign(params):
    """财联社API签名生成"""
    sorted_params = sorted(params.items())
    sign_string = ''.join([f"{key}={value}" for key, value in sorted_params])
    sign_string += ",cailianpressPcANBfjw"
    return hashlib.md5(sign_string.encode('utf-8')).hexdigest()


def _fetch_cls_one_day(date_str):
    """从财联社API获取某天的涨停分析数据 (同步版)"""
    import time as _time
    params = {
        'date': date_str, 'os': 'android', 'sv': '8.3.5', 'ov': '28',
        'net': '', 'app': 'cailianpress', 'channel': '6', 'motif': '0',
        'province_code': '4108', 'token': '', 'mb': 'HUAWEI-ELE-AL00',
        'uid': '', 'sign': '', 'timestamp': str(int(_time.time()))
    }
    params['sign'] = _cls_generate_sign(params)
    headers = {'accept-encoding': 'gzip', 'user-agent': 'okhttp/4.9.0'}
    url = 'https://x-quote.cls.cn/v2/quote/a/plate/up_down_analysis'

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=4)
        if resp.status_code != 200:
            return None, pd.DataFrame(), False
        data = resp.json()
        if data.get('code') not in (0, 200) or 'data' not in data:
            return None, pd.DataFrame(), False
    except Exception:
        return None, pd.DataFrame(), False

    d = data['data']

    # --- 解析 continuous_limit_up → 连板股数据 ---
    clu = d.get('continuous_limit_up', [])

    # --- 解析 plate_stock → 全部涨停股 (含首板) ---
    ps = d.get('plate_stock', [])
    all_zt_stocks = {}  # code -> {名称, 连板数, ...}

    # 先从 continuous_limit_up 提取连板股
    for item in clu:
        height = int(item.get('height', 1))
        for s in item.get('stock_list', []):
            code = s.get('secu_code', '')
            name = s.get('secu_name', '')
            if code:
                all_zt_stocks[code] = {'代码': code, '名称': name, '连板数': height}

    # 再从 plate_stock 提取所有涨停股 (首板的连板数=1)
    for plate in ps:
        for s in plate.get('stock_list', []):
            code = s.get('secu_code', '')
            name = s.get('secu_name', '')
            if code and code not in all_zt_stocks:
                all_zt_stocks[code] = {'代码': code, '名称': name, '连板数': 1}

    if not all_zt_stocks:
        return None, pd.DataFrame(), False

    zt_df = pd.DataFrame(list(all_zt_stocks.values()))
    return zt_df, pd.DataFrame(), True

def _fetch_em_one_day(date_str):
    """从东方财富获取某天的涨停数据 (备用通道)"""
    try:
        import akshare as ak
        df = ak.stock_zt_pool_em(date=date_str)
        if df is not None and not df.empty:
            res_df = pd.DataFrame()
            res_df['代码'] = df['代码'].astype(str).str.zfill(6)
            res_df['名称'] = df['名称']
            res_df['连板数'] = df['连板数']
            return res_df, pd.DataFrame(), True
    except Exception:
        pass
    return None, pd.DataFrame(), False

def _fetch_multi_channel(date_str):
    """多渠道获取一天的数据"""
    zt_df, dt_df, success = _fetch_cls_one_day(date_str)
    if not success or zt_df is None or zt_df.empty:
        zt_df, dt_df, success = _fetch_em_one_day(date_str)
    return date_str, zt_df, dt_df, success


# ============================================================
# 本地CSV缓存管理
# ============================================================
# 缓存路径统一由 paths.py 定义 (单一真源)
from paths import ZT_CACHE_FILE as CACHE_FILE
from paths import LIMIT_POOL_META_CACHE
IS_GITHUB_ACTIONS = os.environ.get('GITHUB_ACTIONS') == 'true'
CACHE_MAX_SIZE_MB = 10 if IS_GITHUB_ACTIONS else 100  # CI: 10MB, 本地: 100MB

def _trim_cache(filepath, date_col='日期', max_size_mb=CACHE_MAX_SIZE_MB):
    """检查缓存文件大小，如超过限制则删除最老的数据"""
    if not os.path.exists(filepath):
        return
    file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
    if file_size_mb <= max_size_mb:
        return
    try:
        df = pd.read_csv(filepath, dtype=str, encoding='utf-8-sig')
        if df.empty or date_col not in df.columns:
            return
        original_rows = len(df)
        original_size = file_size_mb
        unique_dates = sorted(df[date_col].unique())
        avg_size_per_date = file_size_mb / len(unique_dates) if unique_dates else 0
        excess_mb = file_size_mb - max_size_mb * 0.9
        dates_to_remove = max(1, int(excess_mb / avg_size_per_date)) if avg_size_per_date > 0 else 1
        dates_to_drop = unique_dates[:dates_to_remove]
        df = df[~df[date_col].isin(dates_to_drop)]
        df.to_csv(filepath, index=False, encoding='utf-8-sig')
        new_size_mb = os.path.getsize(filepath) / (1024 * 1024)
        print(f"  🗑️ 缓存瘦身: {os.path.basename(filepath)} "
              f"{original_size:.1f}MB → {new_size_mb:.1f}MB "
              f"(删除 {len(dates_to_drop)} 天旧数据, "
              f"{original_rows - len(df)} 行)")
        if new_size_mb > max_size_mb:
            _trim_cache(filepath, date_col, max_size_mb)
    except Exception as e:
        print(f"  ⚠️ 缓存瘦身失败 ({os.path.basename(filepath)}): {e}")


def _load_cache():
    """从本地CSV加载缓存的涨停/跌停数据"""
    if not os.path.exists(CACHE_FILE):
        return {}, {}
    try:
        df = pd.read_csv(CACHE_FILE, encoding='utf-8-sig', dtype={'日期': str, '代码': str})
        if df.empty:
            return {}, {}
        df['代码'] = df['代码'].map(normalize_code)

        zt_data = {}
        dt_data = {}
        for date_str, gdf in df.groupby('日期'):
            zt_rows = gdf[gdf['类型'] == 'ZT'].drop(columns=['类型'], errors='ignore')
            dt_rows = gdf[gdf['类型'] == 'DT'].drop(columns=['类型'], errors='ignore')
            if not zt_rows.empty:
                zt_data[date_str] = zt_rows.reset_index(drop=True)  # type: ignore
            if not dt_rows.empty:
                dt_data[date_str] = dt_rows.reset_index(drop=True)
            else:
                dt_data[date_str] = pd.DataFrame()  # type: ignore
        return zt_data, dt_data
    except Exception as e:
        print(f"  ⚠️ 缓存加载失败: {e}")
        return {}, {}


def _trim_future_cache(zt_data, dt_data, latest_closed_date):
    """Drop rows newer than the closed-day boundary before selecting history."""
    cutoff = str(latest_closed_date).replace('-', '')
    return (
        {date: frame for date, frame in zt_data.items() if str(date).replace('-', '') <= cutoff},
        {date: frame for date, frame in dt_data.items() if str(date).replace('-', '') <= cutoff},
    )


def fetch_zt_pool_data(n_trading_days=120, provider=None, calendar_provider=None):
    """Fetch historical ZT/DT pools through the canonical LimitPoolProvider."""
    from data_sources.fetch_status import FetchStatusStore
    from data_sources.limit_pool_provider import LimitPoolProvider
    from data_sources.models import FetchStatus
    from paths import FETCH_STATUS_CACHE

    cached_zt, cached_dt = _load_cache()
    if calendar_provider is None:
        candidate_dates = get_trading_dates(n_trading_days)
    else:
        candidate_dates = get_trading_dates(n_trading_days, calendar_provider=calendar_provider)
    cache_changed = False
    if candidate_dates:
        before_zt_dates = set(cached_zt)
        before_dt_dates = set(cached_dt)
        cached_zt, cached_dt = _trim_future_cache(cached_zt, cached_dt, candidate_dates[0])
        cache_changed = set(cached_zt) != before_zt_dates or set(cached_dt) != before_dt_dates

    provider = provider or LimitPoolProvider(status_store=FetchStatusStore(FETCH_STATUS_CACHE))
    dates_to_fetch = [d for d in candidate_dates if d not in cached_zt]
    zt_data = dict(cached_zt)
    dt_data = dict(cached_dt)
    new_dates_fetched = 0
    fetched_results = []

    print(f"\n📥 正在获取最近 {n_trading_days} 个交易日的涨停/跌停数据...")
    if dates_to_fetch:
        results = provider.fetch_history([f"{d[:4]}-{d[4:6]}-{d[6:8]}" for d in dates_to_fetch])
        for date_str, result in results.items():
            fetched_results.append(result)
            canonical = date_str.replace("-", "")
            data = result.data
            if data is None:
                print(f"  ⚠️ {canonical} | {result.status.value}: {result.message}")
                continue
            data = data.copy()
            zt_part = data[data["pool_type"] == "ZT"] if "pool_type" in data.columns else pd.DataFrame()
            dt_part = data[data["pool_type"] == "DT"] if "pool_type" in data.columns else pd.DataFrame()
            if result.status in {FetchStatus.SUCCESS, FetchStatus.ZERO} or not zt_part.empty:
                zt_data[canonical] = pd.DataFrame({
                    "代码": zt_part["code"].astype(str).str[2:].tolist(),
                    "名称": zt_part["name"].astype(str).tolist(),
                    "连板数": pd.to_numeric(zt_part["limit_count"], errors="coerce").fillna(1).astype(int).tolist(),
                })
            if result.status in {FetchStatus.SUCCESS, FetchStatus.ZERO} or not dt_part.empty:
                dt_data[canonical] = pd.DataFrame({
                    "代码": dt_part["code"].astype(str).str[2:].tolist(),
                    "名称": dt_part["name"].astype(str).tolist(),
                })
            if result.status in {FetchStatus.SUCCESS, FetchStatus.ZERO, FetchStatus.PARTIAL}:
                new_dates_fetched += 1
                print(f"  🆕 {canonical} | {result.status.value} | {result.actual_count} 条 | {result.source}")
            else:
                print(f"  ⚠️ {canonical} | {result.status.value}: {result.message}")

        _save_limit_pool_metadata(fetched_results)

    if new_dates_fetched > 0 or cache_changed:
        _save_cache(zt_data, dt_data)
        print(f"\n  💾 缓存已更新: 新增 {new_dates_fetched} 天, 共 {len(zt_data)} 天")

    selected_dates = [d for d in candidate_dates if d in zt_data][:n_trading_days]
    final_zt = {d: zt_data[d] for d in selected_dates}
    final_dt = {d: dt_data.get(d, pd.DataFrame()) for d in selected_dates}
    return final_zt, final_dt


def refresh_latest_limit_pool(zt_data, dt_data, date: str, provider, persist: bool = False):
    """Refresh one closed day through LimitPoolProvider and update legacy maps."""
    from data_sources.models import FetchStatus

    canonical_date = str(date).replace("-", "")
    iso_date = f"{canonical_date[:4]}-{canonical_date[4:6]}-{canonical_date[6:8]}"
    result = provider.fetch_day(iso_date)
    if result.data is None:
        return result

    data = result.data.copy()

    def legacy_pool(pool_type: str, include_count: bool):
        part = data[data["pool_type"] == pool_type].copy()
        columns = ["代码", "名称"] + (["连板数"] if include_count else [])
        if part.empty:
            return pd.DataFrame(columns=columns)
        out = pd.DataFrame({
            "代码": part["code"].astype(str).str[2:],
            "名称": part["name"].astype(str),
        })
        if include_count:
            out["连板数"] = pd.to_numeric(part["limit_count"], errors="coerce").fillna(1).astype(int)
        return out[columns].reset_index(drop=True)

    present_types = set(data["pool_type"].astype(str)) if not data.empty else set()
    complete = result.status in {FetchStatus.SUCCESS, FetchStatus.ZERO}
    if complete or "ZT" in present_types:
        zt_data[canonical_date] = legacy_pool("ZT", include_count=True)
    if complete or "DT" in present_types:
        dt_data[canonical_date] = legacy_pool("DT", include_count=False)
    if persist and (complete or present_types):
        _save_cache(zt_data, dt_data)
    return result


def _save_cache(zt_data, dt_data):
    """把涨停/跌停数据保存到本地CSV缓存"""
    rows = []
    keep_cols = ['代码', '名称', '连板数', '涨停统计']

    for date_str, df in zt_data.items():
        sub = pd.DataFrame()
        sub['日期'] = [date_str] * len(df)
        sub['类型'] = 'ZT'
        for col in keep_cols:
            if col in df.columns:
                sub[col] = df[col].values
        if '代码' in sub.columns:
            sub['代码'] = sub['代码'].map(normalize_code)
        rows.append(sub)

    for date_str, df in dt_data.items():
        if df is not None and not df.empty:
            sub = pd.DataFrame()
            sub['日期'] = [date_str] * len(df)
            sub['类型'] = 'DT'
            for col in ['代码', '名称']:
                if col in df.columns:
                    sub[col] = df[col].values
            if '代码' in sub.columns:
                sub['代码'] = sub['代码'].map(normalize_code)
            rows.append(sub)

    if rows:
        cache_df = pd.concat(rows, ignore_index=True)
        cache_df = cache_df.drop_duplicates()
        cache_df.to_csv(CACHE_FILE, index=False, encoding='utf-8-sig')
        _trim_cache(CACHE_FILE, date_col='日期')


def _save_limit_pool_metadata(results):
    """Persist source/status provenance without changing the legacy cache schema."""
    if not results:
        return
    columns = [
        'date', 'pool_type', 'source', 'status', 'expected_count',
        'actual_count', 'fetched_at', 'fallback_level', 'message', 'run_id',
    ]
    existing = pd.DataFrame(columns=columns)
    if os.path.exists(LIMIT_POOL_META_CACHE):
        try:
            existing = pd.read_csv(LIMIT_POOL_META_CACHE, dtype=str).fillna('')
        except Exception:
            existing = pd.DataFrame(columns=columns)
    rows = []
    for result in results:
        data = result.data if isinstance(result.data, pd.DataFrame) else pd.DataFrame()
        for pool_type in ('ZT', 'DT'):
            part = data[data.get('pool_type', pd.Series(dtype=str)).astype(str) == pool_type]
            source = result.source or ''
            fallback_level = 'primary'
            if '|CHECK:' in source or 'eastmoney_push2ex' in source or 'ths_limit_up' in source:
                fallback_level = 'fallback_or_crosscheck'
            rows.append({
                'date': str(result.date),
                'pool_type': pool_type,
                'source': source,
                'status': result.status.value,
                'expected_count': str(result.expected_count),
                'actual_count': str(len(part)),
                'fetched_at': result.finished_at.isoformat(),
                'fallback_level': fallback_level,
                'message': result.message,
                'run_id': result.run_id,
            })
    merged = pd.concat([existing, pd.DataFrame(rows, columns=columns)], ignore_index=True)
    merged = merged.drop_duplicates(['date', 'pool_type'], keep='last')
    merged.to_csv(LIMIT_POOL_META_CACHE, index=False, encoding='utf-8-sig')


def judge_sentiment(ad_ratio):
    """仅根据涨跌家数比判断市场情绪 (5个层级)"""
    if ad_ratio >= 0.8:
        return '🔥极强', '#ff0000'
    elif ad_ratio >= 0.55:
        return '🌤️强势', '#00ff00'
    elif ad_ratio >= 0.45:
        return '⚖️中性', '#ffff00'
    elif ad_ratio >= 0.20:
        return '☁️弱势', '#6699ff'
    else:
        return '❄️冰点', '#cc66ff'


def analyze_lianban(zt_data, dt_data):
    """分析连板数据，含龙头首板日期追踪 (精确历史回溯版)"""
    sorted_dates = sorted(zt_data.keys())
    results = []

    # 准备市场情绪因子
    try:
        from limit_ratio_factor import MarketSentimentFactor
        msf = MarketSentimentFactor()
    except ImportError:
        msf = None
    
    # 建立快捷查询索引: code -> {date: height}
    stock_history = {}
    for d_str, df in zt_data.items():
        if '代码' in df.columns and '连板数' in df.columns:
            # ⚠️ 两列 tolist() 取代 iterrows(): 后者每行造一个 Series
            #    (实测 10453 行 0.83s), 逐值结果完全一致。
            for code, height in zip(df['代码'].tolist(), df['连板数'].tolist()):
                stock_history.setdefault(str(code), {})[d_str] = int(height)

    # 预处理：找出所有【曾作为市场最高板】且【高度>=3】的龙头
    all_historical_lts = {} 
    for d_str, df in zt_data.items():
        if '代码' in df.columns and '连板数' in df.columns and not df.empty:
            max_h_of_day = int(df['连板数'].max())
            if max_h_of_day < 3: continue
            
            top_stocks = df[df['连板数'] == max_h_of_day]
            # 只取第一个，避免分叉
            row = top_stocks.iloc[0]
            c = str(row['代码'])
            n = str(row.get('名称', ''))
            
            # 追溯首板
            best_sb = d_str
            cur_h = max_h_of_day
            test_idx = sorted_dates.index(d_str) - 1
            while test_idx >= 0 and cur_h > 1:
                pdate = sorted_dates[test_idx]
                ph = stock_history.get(c, {}).get(pdate, 0)
                if ph > 0 and ph < cur_h:
                    best_sb = pdate
                    cur_h = ph
                    test_idx -= 1
                else: break
            
            key = (n, best_sb)
            if key not in all_historical_lts:
                all_historical_lts[key] = {'peak_h': max_h_of_day, 'peak_date': d_str}
            else:
                if max_h_of_day > all_historical_lts[key]['peak_h']:
                    all_historical_lts[key]['peak_h'] = max_h_of_day
                    all_historical_lts[key]['peak_date'] = d_str

    current_pressure = 0
    prev_day_leader_h = 0
    prev_day_leader_names = []
    prev_day_leader_codes = []

    for idx, date_str in enumerate(sorted_dates):
        df = zt_data[date_str]
        
        # 连板高度
        lianban_height = 0
        lianban_name = ""
        lianban_names_all = []
        lianban_codes_all = []
        if not df.empty and '连板数' in df.columns:
            lianban_height = int(df['连板数'].max())
            top_mask = df['连板数'] == lianban_height
            lianban_names_all = df[top_mask]['名称'].tolist()
            lianban_codes_all = [str(c) for c in df[top_mask]['代码'].tolist()]
            # 只保留一个名称，避免分叉
            lianban_name = lianban_names_all[0] if lianban_names_all else ""

        # 断板高度逻辑
        duanban_height = 0
        duanban_name = ""
        if prev_day_leader_names:
            # 检查昨天的最高板今天是否还在涨停池中且连板数增加了
            still_leading = False
            for c in prev_day_leader_codes:
                if c in stock_history and date_str in stock_history[c]:
                    if stock_history[c][date_str] > prev_day_leader_h:
                        still_leading = True
                        break
            
            if not still_leading:
                duanban_height = prev_day_leader_h
                duanban_name = ', '.join(prev_day_leader_names[:3])

        # 压力高度逻辑：5日内最高板
        window_heights = [r['连板高度'] for r in results[-4:]] + [lianban_height]
        current_pressure = max(window_heights)

        # 提取当前市场最高板的信息用于 Tooltip 显示
        ad_ratio = None
        res = {}
        if msf:
            res = msf.calculate_factor(date_str)
            ad_ratio = res.get('ad_ratio')
        sentiment, sentiment_color = judge_sentiment(ad_ratio if ad_ratio is not None else 0.5)

        # 提取当前市场最高板的信息用于 Tooltip 显示
        current_lt_name = lianban_names_all[0] if lianban_names_all else ""
        current_lt_sb_date = ""
        if lianban_height >= 1 and lianban_codes_all:
            c = lianban_codes_all[0]
            cur_h = lianban_height
            test_idx = idx - 1
            current_lt_sb_date = date_str
            while test_idx >= 0 and cur_h > 1:
                prev_date = sorted_dates[test_idx]
                prev_h = stock_history.get(c, {}).get(prev_date, 0)
                if prev_h > 0 and prev_h < cur_h:
                    current_lt_sb_date = prev_date
                    cur_h = prev_h
                    test_idx -= 1
                else: break

        results.append({
            '日期': date_str,
            '连板高度': lianban_height,
            '连板股': lianban_name,
            '断板高度': duanban_height,
            '断板股': duanban_name,
            '压力高度': current_pressure,
            '涨停数': len(df),
            '跌停数': len(dt_data.get(date_str, pd.DataFrame())),
            '涨跌比': round(float(ad_ratio), 2) if ad_ratio is not None else None,
            'up': res.get('market_up') if msf else None,
            'down': res.get('market_down') if msf else None,
            'ad_available': bool(res.get('ad_available', False)) if msf else False,
            'ad_status': res.get('ad_status', 'missing') if msf else 'missing',
            'ad_source': res.get('ad_source', 'unknown') if msf else 'unknown',
            '情绪': sentiment,
            '情绪颜色': sentiment_color,
            '龙头首板日期': current_lt_sb_date,
            '龙头名称': current_lt_name
        })

        # 更新昨日最高板信息
        prev_day_leader_h = lianban_height
        prev_day_leader_names = lianban_names_all
        prev_day_leader_codes = lianban_codes_all

    final_df = pd.DataFrame(results)
    lt_summary = "|".join([f"{n}@{d}@{info['peak_h']}@{info['peak_date']}" 
                          for (n, d), info in all_historical_lts.items()])
    final_df['历史龙头汇总'] = lt_summary

    # === 后处理: 对 up=0 且 down=0 的日期, 直接从 LongHu API 补全 ===
    # 这解决了 price_cache 陈旧时 MarketSentimentFactor 返回 fallback 值的问题
    #
    # ⚠️ 默认关闭 (2026-08-22): LongHu 接口忽略 Day 参数, 对任意历史日期都返回
    #    当前最新快照 —— 下方陈旧检测每轮都在第 3~4 个请求就 break, 一天也补不进,
    #    纯白跑 ≈ 2.6s/轮。A/D 家数的唯一真源是价格缓存 (见 sentiment 对账),
    #    这条通道只作历史排查用: 需要时设 LONGHU_AD_BACKFILL=1 打开。
    _longhu_backfill_on = os.environ.get(
        'LONGHU_AD_BACKFILL', '').strip().lower() in {'1', 'true', 'yes'}
    if _longhu_backfill_on and 'up' in final_df.columns and 'down' in final_df.columns:
        missing_mask = (
            ~final_df.get('ad_available', pd.Series(False, index=final_df.index)).fillna(False)
            | final_df['up'].isna()
            | final_df['down'].isna()
        )
        missing_dates = final_df[missing_mask]['日期'].tolist()
        # 只补最近60天, 更早的不太重要
        if len(missing_dates) > 60:
            missing_dates = missing_dates[-60:]
        if missing_dates:
            print(f"  📡 补全 {len(missing_dates)} 天的涨跌家数 (LongHu API)...")
            filled = 0
            last_api_key = None  # 用于检测陈旧 API 响应
            stale_streak = 0     # 连续陈旧响应计数 (见下方提前收口)
            # LongHu 接口忽略 Day 参数, 对任意历史日期都回当前最新快照 —— 一旦
            # 连续 3 天拿到同一组数值, 后面几十天必然也是同一份, 请求纯属白跑
            # (60 天 × 0.27s ≈ 16s)。检测到就提前收口, 结果与跑完全一致。
            STALE_STREAK_LIMIT = 3
            for d_str in missing_dates:
                d_api = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:]}" if '-' not in d_str else d_str
                try:
                    api_url = "https://apphwshhq.longhuvip.com/w1/api/index.php"
                    api_headers = {
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; PFEM10 Build/PQ3A.190605.003)",
                    }
                    api_params = {
                        "a": "GetPlateInfo_w38", "st": "100", "c": "DailyLimitResumption",
                        "PhoneOSNew": "1", "DeviceID": "20adcd18-9e93-3bb7-b4d5-c9fd5fa30b3f",
                        "VerSion": "5.23.0.4", "Index": "0", "apiv": "w44", "Day": d_api
                    }
                    api_payload = "&".join([f"{k}={v}" for k, v in api_params.items()])
                    resp = requests.post(api_url, data=api_payload, headers=api_headers, timeout=10)
                    if resp.status_code == 200:
                        nums = resp.json().get("nums", {})
                        if nums:
                            up_val = int(nums.get("SZJS", 0))
                            down_val = int(nums.get("XDJS", 0))
                            # 陈旧检测: 与上一次返回值完全相同 = API 缓存失效
                            curr_key = f"{up_val}_{down_val}"
                            if last_api_key == curr_key and up_val > 0:
                                # 陈旧数据, 跳过 (不填充)
                                last_api_key = curr_key
                                stale_streak += 1
                                if stale_streak >= STALE_STREAK_LIMIT:
                                    print(f"  ⏭️ API 连续 {stale_streak} 天返回同一快照 "
                                          f"(接口忽略 Day 参数), 停止无效补全")
                                    break
                                continue
                            last_api_key = curr_key
                            stale_streak = 0
                            if up_val > 0:
                                idx = final_df[final_df['日期'] == d_str].index[0]
                                final_df.at[idx, 'up'] = up_val
                                final_df.at[idx, 'down'] = down_val
                                final_df.at[idx, '涨跌比'] = round(up_val / max(up_val + down_val, 1), 2)
                                final_df.at[idx, 'ad_available'] = True
                                final_df.at[idx, 'ad_status'] = 'api_fallback'
                                final_df.at[idx, 'ad_source'] = 'longhu_api'
                                filled += 1
                except Exception:
                    pass
                time.sleep(0.12)
            print(f"  ✅ API补全完成: {filled}/{len(missing_dates)} 天")

    return final_df


# ============================================================
# ECharts 交互式 HTML (TDX 通达信风格 v3)
# ============================================================
def generate_echarts_html(df, save_path='连板高度分析.html'):
    """生成 ECharts 交互式 HTML — TDX通达信终端风格"""
    import json

    dates = pd.to_datetime(df['日期'], format='%Y%m%d')  # type: ignore
    weekday_map = {0: '一', 1: '二', 2: '三', 3: '四', 4: '五', 5: '六', 6: '日'}
    date_labels = [f"{d.strftime('%m/%d')}/{weekday_map[d.weekday()]}" for d in dates]  # type: ignore

    lianban_data = df['连板高度'].tolist()
    pressure_data = df['压力高度'].tolist()
    zt_count_data = df['涨停数'].tolist()
    dt_count_data = df['跌停数'].tolist()
    ratio_data = df['涨跌比'].tolist()

    # 连板高度标签 (只取第一个，防止分叉/重叠)
    lianban_labels = []
    for _, row in df.iterrows():
        name = row['连板股'].split(',')[0].strip() if row['连板股'] else ''
        h = int(row['连板高度'])
        lianban_labels.append(f"{name}\n{h}" if name and h > 1 else (str(h) if h > 1 else ""))

    # Tooltip 详情
    tooltip_details = []
    for _, row in df.iterrows():
        tooltip_details.append({
            'date': str(row['日期']),
            'lb': int(row['连板高度']),
            'lb_name': row['连板股'],
            'pr': int(row['压力高度']),
            'zt': int(row['涨停数']),
            'dt': int(row['跌停数']),
            'ratio': float(row['涨跌比']),
            'mood': row['情绪'],
            'mood_clr': row['情绪颜色'],
            'lt_sb': row['龙头首板日期'],
            'lt_name': row['龙头名称'],
        })

    # 龙头首板数据 (去重逻辑：同一时间段只保留最高的一个龙头，防止分叉)
    longtou_marks = []
    lb_dates_raw = df['日期'].astype(str).tolist()
    
    all_lts_summary = ""
    if '历史龙头汇总' in df.columns:
        all_lts_summary = str(df['历史龙头汇总'].iloc[-1])
        
    if all_lts_summary and all_lts_summary != 'nan':
        items = all_lts_summary.split('|')
        temp_marks = []
        for item in items:
            parts = item.split('@')
            if len(parts) == 4:
                name, sb_date, peak_h, peak_date = parts[0], parts[1], int(parts[2]), parts[3]
                if peak_h >= 3: # 记录3板及以上的核心龙头
                    temp_marks.append({
                        'name': name,
                        'sb_date': sb_date,
                        'peak_h': peak_h,
                        'peak_date': peak_date
                    })
        
        # 过滤逻辑：如果多个龙头首板日期相同，只保留高度最高的
        # 如果周期重叠，保留更强的
        temp_marks.sort(key=lambda x: (x['sb_date'], -x['peak_h']))
        filtered = []
        seen_sb = set()
        for m in temp_marks:
            if m['sb_date'] in seen_sb: continue
            if not filtered:
                filtered.append(m)
                seen_sb.add(m['sb_date'])
            else:
                last = filtered[-1]
                # 如果当前龙头的首板在上一龙头的周期内，且当前更弱，则跳过
                if m['sb_date'] <= last['peak_date'] and m['peak_h'] <= last['peak_h']:
                    continue
                filtered.append(m)
                seen_sb.add(m['sb_date'])
        longtou_marks = filtered
    
    # 兜底：如果字段缺失，尝试使用原有的逐日提取逻辑
    if not longtou_marks:
        seen_dragons = set()
        for i, row in df.iterrows():
            sb_date = row.get('龙头首板日期', '')
            lt_name = row.get('龙头名称', '')
            lb = int(row.get('连板高度', 0))
            if sb_date and lt_name and lb >= 3 and lt_name not in seen_dragons:
                longtou_marks.append({
                    'name': lt_name,
                    'sb_date': sb_date,
                    'peak_h': lb,
                    'peak_date': str(row['日期'])
                })
                seen_dragons.add(lt_name)

    max_idx = int(df['连板高度'].idxmax())  # type: ignore
    max_row = df.iloc[max_idx]  # type: ignore
    last_row = df.iloc[-1]  # type: ignore
    date_range = f"{dates.iloc[0].strftime('%Y/%m/%d')} ~ {dates.iloc[-1].strftime('%Y/%m/%d')}"  # type: ignore

    html_content = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>短线情绪 — 市场高度</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ width: 100%; height: 100%; overflow: hidden; background: #000; }}
body {{
    color: #cccccc;
    font-family: 'Microsoft YaHei', sans-serif;
    display: flex;
    flex-direction: column;
}}
.top-bar {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 2px 12px;
    background: #0d0d0d;
    border-bottom: 1px solid #222;
    height: 30px;
    flex-shrink: 0;
}}
.nav-left {{ display: flex; align-items: center; gap: 10px; }}
.nav-tab {{ font-size: 13px; cursor: pointer; color: #888; }}
.tab-active {{ color: #ff0000; font-weight: bold; border-bottom: 2px solid #ff0000; }}
.nav-right {{ display: flex; align-items: center; gap: 5px; }}
.ind-btn {{
    padding: 1px 8px;
    font-size: 12px;
    cursor: pointer;
    border: 1px solid #333;
    background: #111;
    color: #666;
    border-radius: 2px;
}}
.ind-btn.on {{ border-color: #aa0000; color: #ff3232; }}
.ind-btn.on-yel {{ border-color: #aa8800; color: #ffcc00; }}
.chart-wrap {{ flex: 1; display: flex; flex-direction: column; padding: 10px; }}
#mainChart {{ flex: 65; }}
#subChart {{ flex: 35; border-top: 1px solid #1a1a1a; }}
.bot-bar {{
    background: #0d0d0d;
    border-top: 1px solid #222;
    padding: 2px 15px;
    font-size: 12px;
    display: flex;
    gap: 20px;
    align-items: center;
    height: 26px;
    flex-shrink: 0;
}}
.c-cyan {{ color: #00ffff; }} .c-yel {{ color: #ffff00; }} .c-red {{ color: #ff4444; }} .c-grn {{ color: #00ff00; }}
</style>
</head>
<body>

<div class="top-bar">
    <div class="nav-left">
        <span class="nav-tab">自定义</span>
        <span class="nav-tab">涨跌停</span>
        <span class="nav-tab tab-active">市场高度</span>
    </div>
    <div class="nav-right">
        <span class="ind-btn on" data-s="连板高度" onclick="tog(this)">连板高度</span>
        <span class="ind-btn on-yel" data-s="压力高度" onclick="tog(this)">压力高度</span>
    </div>
</div>

<div class="chart-wrap">
    <div id="mainChart"></div>
    <div id="subChart"></div>
</div>

<div class="bot-bar">
    <span>🏆 最高: <b class="c-red">{int(max_row['连板高度'])}板</b>({max_row['连板股'].split(',')[0]})</span>
    <span>🚧 压力: <b class="c-yel">{int(df['压力高度'].iloc[-1])}板</b></span>
    <span>📊 涨停:<b class="c-red">{int(last_row['涨停数'])}</b> 跌停:<b class="c-grn">{int(last_row['跌停数'])}</b> 比:<b class="c-yel">{last_row['涨跌比']}</b></span>
    <span style="margin-left:auto; color:#666">{date_range}</span>
</div>

<script>
var DL_raw = {json.dumps(lb_dates_raw)};
var DL = {json.dumps(date_labels, ensure_ascii=False)};
var LB = {json.dumps(lianban_data)};
var PR = {json.dumps(pressure_data)};
var ZT = {json.dumps(zt_count_data)};
var DT = {json.dumps(dt_count_data)};
var RT = {json.dumps(ratio_data)};
var LBL = {json.dumps(lianban_labels, ensure_ascii=False)};
var TD = {json.dumps(tooltip_details, ensure_ascii=False)};
var LTM = {json.dumps(longtou_marks, ensure_ascii=False)};

var mc = echarts.init(document.getElementById('mainChart'));
var mOpt = {{
    backgroundColor: '#000000',
    grid: {{ left: 40, right: 20, top: 40, bottom: 30 }},
    tooltip: {{
        trigger: 'axis',
        axisPointer: {{ type: 'cross', crossStyle: {{ color: '#888', type: 'dashed' }} }},
        backgroundColor: 'rgba(0,0,0,0.8)',
        borderColor: '#ff3232',
        borderWidth: 1,
        textStyle: {{ color: '#fff', fontSize: 12 }},
        formatter: function(p) {{
            var i = p[0].dataIndex, d = TD[i];
            var h = '<div style="border-bottom:1px solid #555;padding-bottom:5px;margin-bottom:5px;">';
            h += '<b style="color:#ffff00;font-size:14px;">' + d.date + '</b>  <span style="color:' + d.mood_clr + '">' + d.mood + '</span></div>';
            h += '连板高度: <b style="color:#ffffff;font-size:14px;">' + d.lb + '</b> 板 <span style="color:#ccc">(' + (d.lb_name||'无') + ')</span><br>';
            h += '压力高度: <b style="color:#ffff00">' + d.pr + '</b> 板<br>';
            h += '涨跌停比: <span style="color:#ff3232">' + d.zt + '</span> / <span style="color:#00ff00">' + d.dt + '</span><br>';
            if (d.lt_name) {{
                h += '当前龙头: <b style="color:#ffff00">' + d.lt_name + '</b>';
            }}
            return h;
        }}
    }},
    xAxis: {{
        type: 'category', data: DL,
        axisLine: {{ lineStyle: {{ color: '#ff3232' }} }},
        axisLabel: {{ color: '#ccc', fontSize: 12 }},
        splitLine: {{ show: true, lineStyle: {{ color: '#333', type: 'dotted' }} }}
    }},
    yAxis: {{
        type: 'value', minInterval: 1,
        axisLine: {{ show: true, lineStyle: {{ color: '#ff3232' }} }},
        axisLabel: {{ color: '#ccc', fontSize: 12 }},
        splitLine: {{ show: true, lineStyle: {{ color: '#333', type: 'dotted' }} }}
    }},
    series: [
        {{
            name: '连板高度', type: 'line', data: LB,
            step: 'middle',
            symbol: 'circle', symbolSize: 4,
            lineStyle: {{ color: '#ffffff', width: 2 }},
            itemStyle: {{ color: '#ffffff' }},
            areaStyle: {{
                color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                    {{ offset: 0, color: 'rgba(255, 50, 50, 0.5)' }},
                    {{ offset: 1, color: 'rgba(255, 50, 50, 0.0)' }}
                ])
            }},
            label: {{
                show: true, position: 'top', color: '#ffff00', fontSize: 12,
                formatter: function(p){{ return LBL[p.dataIndex]; }}
            }},
            markArea: {{
                silent: true,
                itemStyle: {{ color: 'rgba(255, 0, 0, 0.15)' }},
                label: {{ position: 'insideTop', color: '#ffff00', fontSize: 12, fontWeight: 'bold', paddingTop: 5 }},
                data: LTM.map(m => {{
                    var s_idx = DL_raw.indexOf(m.sb_date);
                    var p_idx = DL_raw.indexOf(m.peak_date);
                    if (s_idx === -1 || p_idx === -1) return null;
                    return [
                        {{ xAxis: s_idx, name: m.name }},
                        {{ xAxis: p_idx }}
                    ];
                }}).filter(x => x)
            }},
            z: 10
        }},
        {{
            name: '压力高度', type: 'line', data: PR,
            step: 'middle', symbol: 'none',
            lineStyle: {{ color: '#ffff00', width: 2, type: 'dashed' }},
            z: 5
        }}
    ]
}};

var sc = echarts.init(document.getElementById('subChart'));
var sOpt = {{
    backgroundColor: '#000',
    grid: {{ left: 50, right: 20, top: 10, bottom: 20 }},
    xAxis: {{ type: 'category', data: DL, axisLabel: {{ show: false }}, axisLine: {{ lineStyle: {{ color: '#333' }} }} }},
    yAxis: {{ type: 'value', axisLabel: {{ show: false }}, splitLine: {{ show: false }} }},
    series: [
        {{ name: '涨停', type: 'bar', stack: 'a', data: ZT, itemStyle: {{ color: '#ff4444' }} }},
        {{ name: '跌停', type: 'bar', stack: 'a', data: DT, itemStyle: {{ color: '#00ff00' }} }},
        {{ name: '涨跌比', type: 'line', data: RT, symbol: 'none', lineStyle: {{ color: '#ffff00', width: 1 }} }}
    ]
}};

echarts.connect([mc, sc]);
mc.setOption(mOpt);
sc.setOption(sOpt);

function tog(btn) {{
    var s = btn.getAttribute('data-s');
    var on = btn.classList.toggle('on');
    mc.dispatchAction({{ type: 'legendToggleSelect', name: s }});
}}
window.onresize = function() {{ mc.resize(); sc.resize(); }};
</script>
</body>
</html>'''

    with open(save_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"  📈 TDX风格 HTML 报告已生成: {save_path}")


def main():
    """主入口"""
    n_days = 90
    if len(sys.argv) > 1:
        try:
            n_days = int(sys.argv[1])
        except (ValueError, IndexError): pass

    # 1. 获取数据
    zt_data, dt_data = fetch_zt_pool_data(n_days)
    if not zt_data:
        return

    # 2. 分析
    print("📊 正在进行连板逻辑分析...")
    df = analyze_lianban(zt_data, dt_data)

    # 3. 输出 Excel
    excel_file = '连板高度分析.xlsx'
    df.to_excel(excel_file, index=False)
    print(f"  📗 Excel 数据已保存: {excel_file}")

    # 4. 生成 ECharts HTML
    html_file = '连板高度分析.html'
    generate_echarts_html(df, html_file)

    print("\n✅ 分析完成!")

if __name__ == "__main__":
    main()
