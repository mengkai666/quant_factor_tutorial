"""共振结构总结: 阶段量能 + 涨跌家数 + 板块分组 (共振/独立/超跌/未修复)。"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import paths  # noqa: E402

SEG = {
    '下跌段': ('20260623', '20260717'),
    '见底段': ('20260717', '20260721'),
    '震荡段': ('20260722', '20260804'),
    '突破日': ('20260805', '20260805'),
}


def sentiment_by_phase():
    s = pd.read_csv(paths.SENTIMENT_CACHE)
    s['日期'] = s['日期'].astype(str)
    s = s[(s.up > 0) | (s.down > 0)]
    print('=== 各阶段涨跌家数 (A/D) ===')
    for ph, (a, b) in SEG.items():
        seg = s[(s['日期'] >= a) & (s['日期'] <= b)]
        if not len(seg):
            continue
        ad = seg.up / (seg.up + seg.down)
        print(f'  {ph}: 交易日 {len(seg)}  平均上涨占比 {ad.mean() * 100:.1f}%  '
              f'最低 {ad.min() * 100:.1f}%  最高 {ad.max() * 100:.1f}%')
    print('\n  最近 12 日逐日:')
    for _, r in s.tail(12).iterrows():
        tot = r.up + r.down
        print(f"    {r['日期']}  涨 {int(r.up):>4} 跌 {int(r.down):>4}  "
              f"上涨占比 {r.up / tot * 100:>5.1f}%")


def zt_by_phase():
    zt = pd.read_csv(paths.ZT_CACHE_FILE, encoding='utf-8-sig')
    zt.columns = [c.strip() for c in zt.columns]
    zt['日期'] = zt['日期'].astype(str)
    print('\n=== 各阶段涨停/跌停 (情绪强度) ===')
    for ph, (a, b) in SEG.items():
        seg = zt[(zt['日期'] >= a) & (zt['日期'] <= b)]
        if not len(seg):
            continue
        days = seg['日期'].nunique()
        z = (seg['类型'] == 'ZT').sum()
        d = (seg['类型'] == 'DT').sum()
        print(f'  {ph}: {days} 日  涨停 {z} (日均 {z / days:.0f})  '
              f'跌停 {d} (日均 {d / days:.1f})  ZT/DT {z / max(d, 1):.1f}')
    print('\n  最近 10 日逐日涨停家数:')
    r = zt[zt['类型'] == 'ZT'].groupby('日期').size().tail(10)
    d = zt[zt['类型'] == 'DT'].groupby('日期').size()
    for k, v in r.items():
        print(f'    {k}  涨停 {v:>3}  跌停 {d.get(k, 0):>3}')


def sse_volume():
    print('\n=== 上证成交额 (量能) ===')
    import json
    p = os.path.join(paths.OUTPUT_DIR, 'sse_phase_analysis.json')
    if not os.path.exists(p):
        return
    with open(p, encoding='utf-8') as f:
        sse = pd.DataFrame(json.load(f)['sse'])
    sse['d'] = sse.date.str.replace('-', '')
    for ph, (a, b) in SEG.items():
        seg = sse[(sse.d >= a) & (sse.d <= b)]
        if not len(seg):
            continue
        print(f'  {ph}: 日均成交 {seg.volume.mean() / 1e8:.0f} 亿股  '
              f'区间 {seg.volume.min() / 1e8:.0f}~{seg.volume.max() / 1e8:.0f}')


def grouping():
    t = pd.read_csv(os.path.join(paths.OUTPUT_DIR, 'ths_sector_phase.csv'))
    MKT_FALL = -9.6      # 上证下跌段
    print('\n' + '=' * 78)
    print('=== 板块四象限分组 (下跌段抗跌性 × 底部至今强度) ===')
    strong = t['底部至今'].quantile(0.70)
    weak = t['底部至今'].quantile(0.30)

    q1 = t[(t['下跌段'] > MKT_FALL) & (t['底部至今'] >= strong)]
    q2 = t[(t['下跌段'] <= MKT_FALL) & (t['底部至今'] >= strong)]
    q3 = t[(t['下跌段'] > MKT_FALL) & (t['底部至今'] < weak)]
    q4 = t[(t['下跌段'] <= MKT_FALL) & (t['底部至今'] < weak)]

    cols = ['板块', '下跌段', '见底段', '震荡段', '突破日', '底部至今', '量比']
    for q, label in ((q1, '① 独立主线 (抗跌 + 领涨) —— 真正的新方向'),
                     (q2, '② 超跌反弹 (跌深 + 弹猛) —— 弹性来自跌幅'),
                     (q3, '③ 防御退潮 (抗跌 但 涨不动) —— 下跌段的避风港, 现在是死钱'),
                     (q4, '④ 深度受损 (跌深 且 没修复) —— 逻辑被破坏')):
        print(f'\n{label}  n={len(q)}')
        if len(q):
            print(q.sort_values('底部至今', ascending=False)[cols].to_string(index=False))

    # 见底段领涨 (谁先转)
    print('\n' + '=' * 78)
    print('=== 见底段 (7/17→7/21) 领涨 TOP 12 —— 谁先见底/领反弹 ===')
    print(t.nlargest(12, '见底段')[cols].to_string(index=False))


if __name__ == '__main__':
    pd.set_option('display.width', 260)
    sentiment_by_phase()
    zt_by_phase()
    sse_volume()
    grouping()
