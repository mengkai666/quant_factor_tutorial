import pandas as pd
import os
from datetime import datetime

def generate_focus_pool(ml_strength, echelon, top30_data, sentiment_df, output_path="focus_pool.csv"):
    """
    自动化生成“明日核心股票池”与“操作预案”
    """
    pool = []
    
    # 获取近期情绪来决定策略倾向
    curr_mood = 50
    if sentiment_df is not None and not sentiment_df.empty:
        try:
            curr_mood = sentiment_df['ad_mood'].dropna().values[-1]
        except (KeyError, IndexError): pass
        
    # 获取当前最强主线
    core_ml = "未知"
    if ml_strength is not None and not ml_strength.empty:
        try:
            sorted_ml = sorted(ml_strength.iloc[-1].to_dict().items(), key=lambda x: x[1], reverse=True)
            if sorted_ml:
                core_ml = sorted_ml[0][0]
        except (KeyError, IndexError): pass

    # === 策略一：主升接力池 (寻找当前主线的首板或2连板) ===
    if echelon:
        for e in echelon:
            height = str(e.get('height', ''))
            stocks = e.get('stocks', [])
            stock_details = e.get('stock_details', [])
            primary = str(e.get('primary', ''))
            secondary = str(e.get('secondary', ''))
            # name -> code 映射, 用于催化归因反查 (stock_details 里带 code)
            name_to_code = {d.get('name', ''): d.get('code', '') for d in stock_details}

            # 放宽条件：只要是首板、2连板、3连板都可以入选
            if '板' in height:
                # 尽量找核心主线，如果没有匹配上，也把最高板加进去
                is_core = (core_ml in primary or core_ml in secondary or core_ml == "未知")
                if is_core or '3连板' in height or '4连板' in height or '5连板' in height:
                    for s in stocks[:2]:  # 每个高度最多取2只
                        pool.append({
                            '股票': s,
                            '代码': name_to_code.get(s, ''),
                            '板块': primary.split(',')[0] if primary else core_ml,
                            '策略池': '【主升接力池】' if is_core else '【空间博弈池】',
                            '入场条件': f'昨日{height}。若开盘放量换手且承接极强，可跟随打板；切忌加速缩量秒板。',
                            '防守位': '昨日收盘价破位止损'
                        })

    # === 策略二：冰点低吸池 (寻找大容量中军回踩) ===
    # 只要有 top30_data 就选出前两大板块的中军，无视情绪绝对值
    if top30_data:
        ml_keys = list(top30_data.keys())[:2] # 取前两大主线
        for ml in ml_keys:
            records = top30_data[ml]
            if not records: continue
            for r in records[:2]:
                s = r.get('name', '')
                pool.append({
                    '代码': str(r.get('code', '')),
                    '股票': s,
                    '板块': ml,
                    '策略池': '【核心中军低吸池】',
                    '入场条件': f'近期{ml}核心中军。若随大盘情绪杀跌至核心均线(10日/20日)且缩量抗跌，可左侧分批建仓。',
                    '防守位': '有效跌破20日均线无条件斩仓'
                })
                
    # 生成 DataFrame 并去重
    df = pd.DataFrame(pool)
    if not df.empty:
        df = df.drop_duplicates(subset=['股票'])
        if len(df) > 10:
            df = df.head(10)
        try:
            df.to_csv(output_path, index=False, encoding='utf-8-sig')
            print(f"  ✅ [量化引擎] 成功生成明日核心股票池: {output_path} (共 {len(df)} 只标的)")
        except Exception as e:
            print(f"  [量化引擎] 写入股票池失败: {e}")
    else:
        print(f"  [量化引擎] 今日未筛选出符合条件的个股，股票池为空。")
        
    return df
