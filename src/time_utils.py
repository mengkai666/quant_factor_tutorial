import os
import pandas as pd
from datetime import datetime

_cached_latest_date = None

def get_latest_date():
    """Returns the latest date found in the local cache to replace datetime.now() when running in simulation/historical mode."""
    global _cached_latest_date
    if _cached_latest_date is not None:
        return _cached_latest_date
        
    # src/ 的上一级是仓库根, 缓存统一在 data/ 目录
    _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache_file = os.path.join(_base, 'data', '涨停历史缓存.csv')
    try:
        if os.path.exists(cache_file):
            # 优化：只读取必要的列或只读取一部分数据
            df = pd.read_csv(cache_file, dtype=str, usecols=['日期'])
            if '日期' in df.columns and not df.empty:
                max_date_str = df['日期'].max()
                if max_date_str:
                    max_date_str = max_date_str.replace('-', '')
                    _cached_latest_date = datetime.strptime(max_date_str, '%Y%m%d')
                    return _cached_latest_date
    except Exception as e:
        print(f"Error reading cache date: {e}")
        
    _cached_latest_date = datetime.now()
    return _cached_latest_date
