import pandas as pd
import numpy as np

def generate_timing_signal(sentiment_df, advance_decline):
    """
    根据盘面情绪和高度数据，生成量化择时信号
    """
    signal = {
        'action': '震荡应对 / 结构性博弈',
        'level': '中性',
        'color': '#d29922',
        'desc': '市场处于分歧震荡期，控制仓位，聚焦核心主线。',
        'position': '5成仓位'
    }
    
    if sentiment_df is None or sentiment_df.empty or not advance_decline:
        return signal
        
    try:
        # 1. 提取近期情绪
        recent_moods = sentiment_df['ad_mood'].dropna().tail(2).values
        curr_mood = recent_moods[-1] if len(recent_moods) > 0 else 50
        prev_mood = recent_moods[0] if len(recent_moods) > 1 else curr_mood
        
        # 2. 提取连板高度
        curr_height = advance_decline.get('zt_max_height', 0)
        prev_height = advance_decline.get('zt_max_height_prev', 0)
        
        # 将非数字转为0
        if isinstance(curr_height, str) and not curr_height.isdigit(): curr_height = 0
        if isinstance(prev_height, str) and not prev_height.isdigit(): prev_height = 0
        curr_height = int(curr_height)
        prev_height = int(prev_height)

        # === 核心择时逻辑模型 ===
        
        # 场景一：冰点抄底 (情绪极低)
        if curr_mood < 20 and prev_mood < 30:
            signal['action'] = '右侧确认 / 满仓试错'
            signal['level'] = '进攻 (冰点反转)'
            signal['color'] = '#f85149'  # 红色进攻
            signal['desc'] = f'情绪指数极度冰点({curr_mood:.1f}%)，杀跌动能枯竭。若盘中出现新主线异动，可果断试错核心中军或低位首板。'
            signal['position'] = '7-10成仓位'
            
        # 场景二：退潮防守 (高度下降 + 情绪变差)
        elif curr_height < prev_height and curr_mood < 40 and prev_mood < 40:
            signal['action'] = '严格防守 / 降低仓位'
            signal['level'] = '极度危险 (退潮期)'
            signal['color'] = '#58a6ff'  # 蓝色防守
            signal['desc'] = f'最高空间板断板下降 (昨日{prev_height}板 → 今日{curr_height}板)，且情绪连续低迷({curr_mood:.1f}%)。亏钱效应扩散，系统强制减仓！'
            signal['position'] = '1-3成仓位'

        # 场景三：主升加速 (情绪高涨 + 高度拓展)
        elif curr_mood > 70 and curr_height >= 4:
            signal['action'] = '锁仓主升 / 去弱留强'
            signal['level'] = '强进攻 (高潮期)'
            signal['color'] = '#ff4444'
            signal['desc'] = f'情绪高涨({curr_mood:.1f}%)，空间板打开向上空间。切忌后排追高，底仓死拿核心领涨，让利润奔跑。'
            signal['position'] = '8-10成仓位'
            
        # 场景四：高位滞涨退潮预警
        elif curr_mood > 80 and curr_height < prev_height:
            signal['action'] = '警惕兑现 / 止盈减仓'
            signal['level'] = '防守预警 (高位分歧)'
            signal['color'] = '#ff8800' # 橙色预警
            signal['desc'] = f'盘面极度亢奋({curr_mood:.1f}%)，但绝对空间板受压断板。提防获利盘兑现引发的强分歧，逐步兑现后排跟风。'
            signal['position'] = '4-6成仓位'
            
    except Exception as e:
        print(f"择时模块异常: {e}")
        
    return signal
