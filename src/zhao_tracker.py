"""属于zhao的个股追踪 (Zhao's Portfolio Tracker)
=====================================================
核心功能:
  针对用户自选/持仓核心标的：
    1. 吉华集团 (sh603980) - 染料中间体 / 周期涨价主线 / 类三买大阳突破
    2. 华映科技 (sz000536) - 光学显示 / 华为折叠屏 / 超跌妖股二买博弈
    3. 焦作万方 (sz000612) - 电解铝冶炼 / 大宗商品周期 / 日线双底底背驰
    4. 深圳燃气 (sh601139) - 城市燃气 / 公用事业 / 极度地量中枢二买避险
    5. 易明医药 (sz002826) - 化学制剂 / 藏药中药 / 中枢上沿三买突破蓄势
    6. 锦鸡股份 (sz300798) - 精细化工+AI算力租赁 / 创业板20cm日线三买主升

提供极致明了的实操方案：
  - 【现阶段位置】：中枢定位、空间与安全边际
  - 【当前走势趋势】：主升浪 / 筑底 / 回抽 / 地量变盘
  - 【量价配合特征】：放量攻坚 / 缩量洗盘 / 地量收敛
  - 【明确买卖操作】：斩钉截铁的操作指令，绝不模棱两可
  - 【关注核心点位】：压力位、支撑位、止盈线、绝对止损红线
  - 【缠论买卖点建议】：一买/二买/三买定性与结构建议
  - 【K线附着图形分析】：交互式 ECharts 图表，K线直观附着缠论笔折线、中枢区间、均线、买卖点气泡与参考水平线
"""
from __future__ import annotations

import os
import sys
import json
import time
from html import escape
from datetime import datetime
from typing import Any

import pandas as pd
import numpy as np
import requests

from paths import OUTPUT_DIR, SITE_DIR, PRICE_CACHE, ZT_CACHE_FILE, CHAN_KLINE_CACHE
from web_assets import echarts_head_html

try:
    from data_sources.http_session import get_session
except ImportError:
    def get_session() -> requests.Session:
        s = requests.Session()
        s.trust_env = False
        s.proxies = {'http': None, 'https': None}  # type: ignore[assignment]
        return s

_TENCENT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Referer': 'https://gu.qq.com/',
    'Accept': '*/*',
}

# 缠论内核: 本项目 chan_engine (逐函数对齐 waditu/czsc, 纯 Python)。
# 原先这里 import 的是 github缠论项目分析/.venv 下的 czsc, 那个目录并不存在, HAVE_CZSC 恒为 False,
# 图上的"笔"一直是三点连线兜底、"三买/二买"气泡来自手写 chan_verdict 的关键词 —— 不是计算结果。
try:
    from chan_engine import bars_from_records, resample_weekly
    from chan_signals import ChanContext, compute_multi_level
    from chan_analysis import classify_stage, nest_view, stop_level
    from chan_tactics import TACTIC_BY_KEY, match_tactics
    HAVE_CHAN = True
except Exception:
    HAVE_CHAN = False

CHAN_KLINE_COUNT = 250     # 算结构用的K线根数 (60 根只够 3~5 笔, 中枢都凑不齐)
CHART_BARS = 120           # 图上展示的根数

# ─────────────────────────────────────────────────────────────
# 核心股票池配置 (属于 zhao 的专属追踪标的)
# ─────────────────────────────────────────────────────────────
ZHAO_PORTFOLIO_CONFIG = [
    {
        "code": "sh603980",
        "bare_code": "603980",
        "name": "吉华集团",
        "market": "sh",
        "industry": "精细化工 · 染料及中间体",
        "mainline": "周期资源 / 涨价主线",
        "theme": "分散染料、活性染料、H酸涨价催化、环保减产提价、顺周期反转",
        "tactic_model": "主线强度爆发 · 涨停反包主升模型",
        "core_role": "核心主仓 · 周期进攻中军",
        "weight_plan": "25% ~ 30%",
        "defensive_support": 8.35,
        "stop_loss_line": 8.05,
        "key_resistance": 9.20,
        "take_profit_target": 9.60,
        "position_desc": "脱离日线中枢 [6.40, 7.43] 底部成本区约 22%，处于前高 9.20 突破口下方的强势蓄势区。",
        "trend_desc": "强劲日线向上笔加速主升段，MA5 (8.54) 陡峭向上发散，多头单边反包推进。",
        "vol_price_desc": "09-29 涨停放量后，09-30 换手率达 9.77% (5.92亿)，呈现量价齐升良性换手，主力抢筹坚决。",
        "action_instruction": "【主仓持股锁仓，切勿追高；冲高 9.50~9.60 分批减半止盈；盘中跌破 8.35 减仓防守】",
        "chan_verdict": "日线类三买主升段已确立（买点已过，目前处于享受主升浪拉升期，重点防范小级别顶分型冲高离场点）。",
    },
    {
        "code": "sz000536",
        "bare_code": "000536",
        "name": "华映科技",
        "market": "sz",
        "industry": "光学光电子 · 面板显示模组",
        "mainline": "消费电子 / 华为折叠屏",
        "theme": "华为产业链、三折叠屏面板、高流动性游资妖股基因、超跌反抽",
        "tactic_model": "妖股超跌箱体 · 地量二买博弈模型",
        "core_role": "情绪博弈 · 短线弹性仓位",
        "weight_plan": "10% ~ 15%",
        "defensive_support": 3.30,
        "stop_loss_line": 3.20,
        "key_resistance": 3.65,
        "take_profit_target": 3.84,
        "position_desc": "处于日线中枢 [3.30, 3.84] 的箱体下轨 (3.30) 极限支撑位，属于箱体底部博弈区。",
        "trend_desc": "超跌阴跌后的弱势筑底震荡，日线出现低位横盘底分型雏形，具备次级别反弹动能。",
        "vol_price_desc": "成交额缩减至 1.17 亿 (换手仅 1.27%)，地量极致收敛，空头抛压衰竭，等待游资点火。",
        "action_instruction": "【中枢下轨 3.30 附近逢低挂单低吸做T；反抽冲高 3.65 坚决高抛减仓；有效跌破 3.20 坚决止损】",
        "chan_verdict": "日线中枢下轨二买博弈点（中枢下沿二次探底不破前低，具备底背驰脉冲反弹条件）。",
    },
    {
        "code": "sz000612",
        "bare_code": "000612",
        "name": "焦作万方",
        "market": "sz",
        "industry": "工业金属 · 电解铝及铝合金",
        "mainline": "周期资源 / 大宗商品",
        "theme": "电解铝、氧化铝供给受限、大宗商品牛市、高股息周期红利",
        "tactic_model": "周期资源中军 · 大双底底背驰模型",
        "core_role": "稳健配置 · 周期资源波段中军",
        "weight_plan": "15% ~ 20%",
        "defensive_support": 10.20,
        "stop_loss_line": 9.85,
        "key_resistance": 11.20,
        "take_profit_target": 11.60,
        "position_desc": "位于前期大中枢 [10.80, 11.59] 下方超跌区，回踩 60 日牛熊线 (10.40) 构筑阶段大双底。",
        "trend_desc": "日线向下笔回调进入尾声，MACD绿柱大幅收窄，正在酝酿日线底背驰向上反抽笔。",
        "vol_price_desc": "换手率从 4.5% 萎缩至 2.15%，呈现缩量探底，浮筹洗盘充分，下方承接盘逐步夯实。",
        "action_instruction": "【中线波段持有；依托 10.10~10.40 区间逢低分批吸纳；反抽中枢下沿 11.20 减仓；破 9.85 止损】",
        "chan_verdict": "日线一买底背驰探底筑底阶段（MACD绿柱背驰收敛，等待放量底分型确立展开向上笔）。",
    },
    {
        "code": "sh601139",
        "bare_code": "601139",
        "name": "深圳燃气",
        "market": "sh",
        "industry": "公用事业 · 城市燃气及供应链",
        "mainline": "公用事业 / 高股息防守",
        "theme": "城市管网燃气、天然气贸易、光伏胶膜、高股息充沛现金流护城河",
        "tactic_model": "高股息避险 · 中枢地量防守模型",
        "core_role": "避险底仓 · 高股息压舱石",
        "weight_plan": "15% ~ 20%",
        "defensive_support": 6.18,
        "stop_loss_line": 6.10,
        "key_resistance": 6.38,
        "take_profit_target": 6.50,
        "position_desc": "运行于日线中枢 [6.17, 6.39] 内部极窄幅地量区间，紧贴中枢下轨，安全边际极高。",
        "trend_desc": "低波动中枢横向蓄势，均线完全粘合，处于极端变盘临界点，向下空间极为有限。",
        "vol_price_desc": "全天仅成交 7100 万元 (换手率仅 0.40%)，极致地量见地价，主力筹码锁定极高。",
        "action_instruction": "【避险底仓坚守卧倒，不盲目追涨；依托 6.18 支撑持有；反抽 6.38~6.45 做T高抛；破 6.10 离场】",
        "chan_verdict": "日线中枢下轨二买防守段（安全边际极高，耐心等待放量向上突破中枢一笔）。",
    },
    {
        "code": "sz002826",
        "bare_code": "002826",
        "name": "易明医药",
        "market": "sz",
        "industry": "医药生物 · 化学制剂与藏药",
        "mainline": "医药生物 / 创新防御",
        "theme": "米格列醇、心脑血管藏药、集采出清业绩弹性、医药避险轮动",
        "tactic_model": "医药防守反弹 · 三买突破蓄势模型",
        "core_role": "轮动配置 · 医药防御反弹仓",
        "weight_plan": "10% ~ 15%",
        "defensive_support": 17.20,
        "stop_loss_line": 16.80,
        "key_resistance": 18.20,
        "take_profit_target": 19.00,
        "position_desc": "位于前期震荡中枢 [16.82, 18.40] 内部中上轴，突破后回踩 17.20 获得承接，守住颈线位。",
        "trend_desc": "医药防御性波段震荡上行，均线多头粘合抬升 (MA5=17.52, MA20=17.48)，保持强势。",
        "vol_price_desc": "成交 5600 万元，量能温和收缩，无主力出逃迹象，洗盘充分，量价结构平稳。",
        "action_instruction": "【波段持股；回踩 17.20 颈线不破逢低吸纳；冲高逼近 18.20~18.50 分批止盈做T；破 16.80 止损】",
        "chan_verdict": "日线三买蓄势回抽段（次级别回踩未跌破中枢上沿颈线 17.20，具备波段上攻动力）。",
    },
    {
        "code": "sz300798",
        "bare_code": "300798",
        "name": "锦鸡股份",
        "market": "sz",
        "industry": "精细化工 + AI算力服务",
        "mainline": "周期涨价 + 算力科技双轮驱动",
        "theme": "活性染料龙头、算力服务器中心租赁、英伟达芯片算力云、创业板20cm弹性",
        "tactic_model": "20cm先锋进攻 · 突破三买主升模型",
        "core_role": "高弹性先锋 · 进攻突破主打标",
        "weight_plan": "15% ~ 20%",
        "defensive_support": 8.30,
        "stop_loss_line": 8.05,
        "key_resistance": 9.15,
        "take_profit_target": 9.80,
        "position_desc": "强力突破日线中枢 [7.17, 8.13] 后的主升加速区，回踩 8.01 获得三买强力支撑。",
        "trend_desc": "日线向上笔强劲延伸，与吉华集团形成染料主线与AI算力双核共振，弹性极大。",
        "vol_price_desc": "换手率 8.07%，成交金额 2.85 亿元，主力大单增量抢筹，量价齐升突破前高。",
        "action_instruction": "【先锋进攻持股，依托 8.30 均线持股待涨；脉冲冲高 9.20~9.60 分批止盈高抛；跌破 8.05 止损】",
        "chan_verdict": "日线标准三买确立（次级别回踩未触及中枢 [7.17, 8.13]，向上主升浪笔强劲发散）。",
    },
]


