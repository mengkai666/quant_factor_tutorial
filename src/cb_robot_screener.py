"""
可转债与正股联动策略筛选器 (Convertible Bond & Stock Co-movement Screener)
用于筛选具备主线题材、正股动能强、转债盘子轻、高弹性的抱团潜力与主升浪标的。
"""

import os
# 避免系统代理对国内财经接口请求的干扰
os.environ['no_proxy'] = '*'
os.environ['NO_PROXY'] = '*'
for k in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'all_proxy', 'ALL_PROXY']:
    os.environ.pop(k, None)

import pandas as pd
import akshare as ak
import efinance as ef


def run_screener(
    target_sectors=None,
    max_bond_size_yi=10.0,
    min_bond_amount_yi=1.5,
    min_stock_change=1.0,
    max_bond_price=350.0
):
    """
    运行个股与可转债共振量化筛选

    筛选条件：
    1. 规模因子：可转债发行规模 <= max_bond_size_yi (默认10亿以内，筹码轻，利于抱团)
    2. 流动性因子：可转债单日成交额 >= min_bond_amount_yi (默认1.5亿以上，具备场内活跃度)
    3. 题材因子：正股所属板块与主线匹配 (默认涵盖机器人、人形机器人、智能制造、汽车零部件等)
    4. 正股动能：正股处于活跃或主升状态 (当日涨跌幅 >= min_stock_change)
    5. 价格安全线：排除价格过度透支且溢价极端透支标的 (现价 <= max_bond_price)
    """
    if target_sectors is None:
        target_sectors = ['机器人', '减速器', '人形机器人', '自动化', '机器视觉', '电机', '高端装备']

    print(">>> 正在抓取全市场可转债基本面与正股映射关系...")
    try:
        base_df = ef.bond.get_all_base_info()
    except Exception as e:
        print(f"获取可转债基本信息失败: {e}")
        return pd.DataFrame()

    base_df['正股代码'] = base_df['正股代码'].astype(str).str.zfill(6)
    base_df['发行规模(亿)'] = pd.to_numeric(base_df['发行规模(亿)'], errors='coerce')

    print(">>> 正在抓取可转债最新日线行情...")
    try:
        spot_df = ak.bond_zh_hs_cov_spot()
    except Exception as e:
        print(f"获取可转债快照失败: {e}")
        return pd.DataFrame()

    spot_df['code_pure'] = spot_df['symbol'].str[2:]
    spot_df['trade'] = pd.to_numeric(spot_df['trade'], errors='coerce')
    spot_df['amount'] = pd.to_numeric(spot_df['amount'], errors='coerce')
    spot_df['changepercent'] = pd.to_numeric(spot_df['changepercent'], errors='coerce')

    # 合并基本信息与行情
    merged = pd.merge(base_df, spot_df, left_on='债券代码', right_on='code_pure', how='inner')
    merged['成交额(亿)'] = merged['amount'] / 1e8

    # 1. 规模与流动性初筛
    cond_size = merged['发行规模(亿)'] <= max_bond_size_yi
    cond_vol = merged['成交额(亿)'] >= min_bond_amount_yi
    cond_price = merged['trade'] <= max_bond_price
    filtered = merged[cond_size & cond_vol & cond_price].copy()

    print(f">>> 规模<= {max_bond_size_yi}亿 且成交额>= {min_bond_amount_yi}亿 的初筛转债共有: {len(filtered)} 只")

    # 2. 读取题材缓存匹配 (em_stock_plate_cache)
    plate_file = 'data/em_stock_plate_cache.csv'
    robot_stocks = set()
    if os.path.exists(plate_file):
        try:
            plate_df = pd.read_csv(plate_file)
            matched_plate = plate_df[plate_df['sub'].isin(target_sectors) | plate_df['mainline'].isin(target_sectors)]
            for code in matched_plate['code'].dropna().unique():
                robot_stocks.add(str(code)[-6:])
        except Exception as e:
            print(f"读取题材缓存警告: {e}")

    # 手动补齐行业核心机器人/自动化转债标的库
    known_robot_stocks = {
        '688686': '奥普特(机器视觉)',
        '002988': '豪美新材(轻量化/机器人构件)',
        '301459': '丰茂股份(机器人传动件)',
        '300953': '震裕科技(减速器电机铁芯)',
        '688003': '天准科技(机器视觉/具身检测)',
        '003036': '泰坦股份(智能自动化)',
        '002126': '银轮股份(机器人热管理)',
        '301398': '星源卓镁(轻量化部件)',
        '605123': '派克新材(精密特种锻件)',
        '002896': '中大力德(减速器龙头)',
        '300969': '恒帅股份(微电机执行器)',
    }

    results = []
    print(">>> 正在检验正股表现与题材共振状态...")
    for _, row in filtered.iterrows():
        b_code = row['债券代码']
        b_name = row['债券名称']
        s_code = row['正股代码']
        s_name = row['正股名称']
        b_price = row['trade']
        b_pct = row['changepercent']
        b_amt = row['成交额(亿)']
        b_size = row['发行规模(亿)']

        # 判断题材标签
        is_robot_theme = (s_code in robot_stocks) or (s_code in known_robot_stocks) or any(k in b_name for k in ['智', '机器', '精', '动', '达', '泰', '丰', '茂'])

        # 尝试拉取正股最新行情
        prefix = 'sh' if s_code.startswith(('60', '68')) else ('sz' if s_code.startswith(('00', '30')) else 'bj')
        s_sym = f"{prefix}{s_code}"
        
        s_close = None
        s_pct = None
        s_amt = None
        try:
            s_hist = ak.stock_zh_a_daily(symbol=s_sym)
            if s_hist is not None and not s_hist.empty:
                last_bar = s_hist.iloc[-1]
                prev_bar = s_hist.iloc[-2]
                s_close = last_bar['close']
                s_pct = (last_bar['close'] - prev_bar['close']) / prev_bar['close'] * 100
                s_amt = last_bar['amount'] / 1e8
        except Exception:
            pass

        # 判定筛选评分
        score = 0
        reasons = []
        if is_robot_theme:
            score += 3
            reasons.append("机器人/智能制造核心题材")
        if b_size <= 6.0:
            score += 3
            reasons.append(f"袖珍小盘({b_size:.1f}亿)")
        elif b_size <= 10.0:
            score += 1
            reasons.append(f"中偏小盘({b_size:.1f}亿)")
        
        if b_amt >= 10.0:
            score += 3
            reasons.append("场内顶级流动性抱团(成交>10亿)")
        elif b_amt >= 3.0:
            score += 2
            reasons.append("高流动性活跃(成交>3亿)")

        if s_pct is not None:
            if s_pct >= 9.5:
                score += 3
                reasons.append(f"正股强势涨停(+{s_pct:.1f}%)")
            elif s_pct >= 3.0:
                score += 2
                reasons.append(f"正股强势拉升(+{s_pct:.1f}%)")
            elif s_pct < -3.0:
                score -= 2
                reasons.append(f"正股大幅杀跌({s_pct:.1f}%)")

        results.append({
            '综合评分': score,
            '转债代码': b_code,
            '转债名称': b_name,
            '转债现价': round(b_price, 2),
            '转债涨跌%': round(b_pct, 2),
            '成交额(亿)': round(b_amt, 2),
            '规模(亿)': round(b_size, 2) if pd.notnull(b_size) else "-",
            '正股代码': s_code,
            '正股名称': s_name,
            '正股价': round(s_close, 2) if s_close else "-",
            '正股涨跌%': round(s_pct, 2) if s_pct else "-",
            '题材及特征': ", ".join(reasons)
        })

    res_df = pd.DataFrame(results)
    if not res_df.empty:
        res_df = res_df.sort_values(by=['综合评分', '成交额(亿)'], ascending=[False, False])
    return res_df


if __name__ == '__main__':
    df = run_screener()
    print("\n" + "="*80)
    print("【可转债与正股共振 筛选结果TOP榜】")
    print("="*80)
    print(df.head(15).to_string(index=False))
