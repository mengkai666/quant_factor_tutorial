import sys
sys.path.append(r"c:\Users\mengk\Desktop\quant_factor_tutorial")
from lianban_analysis import _fetch_cls_one_day

def test():

    
    for date_str in ['20260324', '20260323', '20260320']:
        print(f"Testing {date_str}...")
        zt_df, dt_df, success = _fetch_cls_one_day(date_str)
        if success and zt_df is not None:
            print(f"Success! {len(zt_df)} items.")
        else:
            print("Failed.")

if __name__ == '__main__':
    test()