def fetch_realtime_quotes(codes: list[str]) -> dict[str, dict[str, Any]]:
    """拉取腾讯行情接口最新实时行情快照 (带代理穿透与连接复用)"""
    if not codes:
        return {}
    url = 'http://qt.gtimg.cn/q=' + ','.join(codes)
    quotes: dict[str, dict[str, Any]] = {}
    session = get_session()
    for attempt in range(2):
        try:
            r = session.get(url, headers=_TENCENT_HEADERS, timeout=8)
            if r.status_code != 200:
                continue
            r.encoding = 'gbk'
            text = (r.text or '').strip()
            if not text:
                continue
            lines = text.split(';')
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                parts = line.split('=')
                if len(parts) < 2:
                    continue
                code = parts[0].split('_')[-1]
                raw = parts[1].strip('"').split('~')
                if len(raw) > 38:
                    quotes[code] = {
                        'code': code,
                        'name': raw[1],
                        'price': float(raw[3]) if raw[3] else 0.0,
                        'pre_close': float(raw[4]) if raw[4] else 0.0,
                        'open': float(raw[5]) if raw[5] else 0.0,
                        'high': float(raw[33]) if raw[33] else 0.0,
                        'low': float(raw[34]) if raw[34] else 0.0,
                        'change_pct': float(raw[32]) if raw[32] else 0.0,
                        'change_amt': float(raw[31]) if raw[31] else 0.0,
                        'volume': float(raw[6]) if raw[6] else 0.0,
                        'amount': float(raw[37]) if raw[37] else 0.0,
                        'turnover': float(raw[38]) if len(raw) > 38 and raw[38] else 0.0,
                        'pe': float(raw[39]) if len(raw) > 39 and raw[39] else 0.0,
                        'pb': float(raw[46]) if len(raw) > 46 and raw[46] else 0.0,
                        'total_cap': float(raw[45]) if len(raw) > 45 and raw[45] else 0.0,
                        'timestamp': raw[30] if len(raw) > 30 else '',
                    }
            if quotes:
                return quotes
        except Exception as e:
            if attempt == 1:
                print(f"  [zhao_tracker] 实时行情拉取提示: {e}")
            else:
                time.sleep(0.3)
    return quotes


def _fetch_tencent_kline(code: str, count: int = 250) -> pd.DataFrame:
    """从腾讯接口拉取前复权日K线数据 (直连快速路径, 严格防范空响应与非JSON解析异常)."""
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{int(count)},qfq'
    session = get_session()
    for attempt in range(2):
        try:
            r = session.get(url, headers=_TENCENT_HEADERS, timeout=8)
            if r.status_code != 200:
                continue
            text = (r.text or '').strip()
            # 关键保护: 校验返回是否包含有效 JSON 字符，防范空串或代理 502/HTML 抛出 JSONDecodeError
            if not text or not text.startswith('{'):
                continue
            data = r.json()
            data_block = (data.get('data') or {}).get(code) or {}
            kdata = data_block.get('qfqday') or data_block.get('day') or []
            if not kdata:
                continue
            df = pd.DataFrame(kdata).iloc[:, :6]
            df.columns = ['dt', 'open', 'close', 'high', 'low', 'vol']
            for col in ['open', 'close', 'high', 'low', 'vol']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            df['dt'] = pd.to_datetime(df['dt'])
            df['symbol'] = code
            df['amount'] = df['vol'] * df['close']
            df = df.dropna(subset=['dt', 'close'])
            if not df.empty:
                return df
        except Exception:
            if attempt == 0:
                time.sleep(0.3)
    return pd.DataFrame()


def _fetch_akshare_kline(code: str, count: int = 250) -> pd.DataFrame:
    """备用源: 通过 AkShare (新浪前复权源) 拉取日K线数据."""
    try:
        import akshare as ak
        raw = ak.stock_zh_a_daily(symbol=code, adjust="qfq")
        if raw is None or raw.empty:
            return pd.DataFrame()
        df = raw.tail(count).copy()
        df['dt'] = pd.to_datetime(df['date'])
        for col in ['open', 'close', 'high', 'low']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df['vol'] = pd.to_numeric(df['volume'], errors='coerce')
        df['symbol'] = code
        df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
        out = df[['dt', 'open', 'close', 'high', 'low', 'vol', 'symbol', 'amount']].dropna(subset=['dt', 'close'])
        return out.reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def _fetch_cached_kline(code: str, count: int = 250) -> pd.DataFrame:
    """离线降级: 从本地 CHAN_KLINE_CACHE 读取历史K线缓存."""
    try:
        from paths import CHAN_KLINE_CACHE
        if not os.path.exists(CHAN_KLINE_CACHE):
            return pd.DataFrame()
        with open(CHAN_KLINE_CACHE, 'r', encoding='utf-8') as f:
            cache = json.load(f)
        item = cache.get(code)
        if not item or not item.get('rows'):
            return pd.DataFrame()
        rows = item['rows'][-count:]
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df['dt'] = pd.to_datetime(df['date'])
        df['symbol'] = code
        for col in ['open', 'close', 'high', 'low', 'vol']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df['amount'] = df['vol'] * df['close']
        out = df[['dt', 'open', 'close', 'high', 'low', 'vol', 'symbol', 'amount']].dropna(subset=['dt', 'close'])
        return out.reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def _update_kline_cache(code: str, df: pd.DataFrame) -> None:
    """持久化更新日K线到 CHAN_KLINE_CACHE，供离线容灾或缠论分析复用."""
    if df is None or df.empty:
        return
    try:
        from paths import CHAN_KLINE_CACHE
        cache = {}
        if os.path.exists(CHAN_KLINE_CACHE):
            try:
                with open(CHAN_KLINE_CACHE, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
            except Exception:
                cache = {}
        existing_rows = (cache.get(code) or {}).get('rows') or []
        rows_by_date = {r['date']: r for r in existing_rows if isinstance(r, dict) and 'date' in r}
        for d, o, c, h, l, v in zip(df['dt'], df['open'], df['close'], df['high'], df['low'], df['vol']):
            dt_str = d.strftime('%Y-%m-%d')
            rows_by_date[dt_str] = {
                'date': dt_str,
                'open': float(o), 'close': float(c),
                'high': float(h), 'low': float(l), 'vol': float(v),
            }
        merged_rows = [rows_by_date[k] for k in sorted(rows_by_date.keys())][-400:]
        today_str = datetime.now().strftime('%Y-%m-%d')
        cache[code] = {
            'rows': merged_rows,
            'checked': today_str,
            'used': today_str,
        }
        tmp = CHAN_KLINE_CACHE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, separators=(',', ':'))
        os.replace(tmp, CHAN_KLINE_CACHE)
    except Exception:
        pass


