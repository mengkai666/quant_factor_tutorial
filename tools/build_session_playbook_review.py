"""Render the 11/2 frozen-plan review from independently collected evidence."""
from collections import defaultdict
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from trading_playbook_view import _document


def build(root=ROOT):
    out = root / 'output/trading_playbooks/review_2026-11-02'
    stock = json.loads((out / 'stock_evidence/stock_review.json').read_text(encoding='utf8'))
    cb = json.loads((out / 'cb_evidence/session_review.json').read_text(encoding='utf8'))
    assert stock['meta']['review_date'] == cb['review_date'] == '2026-11-02'
    assert all(x['cross_source_ohlc_agree'] and x['previous_close_agree'] for x in stock['market_evidence'])
    assert all(x['verified_review_date'] for x in cb['rows'])
    assessments = defaultdict(list)
    for row in stock['plan_comparisons']:
        assessments[row['code']].append(row['assessment'])
    stocks = []
    for row in stock['market_evidence']:
        prices = row['ohlcv']
        stocks.append([row['name'], f"{row['change_pct']:+.2f}%",
                       '/'.join(f'{prices[k]:.2f}' for k in ('open','high','low','close')),
                       '；'.join(assessments[row['code']])])
    bonds = []
    for row in cb['rows']:
        prices = row['bond']['metrics']
        bonds.append([row['name'], row['prior_status'], f"{prices['day_change_pct']:+.2f}%",
                      '/'.join(f'{prices[k]:.3f}' for k in ('open','high','low','close')),
                      row['execution_conclusion']])
    fixes = [
        ['覆盖', '只细核成交额前30，丰茂和震裕未详细展示。', '扩为前30、成交额≥2亿元或涨幅≥1%的并集；原10/30快照细核64只，独立修复研究47只。排序沿用原成交额，不按11/2涨幅挑选。'],
        ['信号状态', '没有分钟证据，却显示已触发0次。', '未核验显示未知；只有核验状态与明确计数同时存在，才显示0或实际次数。'],
        ['取消规则', '跌破失效线取消，与09:45前收回宽限冲突。', '先检查硬失效；触碰失效价即取消且当日不重启。宽限仅在失效线以上有效。旧计划按原文复盘，不追改结果。'],
        ['回避卡片', '凡拓明确回避，却残留数值买区与做多情景。', '回避卡清除买区，修复仅作重新建案条件。带日期案例锚不得自动套用未来日期。'],
        ['报告事实', '固定修复文案、缺行情填15元或10%、归因样本85误写连板数。', '市场文案按真实指标生成；缺行情显示待核验。连板按高度≥2独立计数，11/2为11只；旧预测缓存保留原样。'],
        ['时效与留档', '过期预案仍用下一交易日口吻，同日重跑覆盖原版。', '显著标记计划已结束；原始归档不改，同日重跑另存修订版和证据哈希。'],
    ]
    pending = [
        '仍缺11/2分钟回踩、VWAP、盘中题材同伴及成交记录；未统计策略收益或确认触发次数。',
        '股票三只回踩候选均触及原失效价下方。失效价仅比支撑低一分钱，可能过窄；需要固定规则后做含T+1、跳空和不可成交情况的前向验证。',
        '转债修复池新增规则尚未证明有盈利优势；不得因奥普、丰茂11/2上涨就降低溢价、强赎或新债数据门槛。',
        '本次完成原11/2预案复盘及事后规则修订。三战法修订页仍以10/30为基准，不是11/3的新预案；日常三预案自动生成接线仍需单独完成。',
    ]
    title = '11月2日三战法复盘：结果、失效条件与修订'
    md = '# ' + title + '\n\n'
    intro = '对照10月30日收盘制定的11月2日原始预案。核对7只股票、5只转债及正股；日涨幅只是市场表现，不是预案收益。'
    html = '<nav class="report-nav"><a href="../index.html">修订后三战法</a><a href="../archive/2026-11-02_asof_2026-10-30/index.html">原始冻结预案</a><a href="../../主线强度追踪.html#sec-playbook-review">主报告</a></nav>'
    html += '<header><div class="eyebrow">复盘交易日 2026-11-02 / 原始数据 2026-10-30</div><h1>' + title + '</h1><p>' + intro + '</p></header>'
    lead = '最需要优化的是覆盖范围与取消规则：活跃转债回撤值得独立观察；股票收盘上涨，也可能已经触碰原失效线。当前盘中触发、成交和策略收益均未核验。'
    html += '<section class="decision"><h2>复盘结论</h2><p class="lead">' + lead + '</p></section>'
    md += intro + '\n\n' + lead + '\n\n'
    market_md = out / 'market_review.md'
    if market_md.is_file():
        market = market_md.read_text(encoding='utf8')
        html += '<section class="decision"><h2>11月2日整体盘面：普涨修复，接力仍薄</h2><p>上涨4597家、下跌792家，上涨占比85.3%；涨停47→125，跌停82→8。上证+1.08%，创业板+1.87%。</p><p>125只涨停中114只是首板，占91.2%；昨日涨停47只中11只继续连板，晋级率23.4%。青岛食品6板、5板空缺、澳洋健康4板。AI应用与传媒扩散明显，重点验证下一交易日首板晋级和板块承接。</p><p>结论：普涨修复已发生，新周期主升仍待延续确认。策略资格未验证与市场涨跌是两件事，不能把“不开新仓”解释成市场没有机会。</p><p><a href="market_review.md">查看主日报细核及口径差异</a></p></section>'
        md += '\n## 最新主日报整体复盘\n\n' + market + '\n\n'
    for label, headers, rows in [('股票与异动预案', ['股票','日涨跌','开/高/低/收','原预案对照'],stocks),
                                 ('用户点名的五只转债', ['转债','原覆盖状态','日涨跌','开/高/低/收','可核验结论'],bonds),
                                 ('已落地的优化', ['项目','原问题','修订'],fixes)]:
        html += '<section><h2>' + label + '</h2><div class="table-wrap"><table><thead><tr>' + ''.join('<th>'+h+'</th>' for h in headers) + '</tr></thead><tbody>'
        md += '## '+label+'\n\n|'+'|'.join(headers)+'|\n|'+'|'.join('---' for _ in headers)+'|\n'
        for row in rows:
            html += '<tr>' + ''.join('<td>'+escape(str(cell))+'</td>' for cell in row) + '</tr>'
            md += '|'+'|'.join(str(cell).replace('|','／') for cell in row)+'|\n'
        html += '</tbody></table></div></section>'
        md += '\n'
    html += '<section class="decision"><h2>下一轮优先验证</h2><ul>' + ''.join('<li>'+escape(x)+'</li>' for x in pending) + '</ul></section>'
    md += '## 下一轮优先验证\n\n' + '\n'.join('- '+x for x in pending) + '\n\n'
    evidence_links = [('股票双源证据与逐项复盘','stock_evidence/stock_review.json'), ('转债与正股同日核验','cb_evidence/session_review.json'), ('原始预案与证据哈希','verification.json')]
    html += '<section><h2>证据</h2><p>股票采用新浪日线与腾讯OHLC交叉核对；转债采用新浪日线与新浪同日报价核对，不等于独立双源验证。所有数据均核对为11/2收盘。</p><ul>' + ''.join(f'<li><a href="{url}">{label}</a></li>' for label,url in evidence_links) + '</ul></section>'
    md += '## 证据\n\n股票：新浪日线与腾讯OHLC。转债：新浪日线与同源报价核对，不等于独立双源验证。\n\n' + '\n'.join(f'- [{label}]({url})' for label,url in evidence_links) + '\n'
    (out/'index.html').write_text(_document(title,html),encoding='utf8')
    (out/'复盘与优化.md').write_text(md,encoding='utf8')
    hashes = {}
    for base in [out/'stock_evidence',out/'cb_evidence',out.parent/'archive/2026-11-02_asof_2026-10-30']:
        for path in base.rglob('*'):
            if path.is_file(): hashes[path.relative_to(root).as_posix()] = sha256(path.read_bytes()).hexdigest()
    result = {'review_date':'2026-11-02','stocks_checked':len(stocks),'bond_pairs_checked':len(bonds),
              'triggered_count':None,'strategy_pnl':None,'hashes':hashes}
    (out/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    return result


if __name__ == '__main__':
    print(json.dumps({k:v for k,v in build().items() if k!='hashes'},ensure_ascii=False))
