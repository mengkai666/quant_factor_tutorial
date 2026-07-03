import sys
import pandas as pd
from datetime import datetime
sys.path.append(r"c:\Users\mengk\Desktop\quant_factor_tutorial")
from 主线强度追踪 import load_and_classify_zt

def trace():
    print("Tracing load_and_classify_zt(n_days=90)")
    df = load_and_classify_zt(n_days=90)
    print("Final dates:")
    print(sorted(df['日期'].unique())[-10:])

if __name__ == '__main__':
    trace()
