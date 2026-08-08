"""共振/领涨板块的板块内个股分类代表。

分类逻辑 (每个题材内):
  龙头      —— 底部至今涨幅前列 且 有涨停记录 (资金认可的进攻先锋)
  中军      —— 市值/成交额靠前, 涨幅中上, 无涨停 (机构配置盘, 波动小)
  超跌反弹  —— 下跌段跌幅前 30% 且 反弹幅度前 30% (弹性来自跌深, 非新逻辑)
  独立新高  —— 下跌段抗跌(跌幅小于全市场中位) 且 反弹段仍领先 (真主线)

数据: output/local_sector_phase.csv (由 local_sector_phase.py 生成) + 涨停历史缓存
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import paths  # noqa: E402

PHASE_CSV = os.path.join(paths.OUTPUT_DIR, 'local_sector_phase.csv')
MKT_FALL_MED = -11.04   # 全市场下跌段中位数 (来自 local_sector_phase 输出)


def load_zt():
    """涨停缓存: 统计 7/17 之后每只股的涨停次数与最高连板。"""
    zt = pd.read_csv(paths.ZT_CACHE_FILE, encoding='utf-8-sig')
    zt.columns = [c.strip() for c in zt.columns]
    zt = zt[(zt['类型'] == 'ZT') & (zt['日期'] >= 20260717)]
    zt['code6'] = zt['代码'].astype(str).str.extract(r'(\d{6})')[0]
    g = zt.groupby('code6').agg(
        涨停次数=('日期', 'count'),
        最高连板=('连板数', 'max'),
        名称=('名称', 'last'),
    ).reset_index()
    return g


def main():
    pd.set_option('display.width', 260)
    df = pd.read_csv(PHASE_CSV)
    df['code6'] = df['code'].str.extract(r'(\d{6})')[0]
    zt = load_zt()
    df = df.merge(zt[['code6', '涨停次数', '最高连板']], on='code6', how='left')
    df['涨停次数'] = df['涨停次数'].fillna(0).astype(int)

    # 用价格缓存最新收盘 * 不可得股本 → 用成交额代理不可行, 这里用收盘价与涨幅排序即可
    print(f'载入 {len(df)} 只, 其中 7/17 后有涨停 {(df.涨停次数 > 0).sum()} 只')

    # 全市场涨停王 (情绪主线的直接证据)
    print('\n=== 7/17 以来 涨停次数最多 TOP 25 (资金主攻方向) ===')
    top = df.nlargest(25, '涨停次数')[
        ['code', 'name', 'sub', 'mainline', 'industry', '下跌段', '震荡段', '突破日', '底部至今', '涨停次数', '最高连板']]
    print(top.to_string(index=False))

    # 各主线 / 概念 的代表
    for col, keys in (
        ('sub', ['AI应用', '算力', '电网', '风电', '有色', '金融科技', '芯片',
                 '传媒', '军工', '储能', '消费电子', '半导体', '医药']),
    ):
        for k in keys:
            sub = df[df[col] == k].copy()
            if len(sub) < 3:
                continue
            print(f'\n{"=" * 70}\n### {k} (n={len(sub)}) '
                  f'下跌段中位 {sub["下跌段"].median():.1f}% | '
                  f'底部至今中位 {sub["底部至今"].median():.1f}%')

            fall_q30 = sub['下跌段'].quantile(0.30)
            rise_q70 = sub['底部至今'].quantile(0.70)

            lead = sub[(sub['涨停次数'] > 0)].nlargest(5, '底部至今')
            if len(lead):
                print('  [龙头/涨停先锋]')
                print(lead[['code', 'name', '下跌段', '震荡段', '突破日', '底部至今',
                            '涨停次数', '最高连板']].to_string(index=False))

            indep = sub[(sub['下跌段'] > MKT_FALL_MED) & (sub['底部至今'] >= rise_q70)]
            indep = indep.nlargest(5, '底部至今')
            if len(indep):
                print('  [独立主线: 抗跌+领涨]')
                print(indep[['code', 'name', '下跌段', '震荡段', '突破日', '底部至今',
                             '涨停次数']].to_string(index=False))

            reb = sub[(sub['下跌段'] <= fall_q30) & (sub['底部至今'] >= rise_q70)]
            reb = reb.nlargest(5, '底部至今')
            if len(reb):
                print('  [超跌反弹: 跌最深+弹最猛]')
                print(reb[['code', 'name', '下跌段', '震荡段', '突破日', '底部至今',
                           '涨停次数']].to_string(index=False))

            dead = sub.nsmallest(3, '底部至今')
            print('  [未修复/掉队]')
            print(dead[['code', 'name', '下跌段', '震荡段', '突破日', '底部至今']].to_string(index=False))

    out = os.path.join(paths.OUTPUT_DIR, 'sector_representatives.csv')
    df.to_csv(out, index=False, encoding='utf-8-sig')
    print(f'\n已存 {out}')


if __name__ == '__main__':
    main()