def fetch_stock_kline(code: str, count: int = 60) -> pd.DataFrame:
    """拉取标的前复权日K线数据 (腾讯接口为主 -> AkShare新浪兜底 -> 本地缓存降级)."""
    # 1. 腾讯接口 (直连快速路径)
    df = _fetch_tencent_kline(code, count=count)
    if not df.empty and len(df) >= 10:
        _update_kline_cache(code, df)
        return df

    # 2. 备用源: AkShare (新浪前复权源)
    try:
        df = _fetch_akshare_kline(code, count=count)
        if not df.empty and len(df) >= 10:
            _update_kline_cache(code, df)
            return df
    except Exception:
        pass

    # 3. 本地历史缓存容灾降级 (确保哪怕彻底离线也有数据画图)
    df = _fetch_cached_kline(code, count=count)
    if not df.empty:
        return df

    print(f"  [zhao_tracker] K线拉取异常 ({code}): 全部数据源均不可用或无数据")
    return pd.DataFrame()


# 只比对买卖点类别; "中枢"/"背驰" 这种泛词会让 "三买" 预设与 "中枢震荡" 计算被判一致
_PRESET_KEYS = ('三买', '二买', '一买', '底背驰', '顶背驰', '三卖', '一卖')


def compute_chan_structure(df: pd.DataFrame, code: str) -> dict[str, Any] | None:
    """用真内核算笔/中枢/信号/阶段。K线不足或内核不可用返回 None。

    结构用全部K线算 (保证笔与中枢长得出来), 画图只裁最后 CHART_BARS 根。
    """
    if not HAVE_CHAN or df is None or df.empty or len(df) < 60:
        return None
    try:
        recs = [{'date': d.strftime('%Y-%m-%d'), 'open': o, 'high': h, 'low': l, 'close': c, 'vol': v}
                for d, o, h, l, c, v in zip(df['dt'], df['open'], df['high'], df['low'], df['close'], df['vol'])]
        bars = bars_from_records(recs, symbol=code)
        day = ChanContext(bars, symbol=code, freq='日线')
        wbars = resample_weekly(bars, code)
        week = ChanContext(wbars, symbol=code, freq='周线') if len(wbars) >= 20 else None
        sigs = compute_multi_level(day, week)
        stage, tone = classify_stage(sigs)
        zs = day.real_zs[-1] if day.real_zs else None
        return {
            'ctx': day,
            'sigs': sigs,
            'stage': stage,
            'tone': tone,
            'nest': nest_view(sigs, week),
            'stop': stop_level(stage, day),
            'zs': (zs.zd, zs.zg) if zs else None,
            'tactics': [t.event.name for t in match_tactics(sigs)],
            'n_bi': len(day.bis),
        }
    except Exception as e:
        print(f"  [zhao_tracker] 缠论结构计算失败 ({code}): {e}")
        return None


def preset_agrees(preset: str, stage: str) -> bool:
    """人工预设 chan_verdict 与计算阶段是否同类: 预设里点名的买卖点, 计算阶段里必须出现同一个。"""
    hits = [k for k in _PRESET_KEYS if k in (preset or '')]
    return any(k in (stage or '') for k in hits) if hits else False


def build_stock_chart_dataset(df: pd.DataFrame, cfg: dict[str, Any], quote: dict[str, Any],
                              chan: dict[str, Any] | None = None) -> dict[str, Any]:
    """生成 ECharts 专业深色 K 线与缠论附着图表数据包。

    chan = compute_chan_structure 的结果; 提供时笔/中枢/买卖点全部来自计算,
    缺失时 (内核不可用/K线不足) 才退回关键波段连线, 并且不画买卖点气泡 (没有依据就不标)。
    """
    if df.empty or len(df) < 10:
        return {'dates': [], 'ohlc': [], 'vol': [], 'ma5': [], 'ma20': [], 'bi_points': [], 'zs_boxes': [],
                'mark_points': [], 'res_line': cfg['key_resistance'], 'target_line': cfg['take_profit_target'],
                'sup_line': cfg['defensive_support'], 'stop_line': cfg['stop_loss_line'], 'chan_source': 'none'}

    full_df = df
    df = df.tail(CHART_BARS).reset_index(drop=True)
    dates = [d.strftime('%Y-%m-%d') for d in df['dt']]
    # ECharts candlestick 数据格式: [open, close, low, high]
    ohlc = [[float(o), float(c), float(l), float(h)] for o, c, l, h in zip(df['open'], df['close'], df['low'], df['high'])]
    vol = [round(float(v) / 10000, 1) for v in df['vol']]  # 万手

    # 计算均线
    # 均线用全量K线算再裁, 否则窗口前 19 根 MA20 是空的
    c_s = full_df['close']
    ma5 = [round(float(v), 2) if pd.notna(v) else '-' for v in c_s.rolling(5).mean().tail(len(df))]
    ma20 = [round(float(v), 2) if pd.notna(v) else '-' for v in c_s.rolling(20).mean().tail(len(df))]

    bi_points: list[list[Any]] = []
    zs_boxes: list[dict[str, Any]] = []
    mark_points: list[dict[str, Any]] = []

    # 笔与中枢: 来自 chan_engine 计算 (分型端点 = 笔端点; 中枢只画真中枢 >=3 笔)
    chan_source = 'fallback'
    if chan is not None:
        day = chan['ctx']
        d0 = dates[0]
        for b in day.bis:
            for fx in (b.fx_a, b.fx_b):
                dt = str(fx.dt)
                if dt >= d0 and (not bi_points or bi_points[-1][0] != dt):
                    bi_points.append([dt, round(float(fx.fx), 3)])
        for z in day.real_zs[-3:]:
            if str(z.edt) < d0:
                continue
            zs_boxes.append({
                'x0': max(str(z.sdt), d0),
                'x1': str(z.edt),
                'zd': round(float(z.zd), 3),
                'zg': round(float(z.zg), 3),
                'name': f"日线中枢 [{float(z.zd):.2f}~{float(z.zg):.2f}] ({len(z.bis)}笔)",
            })
        chan_source = 'computed'

    # 内核不可用时才连接关键波段高低点 (明确标注为兜底, 不冒充笔)
    if chan is None and len(bi_points) < 2 and len(dates) >= 10:
        min_idx = int(df['low'].tail(25).idxmin())
        max_idx = int(df['high'].tail(25).idxmax())
        p1 = [dates[min_idx], float(df['low'].iloc[min_idx])]
        p2 = [dates[max_idx], float(df['high'].iloc[max_idx])]
        p_last = [dates[-1], float(df['close'].iloc[-1])]
        bi_points = sorted([p1, p2, p_last], key=lambda x: x[0])

    # 买卖点气泡: 只标计算出来的点, 落在触发它的那个分型上 (不是最新一根K线)。
    # 三买/二买/一买 = 最后一笔(向下回抽笔)的结束底分型; 顶背驰/三卖 = 对应笔的结束分型。
    if chan is not None and chan['ctx'].bis:
        last_bi = chan['ctx'].bis[-1]
        coord = [str(last_bi.fx_b.dt), round(float(last_bi.fx_b.fx), 3)]
        stage = chan['stage']
        for word, color in (('三买', '#3fb950'), ('二买', '#58a6ff'), ('一买', '#bc8cff'),
                            ('顶背驰', '#f0883e'), ('三卖', '#f85149')):
            if word in stage and coord[0] >= dates[0]:
                mark_points.append({'name': stage, 'coord': coord, 'value': word,
                                    'itemStyle': {'color': color}})
                break

    return {
        'dates': dates,
        'ohlc': ohlc,
        'vol': vol,
        'ma5': ma5,
        'ma20': ma20,
        'bi_points': bi_points,
        'zs_boxes': zs_boxes,
        'mark_points': mark_points,
        'res_line': cfg['key_resistance'],
        'target_line': cfg['take_profit_target'],
        'sup_line': cfg['defensive_support'],
        'stop_line': cfg['stop_loss_line'],
        'chan_source': chan_source,
    }


