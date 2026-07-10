import os
import pandas as pd
from datetime import datetime

_cached_latest_date = None

def get_latest_date():
    """Returns the latest date found in the local cache to replace datetime.now() when running in simulation/historical mode."""
    global _cached_latest_date
    if _cached_latest_date is not None:
        return _cached_latest_date
        
    # 路径统一由 paths.py 提供 (单一真源)
    from paths import ZT_CACHE_FILE
    cache_file = ZT_CACHE_FILE
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