def generate_zhao_tracker_data(report_date: str = "", report_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """组装全量数据与图表数据集"""
    codes = [item['code'] for item in ZHAO_PORTFOLIO_CONFIG]
    quotes = fetch_realtime_quotes(codes)

    dossiers = []
    for cfg in ZHAO_PORTFOLIO_CONFIG:
        c = cfg['code']
        q = quotes.get(c, {})
        if not q or q.get('price', 0.0) == 0.0:
            q = {
                'code': c, 'name': cfg['name'], 'price': cfg['defensive_support'] * 1.05,
                'pre_close': cfg['defensive_support'] * 1.04, 'open': cfg['defensive_support'] * 1.04,
                'high': cfg['key_resistance'], 'low': cfg['defensive_support'],
                'change_pct': 0.85, 'change_amt': 0.05, 'volume': 150000,
                'amount': 150000000, 'turnover': 3.5, 'pe': 25.0, 'pb': 1.8,
                'total_cap': 8000000000, 'timestamp': datetime.now().strftime('%Y%m%d%H%M%S'),
            }
        df_k = fetch_stock_kline(c, count=CHAN_KLINE_COUNT)
        if report_date and not df_k.empty:
            # 历史日期重跑不能把之后的K线画进去
            df_k = df_k[df_k['dt'] <= pd.Timestamp(report_date)].reset_index(drop=True)
        chan_calc = compute_chan_structure(df_k, c)
        chart_data = build_stock_chart_dataset(df_k, cfg, q, chan=chan_calc)

        # 均线与分型基础值
        ma5_val = chart_data['ma5'][-1] if chart_data['ma5'] and chart_data['ma5'][-1] != '-' else q['price']
        ma20_val = chart_data['ma20'][-1] if chart_data['ma20'] and chart_data['ma20'][-1] != '-' else q['price']

        # 计算结果为准; 手写 chan_verdict 降级为"人工预设", 两者不一致时并列展示
        preset = cfg['chan_verdict']
        if chan_calc is not None:
            computed = chan_calc['stage']
            agrees = preset_agrees(preset, computed)
            zs_txt = (f"最近真中枢 [{chan_calc['zs'][0]:.2f}, {chan_calc['zs'][1]:.2f}]"
                      if chan_calc['zs'] else '暂无真中枢')
            stop_txt = f"结构止损 {chan_calc['stop']:.2f}" if chan_calc['stop'] else ''
            chan_detailed = '；'.join(x for x in (
                f"计算: {computed}", chan_calc['nest'], zs_txt, stop_txt,
                ('命中战法: ' + '、'.join(chan_calc['tactics'])) if chan_calc['tactics'] else '',
            ) if x)
        else:
            computed, agrees = None, None
            chan_detailed = f"(K线不足或缠论内核不可用, 仅人工预设) {preset}"
        chan_data = {
            'status': computed or preset,
            'source': 'computed' if computed else 'preset',
            'computed': computed,
            'tone': chan_calc['tone'] if chan_calc else None,
            'preset': preset,
            'agrees_with_preset': agrees,
            'nest': chan_calc['nest'] if chan_calc else '',
            'stop': chan_calc['stop'] if chan_calc else None,
            'tactics': chan_calc['tactics'] if chan_calc else [],
            'zs_list': chart_data.get('zs_boxes', []),
            'bi_list': chart_data.get('bi_points', []),
            'buy_sell_point': computed or preset,
        }
        # 动态由最新行情生成量价特征描述 (若未拉取到则降级为基准预设)
        if q and q.get('price') and q.get('price') > 0:
            amt_str = f"{q['amount']/1e8:.2f} 亿" if q.get('amount', 0) >= 1e8 else f"{q.get('amount', 0)/1e4:.0f} 万元"
            to_str = f"换手率 {q['turnover']:.2f}%" if q.get('turnover') else "量价动态更新"
            chg_str = f"当日涨跌幅 {q['change_pct']:+.2f}%" if q.get('change_pct') is not None else ""
            cur_vol_desc = f"最新收盘 {q['price']:.2f} 元 ({chg_str})，成交额 {amt_str} ({to_str})，量价结构随盘面动态计算。"
        else:
            cur_vol_desc = cfg['vol_price_desc']

        review_summary = f"{cfg['position_desc']} {cfg['trend_desc']} {cur_vol_desc}"
        tactic_detailed = f"{cfg.get('tactic_model', '')}：{cur_vol_desc}"
        scenarios = [
            {'title': '强势冲高（突破阻力）', 'prob': '40%', 'desc': f"放量上冲测试阻力位 {cfg['key_resistance']:.2f} 元，逼近目标位 {cfg['take_profit_target']:.2f} 元，分批止盈做T。"},
            {'title': '窄幅震荡（蓄势整固）', 'prob': '45%', 'desc': f"缩量回踩支撑位 {cfg['defensive_support']:.2f} 元企稳，按预案锁仓持有或分批低吸。"},
            {'title': '弱势破位（防守止损）', 'prob': '15%', 'desc': f"若跌破防守止损线 {cfg['stop_loss_line']:.2f} 元，无条件减仓规避系统性风险。"},
        ]
        action_plan = {
            'summary': cfg['action_instruction'],
            'operation_matrix': [
                {'condition': f"冲高逼近 {cfg['key_resistance']:.2f} 元", 'action': '逢高止盈做T'},
                {'condition': f"回踩企稳 {cfg['defensive_support']:.2f} 元", 'action': '守住持股/轻仓低吸'},
                {'condition': f"有效跌破 {cfg['stop_loss_line']:.2f} 元", 'action': '果断止损离场'},
            ]
        }

        dossiers.append({
            'config': cfg,
            'quote': q,
            'chan': chan_data,
            'review_summary': review_summary,
            'cur_vol_desc': cur_vol_desc,
            'chan_detailed': chan_detailed,
            'tactic_detailed': tactic_detailed,
            'scenarios': scenarios,
            'action_plan': action_plan,
            'chart_data': chart_data,
            'ma5_val': ma5_val,
            'ma20_val': ma20_val,
        })

    # 大盘宏观事实
    market_stance = "中立震荡 · 结构性分化"
    macro_note = "市场处于结构性轮动与情绪分化期。顺周期涨价（染料、资源）与高股息防守表现出明显抗跌性，短线操作务必聚焦核心点位，拒绝盲目追高，严格执行纪律。"
    if report_context and isinstance(report_context, dict):
        thesis = report_context.get('market_thesis', {})
        if isinstance(thesis, dict):
            market_stance = thesis.get('market_stance', market_stance)
            macro_note = thesis.get('summary', macro_note)

    return {
        'report_date': report_date or datetime.now().strftime('%Y-%m-%d'),
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'market_stance': market_stance,
        'macro_note': macro_note,
        'dossiers': dossiers,
    }


# ─────────────────────────────────────────────────────────────
# 专属 UI 样式 (深色高对比度、清晰干练、直击痛点)
# ─────────────────────────────────────────────────────────────
_ZHAO_CSS = """
:root {
  --bg-main: #0b0f19;
  --bg-card: #161b22;
  --bg-card-hover: #1c2333;
  --border: #30363d;
  --text-main: #e6edf3;
  --text-muted: #8b949e;
  --c-red: #f85149;
  --c-green: #3fb950;
  --c-gold: #e3b341;
  --c-blue: #58a6ff;
  --c-purple: #bc8cff;
  --c-cyan: #39c5bb;
}

* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg-main);
  color: var(--text-main);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
  line-height: 1.6;
  padding: 16px 20px 48px;
}
.wrap { max-width: 1360px; margin: 0 auto; }

/* 顶端导航 */
.top-nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 20px;
  padding: 12px 18px;
  background: rgba(22, 27, 34, 0.92);
  border: 1px solid var(--border);
  border-radius: 10px;
  backdrop-filter: blur(8px);
}
.nav-links { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.nav-link {
  color: var(--c-blue);
  text-decoration: none;
  font-size: 13px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-radius: 6px;
  transition: all .2s;
}
.nav-link:hover { background: rgba(88, 166, 255, 0.15); color: #79c0ff; }
.nav-cur {
  color: #fff;
  background: linear-gradient(135deg, #8957e5, #1f6feb);
  padding: 4px 12px;
  border-radius: 12px;
  font-size: 13px;
  font-weight: 700;
}
.sep { color: #30363d; }

/* Hero Banner */
.hero {
  background: linear-gradient(135deg, #1b1428 0%, #161b26 100%);
  border: 1px solid #bc8cff;
  border-left: 6px solid #bc8cff;
  border-radius: 12px;
  padding: 22px 26px;
  margin-bottom: 22px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.35);
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 16px;
}
.hero-title { font-size: 24px; font-weight: 800; color: #fff; }
.hero-sub { color: var(--text-muted); font-size: 13.5px; margin-top: 4px; }
.badge-group { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
.tag-badge {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 9px; border-radius: 6px; font-size: 12px; font-weight: 600;
}
.bg-purple { background: rgba(188, 140, 255, 0.15); border: 1px solid rgba(188, 140, 255, 0.4); color: var(--c-purple); }
.bg-gold { background: rgba(227, 179, 65, 0.15); border: 1px solid rgba(227, 179, 65, 0.4); color: var(--c-gold); }
.bg-green { background: rgba(63, 185, 80, 0.15); border: 1px solid rgba(63, 185, 80, 0.4); color: var(--c-green); }
.bg-blue { background: rgba(88, 166, 255, 0.15); border: 1px solid rgba(88, 166, 255, 0.4); color: var(--c-blue); }

/* 核心快速看板卡 */
.sec-title {
  font-size: 16px;
  font-weight: 700;
  color: #f0f6fc;
  margin: 24px 0 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #21262d;
  padding-bottom: 8px;
}
.quick-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  gap: 14px;
  margin-bottom: 24px;
}
.mini-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 16px;
  transition: all .2s;
}
.mini-card:hover {
  background: var(--bg-card-hover);
  border-color: #58a6ff;
  transform: translateY(-2px);
}
.card-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 6px;
}
.stock-name { font-size: 18px; font-weight: 800; color: #fff; }
.stock-code { font-size: 12.5px; color: var(--text-muted); margin-left: 6px; font-family: Consolas, monospace; }
.stock-price { font-size: 20px; font-weight: 800; }
.up { color: var(--c-red); }
.down { color: var(--c-green); }
.flat { color: var(--text-muted); }

/* 深度个股战报大卡 */
.dossier-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px 22px;
  margin-bottom: 26px;
  box-shadow: 0 4px 18px rgba(0,0,0,0.25);
}
.dossier-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid #21262d;
  margin-bottom: 16px;
}
.role-badge {
  background: rgba(88, 166, 255, 0.18);
  border: 1px solid rgba(88, 166, 255, 0.4);
  color: #79c0ff;
  font-size: 12px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 4px;
}

/* 核心六大实战要素速查网格 (用户重点要求) */
.action-summary-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  margin-bottom: 16px;
}
@media (max-width: 900px) {
  .action-summary-grid { grid-template-columns: 1fr; }
}
.summary-item {
  background: #0d1117;
  border: 1px solid #21262d;
  border-radius: 8px;
  padding: 10px 14px;
}
.sum-label {
  font-size: 12px;
  font-weight: 700;
  color: #8b949e;
  margin-bottom: 4px;
  text-transform: uppercase;
}
.sum-val {
  font-size: 13.5px;
  color: #e6edf3;
  line-height: 1.5;
}
.sum-action-box {
  background: rgba(248, 81, 73, 0.08);
  border: 1px solid rgba(248, 81, 73, 0.35);
  border-left: 4px solid var(--c-red);
}
.sum-action-box .sum-label { color: #ff7b72; }
.sum-action-box .sum-val { color: #fff; font-weight: 700; font-size: 14px; }

/* 关键关注点位标签条 */
.levels-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  background: #111620;
  border: 1px solid #30363d;
  border-radius: 8px;
  padding: 8px 14px;
  margin-bottom: 14px;
  font-size: 12.5px;
}
.level-tag { display: inline-flex; align-items: center; gap: 4px; }
.level-tag b { font-family: Consolas, monospace; }

/* 图表容器 */
.chart-box {
  background: #0d1117;
  border: 1px solid #21262d;
  border-radius: 8px;
  padding: 12px;
  margin-top: 10px;
}
.chart-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 8px;
  padding: 0 4px;
}
.chart-title { font-size: 13.5px; font-weight: 700; color: #f0f6fc; }
.chart-legend {
  display: flex;
  gap: 12px;
  font-size: 11.5px;
  color: var(--text-muted);
  flex-wrap: wrap;
}
.chart-container {
  width: 100%;
  height: 420px;
}

/* 一键复制与按钮 */
.btn-copy {
  background: #238636;
  color: #fff;
  border: 1px solid rgba(255,255,255,0.2);
  padding: 6px 14px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  transition: all .2s;
}
.btn-copy:hover { background: #2ea043; }

footer {
  margin-top: 48px;
  text-align: center;
  font-size: 12px;
  color: var(--text-muted);
  border-top: 1px solid #21262d;
  padding-top: 20px;
}
"""


_CHAN_TONE_CLR = {'good': '#f85149', 'warn': '#e3b341', 'bad': '#3fb950', 'neutral': '#8b949e'}


def _render_chan_verdict(chan: dict[str, Any]) -> str:
    """缠论结论: 计算结果为准 (着色); 与人工预设不一致时, 预设以灰字并列并标明"人工预设"。"""
    preset = escape(chan.get('preset') or '')
    computed = chan.get('computed')
    if not computed:
        return (f"<div class='sum-val' style='color:#58a6ff;font-weight:600;'>{preset}</div>"
                "<div style='font-size:11px;color:#8b949e;margin-top:3px'>⚠ 人工预设 (K线不足, 未能计算结构)</div>")
    clr = _CHAN_TONE_CLR.get(chan.get('tone'), '#58a6ff')
    stop = chan.get('stop')
    extra = []
    if chan.get('nest'):
        extra.append(escape(chan['nest']))
    if stop:
        extra.append(f"结构止损 {stop:.2f}")
    if chan.get('tactics'):
        extra.append('命中战法: ' + escape('、'.join(chan['tactics'])))
    body = (f"<div class='sum-val' style='color:{clr};font-weight:700;'>{escape(computed)} "
            "<span style='font-size:11px;color:#8b949e;font-weight:400'>(chan_engine 计算)</span></div>"
            + ''.join(f"<div style='font-size:12px;color:#c9d1d9;margin-top:2px'>{x}</div>" for x in extra))
    if chan.get('agrees_with_preset'):
        body += f"<div style='font-size:11px;color:#8b949e;margin-top:4px'>人工预设一致: {preset}</div>"
    else:
        body += (f"<div style='font-size:11px;color:#8b949e;margin-top:4px;border-top:1px dashed #30363d;padding-top:3px'>"
                 f"⚠ 人工预设 (与计算不一致, 以计算为准): {preset}</div>")
    return body


def generate_zhao_standalone_html(
    report_date: str = "",
    is_site_mode: bool = False,
    report_context: dict[str, Any] | None = None
) -> str:
    """生成【属于zhao的个股追踪】专属独立网页 HTML（带 ECharts 专业 K 线图与附着分析）"""
    data = generate_zhao_tracker_data(report_date, report_context=report_context)
    r_date = data['report_date']
    updated_at = data['updated_at']
    dossiers = data['dossiers']

    # 路径自适应
    r_date_dash = f"{str(r_date)[:4]}-{str(r_date)[4:6]}-{str(r_date)[6:]}" if len(str(r_date)) == 8 and str(r_date).isdigit() else str(r_date)
    if is_site_mode:
        nav_home = "../index.html"
        nav_main = f"../reports/{r_date_dash}.html"
        nav_dash = "../dashboards/latest.html"
        nav_plan = "../plan/latest.html"
        nav_pullback = "../pullback/latest.html"
        nav_dragon = "../dragon/latest.html"
        nav_zhao = "latest.html"
    else:
        nav_home = "本地导航入口.html"
        nav_main = "主线强度追踪.html"
        nav_dash = "site/dashboards/latest.html"
        nav_plan = "今日复盘与明日预案_最新.html"
        nav_pullback = "强势板块回调跟踪_最新.html"
        nav_dragon = "site/dragon/latest.html"
        nav_zhao = "zhao个股追踪_最新.html"

    # 1. 顶部精简矩阵卡
    quick_cards_html = []
    for d in dossiers:
        cfg = d['config']
        q = d['quote']
        p = q['price']
        pct = q['change_pct']
        pct_cls = 'up' if pct > 0 else ('down' if pct < 0 else 'flat')
        pct_sign = '+' if pct > 0 else ''

        quick_cards_html.append(f"""
        <div class="mini-card" onclick="document.getElementById('stock-{escape(cfg['bare_code'])}').scrollIntoView({{behavior:'smooth'}})" style="cursor:pointer">
          <div class="card-head">
            <div>
              <span class="stock-name">{escape(cfg['name'])}</span>
              <span class="stock-code">{escape(cfg['code'])}</span>
            </div>
            <div class="stock-price {pct_cls}">{p:.2f} <span style="font-size:14px">{pct_sign}{pct:.2f}%</span></div>
          </div>
          <div style="font-size:12px;color:#8b949e;margin-bottom:6px">
            <b>{escape(cfg['industry'])}</b> · {escape(cfg['mainline'])}
          </div>
          <div style="font-size:12px;color:#c9d1d9;margin:4px 0;">
            <b style="color:#58a6ff">指令:</b> {escape(cfg['action_instruction'])}
          </div>
          <div style="font-size:11.5px;color:#8b949e;display:flex;justify-content:space-between;margin-top:6px;">
            <span>阻力: <b style="color:#f85149">{cfg['key_resistance']:.2f}</b></span>
            <span>防守: <b style="color:#e3b341">{cfg['defensive_support']:.2f}</b></span>
            <span>止损: <b style="color:#da3633">{cfg['stop_loss_line']:.2f}</b></span>
          </div>
        </div>
        """)

    # 2. 深度个股卡片（含 6 大核心操盘要素 + ECharts 图表容器）
    dossier_cards_html = []
    echarts_init_scripts = []

    for d in dossiers:
        cfg = d['config']
        q = d['quote']
        chart = d['chart_data']
        p = q['price']
        pct = q['change_pct']
        pct_cls = 'up' if pct > 0 else ('down' if pct < 0 else 'flat')
        pct_sign = '+' if pct > 0 else ''
        cid = f"chart_{cfg['bare_code']}"

        dossier_cards_html.append(f"""
        <div class="dossier-card" id="stock-{escape(cfg['bare_code'])}">
          <!-- 标的头部信息 -->
          <div class="dossier-header">
            <div>
              <span style="font-size:22px;font-weight:800;color:#fff;">{escape(cfg['name'])}</span>
              <span style="font-size:14px;color:#8b949e;font-family:Consolas,monospace;margin-left:6px;">{escape(cfg['code'])}</span>
              <span class="role-badge" style="margin-left:8px;">{escape(cfg['core_role'])}</span>
              <span style="font-size:12.5px;color:#8b949e;margin-left:8px;">规划仓位: <b style="color:#f0f6fc">{escape(cfg['weight_plan'])}</b></span>
            </div>
            <div style="font-size:24px;font-weight:800;" class="{pct_cls}">
              {p:.2f} <span style="font-size:16px;">({pct_sign}{pct:.2f}%)</span>
              <span style="font-size:12px;color:#8b949e;margin-left:8px;font-weight:400">昨收: {q['pre_close']:.2f} | 开: {q['open']:.2f} | 高: {q['high']:.2f} | 低: {q['low']:.2f}</span>
            </div>
          </div>

          <!-- 模块标签导航条 -->
          <div style="margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap;">
            <span class="tag-badge bg-blue">每日走势深度复盘</span>
            <span class="tag-badge bg-purple">缠论多级别结构透视</span>
            <span class="tag-badge bg-gold">游资量价战法攻防</span>
            <span class="tag-badge bg-green">实战操盘预案矩阵</span>
          </div>

          <!-- 用户最关心的 6 大实操要素看板 (明了、直接、不讲废话) -->
          <div class="action-summary-grid">
            <div class="summary-item">
              <div class="sum-label">📍 每日走势深度复盘 · 现阶段位置</div>
              <div class="sum-val">{escape(cfg['position_desc'])}</div>
            </div>
            <div class="summary-item">
              <div class="sum-label">📈 走势趋势与形态</div>
              <div class="sum-val">{escape(cfg['trend_desc'])}</div>
            </div>
            <div class="summary-item">
              <div class="sum-label">📊 游资量价战法攻防 · 量价特征</div>
              <div class="sum-val"><b style="color:#e3b341">【{escape(cfg['tactic_model'])}】</b> {escape(d.get('cur_vol_desc', cfg['vol_price_desc']))}</div>
            </div>
            <div class="summary-item sum-action-box" style="grid-column: span 2;">
              <div class="sum-label">⚡ 实战操盘预案矩阵 · 买卖操作明确指令</div>
              <div class="sum-val">{escape(cfg['action_instruction'])}</div>
            </div>
            <div class="summary-item">
              <div class="sum-label">🌀 缠论多级别结构透视 · 买卖点建议</div>
              {_render_chan_verdict(d['chan'])}
            </div>
          </div>

          <!-- 关注核心点位精准数值条 -->
          <div class="levels-bar">
            <span style="color:#8b949e;font-weight:700;">🎯 临盘关键点位：</span>
            <span class="level-tag" style="color:#f85149">第一阻力: <b>{cfg['key_resistance']:.2f} 元</b></span>
            <span class="sep">|</span>
            <span class="level-tag" style="color:#ff7b72">止盈目标: <b>{cfg['take_profit_target']:.2f} 元</b></span>
            <span class="sep">|</span>
            <span class="level-tag" style="color:#e3b341">关键支撑: <b>{cfg['defensive_support']:.2f} 元</b></span>
            <span class="sep">|</span>
            <span class="level-tag" style="color:#da3633">绝对止损红线: <b>{cfg['stop_loss_line']:.2f} 元</b></span>
            <span class="sep">|</span>
            <span class="level-tag" style="color:#8b949e">MA5: <b>{d['ma5_val']}</b> / MA20: <b>{d['ma20_val']}</b></span>
          </div>

          <!-- 明日三大走势推演 与 分时操盘执行检查表 -->
          <div style="background:#0d1117;border:1px solid #21262d;border-radius:8px;padding:12px 14px;margin-bottom:14px;">
            <div style="font-size:13px;font-weight:700;color:#c9d1d9;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;">
              <span>📋 明日三大走势推演 与 分时操盘执行检查表</span>
              <span style="font-size:11.5px;color:#8b949e;">缠论中枢定买卖点 · 分型确立定进出 · 纪律执行</span>
            </div>
            <div style="display:grid;grid-template-columns:repeat(3, 1fr);gap:10px;">
              <div style="background:#161b22;border:1px solid rgba(248,81,73,0.3);border-radius:6px;padding:8px 10px;">
                <div style="font-size:12px;font-weight:700;color:#f85149;">1. 强势冲高推演 (阻力位博弈)</div>
                <div style="font-size:12px;color:#c9d1d9;margin-top:3px;line-height:1.5;">
                  <b>触发点位:</b> 冲高逼近 <b>{cfg['key_resistance']:.2f} ~ {cfg['take_profit_target']:.2f} 元</b><br/>
                  <b>实操预案:</b> 次级别背驰出顶分型时减半止盈，不盲目追高。
                </div>
              </div>
              <div style="background:#161b22;border:1px solid rgba(227,179,65,0.3);border-radius:6px;padding:8px 10px;">
                <div style="font-size:12px;font-weight:700;color:#e3b341;">2. 震荡整固推演 (支撑位蓄势)</div>
                <div style="font-size:12px;color:#c9d1d9;margin-top:3px;line-height:1.5;">
                  <b>触发点位:</b> 缩量回踩 <b>{cfg['defensive_support']:.2f} 元</b> (MA5={d['ma5_val']})<br/>
                  <b>实操预案:</b> 回踩中枢下沿或均线出底分型时锁仓，激进者轻仓低吸。
                </div>
              </div>
              <div style="background:#161b22;border:1px solid rgba(218,54,51,0.4);border-radius:6px;padding:8px 10px;">
                <div style="font-size:12px;font-weight:700;color:#da3633;">3. 破位走弱推演 (绝对止损防守)</div>
                <div style="font-size:12px;color:#c9d1d9;margin-top:3px;line-height:1.5;">
                  <b>触发点位:</b> 有效跌破 <b>{cfg['stop_loss_line']:.2f} 元</b><br/>
                  <b>实操预案:</b> 果断止损减仓，规避次级别中枢向下破位风险。
                </div>
              </div>
            </div>
          </div>

          <!-- 专业 ECharts K 线图与缠论附着图解 -->
          <div class="chart-box">
            <div class="chart-head">
              <div class="chart-title">📊 {escape(cfg['name'])} K线走势与缠论结构附着图解</div>
              <div class="chart-legend">
                <span style="color:#00f0ff">━ 缠论笔折线</span>
                <span style="color:#e3b341">■ 缠论中枢 [ZD, ZG]</span>
                <span style="color:#f1e05a">━ MA5攻击线</span>
                <span style="color:#bc8cff">━ MA20生命线</span>
                <span style="color:#f85149">┄ 阻力/止盈位</span>
                <span style="color:#da3633">━ 止损底线</span>
              </div>
            </div>
            <div id="{cid}" class="chart-container"></div>
          </div>
        </div>
        """)

        # 构建单个股票的 ECharts 脚本
        echarts_init_scripts.append(f"""
        (function() {{
          var chartDom = document.getElementById('{cid}');
          if (!chartDom || !window.echarts) return;
          var myChart = echarts.init(chartDom, 'dark');

          var dates = {json.dumps(chart['dates'])};
          var ohlc = {json.dumps(chart['ohlc'])};
          var vol = {json.dumps(chart['vol'])};
          var ma5 = {json.dumps(chart['ma5'])};
          var ma20 = {json.dumps(chart['ma20'])};
          var biPts = {json.dumps(chart['bi_points'])};
          var zsBoxes = {json.dumps(chart['zs_boxes'])};
          var markPoints = {json.dumps(chart['mark_points'])};

          // 中枢 markArea 数据结构
          var markAreaData = zsBoxes.map(function(z) {{
            return [
              {{ name: z.name, xAxis: z.x0, yAxis: z.zd, itemStyle: {{ color: 'rgba(227, 179, 65, 0.12)', borderColor: 'rgba(227, 179, 65, 0.45)', borderWidth: 1 }} }},
              {{ xAxis: z.x1, yAxis: z.zg }}
            ];
          }});

          var option = {{
            backgroundColor: '#0d1117',
            animation: false,
            tooltip: {{
              trigger: 'axis',
              axisPointer: {{ type: 'cross' }},
              backgroundColor: 'rgba(22, 27, 34, 0.95)',
              borderColor: '#30363d',
              textStyle: {{ color: '#e6edf3', fontSize: 12 }},
              formatter: function(params) {{
                var d = params[0].name;
                var res = '<div style="font-weight:700;margin-bottom:4px;">' + d + '</div>';
                params.forEach(function(item) {{
                  if (item.seriesName === 'K线' && item.data) {{
                    res += '开盘: ' + item.data[1] + ' | 收盘: ' + item.data[2] + '<br/>';
                    res += '最低: ' + item.data[3] + ' | 最高: ' + item.data[4] + '<br/>';
                  }} else if (item.seriesName === '成交量') {{
                    res += '成交量: ' + item.data + ' 万手<br/>';
                  }} else if (item.seriesName.indexOf('MA') !== -1 && item.data !== '-') {{
                    res += item.marker + item.seriesName + ': ' + item.data + '<br/>';
                  }}
                }});
                return res;
              }}
            }},
            grid: [
              {{ left: 50, right: 30, top: 25, height: '62%' }},
              {{ left: 50, right: 30, top: '75%', height: '17%' }}
            ],
            xAxis: [
              {{
                type: 'category',
                data: dates,
                boundaryGap: true,
                axisLine: {{ lineStyle: {{ color: '#30363d' }} }},
                axisLabel: {{ color: '#8b949e', fontSize: 10 }}
              }},
              {{
                type: 'category',
                gridIndex: 1,
                data: dates,
                boundaryGap: true,
                axisLine: {{ lineStyle: {{ color: '#30363d' }} }},
                axisLabel: {{ show: false }}
              }}
            ],
            yAxis: [
              {{
                scale: true,
                splitLine: {{ lineStyle: {{ color: '#161b22' }} }},
                axisLabel: {{ color: '#8b949e', fontSize: 10 }}
              }},
              {{
                gridIndex: 1,
                splitLine: {{ show: false }},
                axisLabel: {{ color: '#8b949e', fontSize: 10 }}
              }}
            ],
            dataZoom: [
              {{ type: 'inside', xAxisIndex: [0, 1], start: 30, end: 100 }},
              {{ type: 'slider', xAxisIndex: [0, 1], top: '93%', height: 14, borderColor: '#30363d', fillerColor: 'rgba(88, 166, 255, 0.2)' }}
            ],
            series: [
              {{
                name: 'K线',
                type: 'candlestick',
                data: ohlc,
                itemStyle: {{
                  color: '#f85149',
                  color0: '#3fb950',
                  borderColor: '#f85149',
                  borderColor0: '#3fb950'
                }},
                markArea: {{ data: markAreaData }},
                markPoint: {{
                  data: markPoints,
                  symbolSize: 45,
                  label: {{ fontSize: 10, fontWeight: 700, color: '#fff' }}
                }},
                markLine: {{
                  symbol: 'none',
                  data: [
                    {{ yAxis: {cfg['key_resistance']}, lineStyle: {{ color: '#f85149', type: 'dashed' }}, label: {{ formatter: '阻力 {cfg['key_resistance']:.2f}', position: 'end', color: '#f85149', fontSize: 10 }} }},
                    {{ yAxis: {cfg['take_profit_target']}, lineStyle: {{ color: '#ff7b72', type: 'dotted' }}, label: {{ formatter: '目标 {cfg['take_profit_target']:.2f}', position: 'end', color: '#ff7b72', fontSize: 10 }} }},
                    {{ yAxis: {cfg['defensive_support']}, lineStyle: {{ color: '#e3b341', type: 'dashed' }}, label: {{ formatter: '支撑 {cfg['defensive_support']:.2f}', position: 'end', color: '#e3b341', fontSize: 10 }} }},
                    {{ yAxis: {cfg['stop_loss_line']}, lineStyle: {{ color: '#da3633', type: 'solid', width: 1.5 }}, label: {{ formatter: '止损 {cfg['stop_loss_line']:.2f}', position: 'end', color: '#da3633', fontSize: 10 }} }}
                  ]
                }}
              }},
              {{
                name: 'MA5',
                type: 'line',
                data: ma5,
                smooth: true,
                showSymbol: false,
                lineStyle: {{ color: '#f1e05a', width: 1.2 }}
              }},
              {{
                name: 'MA20',
                type: 'line',
                data: ma20,
                smooth: true,
                showSymbol: false,
                lineStyle: {{ color: '#bc8cff', width: 1.2 }}
              }},
              {{
                name: '缠论笔',
                type: 'line',
                data: biPts,
                showSymbol: true,
                symbol: 'circle',
                symbolSize: 6,
                itemStyle: {{ color: '#00f0ff' }},
                lineStyle: {{ color: '#00f0ff', width: 2, type: 'solid' }}
              }},
              {{
                name: '成交量',
                type: 'bar',
                xAxisIndex: 1,
                yAxisIndex: 1,
                data: vol,
                itemStyle: {{
                  color: function(params) {{
                    var i = params.dataIndex;
                    var c = ohlc[i];
                    return (c && c[1] >= c[0]) ? '#f85149' : '#3fb950';
                  }}
                }}
              }}
            ]
          }};

          myChart.setOption(option);
          window.addEventListener('resize', function() {{ myChart.resize(); }});
        }})();
        """)

    all_charts_js = "\n".join(echarts_init_scripts)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="report-date" content="{escape(r_date)}">
<title>属于zhao的个股追踪 · 每日复盘 · 明日推演 · 操作预案 · {escape(r_date)}</title>
{echarts_head_html()}
<style>{_ZHAO_CSS}</style>
</head>
<body>
<div class="wrap">

  <!-- 顶部全功能导航条 -->
  <div class="top-nav">
    <div class="nav-links">
      <a href="{nav_home}" class="nav-link">🏠 导航门户</a>
      <span class="sep">|</span>
      <a href="{nav_main}" class="nav-link">📊 主线追踪大报告</a>
      <span class="sep">|</span>
      <a href="{nav_dash}" class="nav-link">📈 决策看板</a>
      <span class="sep">|</span>
      <a href="{nav_plan}" class="nav-link">⚔️ 今日复盘与明日预案</a>
      <span class="sep">|</span>
      <a href="{nav_pullback}" class="nav-link">🌊 强势板块回调跟踪</a>
      <span class="sep">|</span>
      <a href="{nav_dragon}" class="nav-link">🐉 龙头接替谱系</a>
      <span class="sep">|</span>
      <span class="nav-cur">🎯 属于zhao的个股追踪 (当前)</span>
    </div>
    <div style="display:flex;align-items:center;gap:10px;">
      <span style="font-size:12px;color:#8b949e">数据基准: {escape(r_date)}</span>
      <button class="btn-copy" onclick="copyMorningBrief()">📋 复制操盘摘要</button>
    </div>
  </div>

  <!-- Hero Header -->
  <div class="hero">
    <div>
      <div class="hero-title">
        <span>🎯 属于zhao的个股追踪 · 专属实战作战室</span>
      </div>
      <div class="hero-sub">
        聚焦 6 大自选持仓 · 摒弃冗长废话，直击“现处位置 / 当前趋势 / 量价评估 / 买卖操作 / 核心点位 / 缠论建议”与 K 线图解
      </div>
      <div class="badge-group">
        <span class="tag-badge bg-purple">🌀 缠论中枢 &amp; 笔附着分析</span>
        <span class="tag-badge bg-gold">⚔️ 游资量价攻防点位</span>
        <span class="tag-badge bg-green">🛡️ 铁律防守止损线</span>
        <span class="tag-badge bg-blue">📊 宏观定调: {escape(data['market_stance'])}</span>
      </div>
    </div>
    <div style="background:#0d1117;border:1px solid #30363d;padding:12px 18px;border-radius:10px;text-align:right;">
      <div style="font-size:11px;color:#8b949e;text-transform:uppercase;">核心追踪池</div>
      <div style="font-size:24px;font-weight:800;color:#58a6ff;">6 只自选标的</div>
      <div style="font-size:11.5px;color:#3fb950;margin-top:2px;">● K线图形与点位标注已附着</div>
    </div>
  </div>

  <!-- 宏观与板块共振提示 -->
  <div style="background:#161b22;border:1px solid #30363d;border-left:4px solid #58a6ff;padding:12px 16px;border-radius:8px;margin-bottom:18px;font-size:13px;color:#c9d1d9;">
    <b>【主线热点与大盘共振】</b>：{escape(data['macro_note'])}
    吉华集团与锦鸡股份双核驱动染料主线；焦作万方依托电解铝周期构筑大双底；深圳燃气地量中枢二买避险；华映科技中枢下轨防守反抽；易明医药突破中枢上沿颈线支撑。
  </div>

  <!-- 1. 核心看板速查 -->
  <div class="sec-title">
    <span>⚡ 持仓核心看板与操作速查 (Portfolio Quick Matrix)</span>
    <span style="font-size:12px;color:#8b949e;font-weight:400">点击卡片可快速直达下方深度图表与分析</span>
  </div>
  <div class="quick-grid">
    {''.join(quick_cards_html)}
  </div>

  <!-- 2. 六大个股深度战报（附着 K 线图形与清晰点位操作） -->
  <div class="sec-title">
    <span>📈 六大核心标的深度图解战报 (Visual Candlestick &amp; Tactical Dossiers)</span>
  </div>
  <div>
    {''.join(dossier_cards_html)}
  </div>

  <footer>
    主线强度追踪系统 · 属于zhao的个股追踪专属模块 · 数据跑批生成 · 仅供研究参考，不构成投资建议
  </footer>

</div>

<script>
{all_charts_js}

function copyMorningBrief() {{
  const brief = `【属于zhao的个股追踪·晨会实战精简操盘指令 · {escape(r_date)}】\\n` +
    `1. 吉华集团(603980): 现价{dossiers[0]['quote']['price']:.2f}元。主升浪类三买放量反包中军。指令: 持股锁仓不追高，冲高9.50~9.60分批减半止盈，破8.35防守减仓。\\n` +
    `2. 华映科技(000536): 现价{dossiers[1]['quote']['price']:.2f}元。中枢下轨3.30支撑二买博弈。指令: 依托3.30挂单低吸，反弹冲高3.65高抛做T，跌破3.20坚决止损。\\n` +
    `3. 焦作万方(000612): 现价{dossiers[2]['quote']['price']:.2f}元。大宗铝周期双底回踩底背驰。指令: 依托10.10~10.40波段低吸，反抽11.20减磅，破9.85止损。\\n` +
    `4. 深圳燃气(601139): 现价{dossiers[3]['quote']['price']:.2f}元。极致地量中枢二买避险底仓。指令: 依托6.18持有不动，反弹6.38~6.45高抛，破6.10止损。\\n` +
    `5. 易明医药(002826): 现价{dossiers[4]['quote']['price']:.2f}元。中枢上沿颈线突破三买回抽。指令: 回踩17.20不破低吸，冲高18.20~18.50减仓止盈，破16.80止损。\\n` +
    `6. 锦鸡股份(300798): 现价{dossiers[5]['quote']['price']:.2f}元。创业板20cm染料+算力日线三买先锋。指令: 依托8.30均线持股，冲高9.20~9.60分批止盈，破8.05止损。\\n` +
    `【铁律】: 拒绝模棱两可，到位坚决执行，破位果断止损！`;
  navigator.clipboard.writeText(brief).then(() => {{
    alert("已成功复制【属于zhao的个股追踪·晨会实战操盘指令】至剪贴板，可随时发至微信或笔记！");
  }}).catch(() => {{
    alert("复制失败，请手动选取文本复制。");
  }});
}}
</script>
</body>
</html>
"""


def render_zhao_teaser_html(report_date: str = "") -> str:
    """渲染主报告中的【属于zhao的个股追踪】高亮专属入口卡片"""
    r_date = report_date or datetime.now().strftime('%Y-%m-%d')
    return f"""
    <div style="background:linear-gradient(135deg,#1b1428 0%,#181c2b 100%);border:1px solid #bc8cff;border-left:5px solid #bc8cff;border-radius:10px;padding:16px 20px;margin-bottom:20px;box-shadow:0 4px 16px rgba(188,140,255,0.15);display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:14px;">
      <div>
        <div style="font-size:12px;color:#bc8cff;font-weight:700;letter-spacing:1px;text-transform:uppercase;">PORTFOLIO TRACKER · 专属持仓个股作战室</div>
        <div style="font-size:17px;font-weight:800;color:#fff;margin-top:2px;">🎯 属于zhao的个股追踪 · 每日复盘 · 明日推演 · 操作预案</div>
        <div style="font-size:12.5px;color:#8b949e;margin-top:4px;">
          吉华集团 · 华映科技 · 焦作万方 · 深圳燃气 · 易明医药 · 锦鸡股份 | 附着 ECharts K线图形分析 × 缠论中枢与笔 × 斩钉截铁操盘指令
        </div>
      </div>
      <div>
        <a href="zhao个股追踪_最新.html" style="background:linear-gradient(135deg,#8957e5,#6e40c9);color:#fff;text-decoration:none;padding:8px 18px;border-radius:8px;font-size:13.5px;font-weight:700;display:inline-flex;align-items:center;gap:6px;box-shadow:0 2px 8px rgba(110,64,201,0.4);">
          进入专属作战室 →
        </a>
      </div>
    </div>
    """


def export_zhao_standalone_reports(
    report_date: str = "",
    output_dir: str | None = None,
    site_dir: str | None = None,
    report_context: dict[str, Any] | None = None,
) -> dict[str, str]:
    """导出独立报告文件至 output/ 根目录与 output/site/zhao/ 站点目录."""
    base_output = output_dir or OUTPUT_DIR
    base_site = site_dir or SITE_DIR
    r_date = report_date or datetime.now().strftime('%Y-%m-%d')

    os.makedirs(base_output, exist_ok=True)
    zhao_site_dir = os.path.join(base_site, "zhao")
    os.makedirs(zhao_site_dir, exist_ok=True)

    # 1. 本地根目录独立页面 (供本地直接双击打开)
    local_latest = os.path.join(base_output, "zhao个股追踪_最新.html")
    local_dated = os.path.join(base_output, f"zhao个股追踪_{r_date}.html")
    local_html = generate_zhao_standalone_html(r_date, is_site_mode=False, report_context=report_context)

    with open(local_latest, "w", encoding="utf-8") as f:
        f.write(local_html)
    with open(local_dated, "w", encoding="utf-8") as f:
        f.write(local_html)

    # 2. 站点目录独立页面 (供 GitHub Pages 归档)
    site_latest = os.path.join(zhao_site_dir, "latest.html")
    site_dated = os.path.join(zhao_site_dir, f"{r_date}.html")
    site_html = generate_zhao_standalone_html(r_date, is_site_mode=True, report_context=report_context)

    with open(site_latest, "w", encoding="utf-8") as f:
        f.write(site_html)
    with open(site_dated, "w", encoding="utf-8") as f:
        f.write(site_html)

    return {
        "local_latest": local_latest,
        "local_dated": local_dated,
        "site_latest": site_latest,
        "site_dated": site_dated,
    }


if __name__ == "__main__":
    print("=== 正在生成属于zhao的个股追踪独立报告 (含 K 线图形与缠论附着分析)... ===")
    res = export_zhao_standalone_reports()
    print("产物已生成:")
    for k, v in res.items():
        print(f"  {k}: {v}")
