"""Generate abnormal-move research. --offline replays captured evidence.

Production prices/limit caches are read-only. No scheduling, publishing or orders.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from abnormal_study_v2 import clean_json, prepare_prices, build_events_and_landmarks, group_stats, make_current, build_crossing_events
from abnormal_target_cases import build_target_cases
from data_sources.kaipanla_client import KaiPanLaClient

TZ = timezone(timedelta(hours=8))
OUT = ROOT / "output" / "abnormal_lianban_study"
INDEX_CODES = ["sh000002", "sz399107", "sz399102"]


def save_json(path, payload):
    path.write_text(json.dumps(clean_json(payload), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def fetch_indices(start, end):
    def one(code):
        url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
        session = requests.Session()
        session.trust_env = False
        r = session.get(url, params={'symbol':code,'scale':240,'ma':'no','datalen':500}, timeout=20)
        r.raise_for_status()
        rows = r.json()
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"{code}指数数据为空")
        return [{"date": x['day'][:10], "index_code": code, "index_close": float(x['close']), 'source':'sina_index_daily'} for x in rows if str(x['day'])[:10] <= end]
    with ThreadPoolExecutor(max_workers=3) as executor:
        return [r for result in executor.map(one, INDEX_CODES) for r in result]


def evidence_snapshot(out, offline, start, cutoff):
    ip, ap = out / "index_snapshot.json", out / "alert_snapshot.json"
    errors = []
    if not offline:
        try:
            fetched = fetch_indices(start, cutoff)
            if any(max((x['date'] for x in fetched if x['index_code'] == code), default='') != cutoff for code in INDEX_CODES):
                raise ValueError('指数返回未覆盖目标日，保留原快照')
            save_json(ip, {"fetched_at": datetime.now(TZ).isoformat(), "source": "新浪指数日线", "rows": fetched})
        except (ValueError, requests.RequestException) as exc:
            errors.append(f"指数刷新失败：{exc}; 仅在已有快照足够时回放")
        alert = KaiPanLaClient(timeout=15).get_abnormal_alerts()
        if alert.get('available'):
            alert['fetched_at'] = datetime.now(TZ).isoformat()
            save_json(ap, alert)
        else:
            errors.append("开盘啦提醒刷新失败，保留原快照日期")
    if not ip.exists():
        raise RuntimeError("缺少指数快照，请先运行一次在线生成")
    ix = pd.DataFrame(json.loads(ip.read_text(encoding='utf-8'))['rows'])
    ix['date'] = pd.to_datetime(ix.date)
    ix = ix[ix.date.le(pd.Timestamp(cutoff))].drop_duplicates(['date', 'index_code'])
    if set(ix.index_code) != set(INDEX_CODES) or any(str(x)[:10] != cutoff for x in ix.groupby('index_code').date.max()):
        raise RuntimeError(f"指数快照未完整覆盖截至{cutoff}的三个指数")
    alert = json.loads(ap.read_text(encoding='utf-8')) if ap.exists() else {'date': None, 'alerts': []}
    return ix, alert, errors


def chart(out, summary):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    fig, ax = plt.subplots(figsize=(10, 4.4), dpi=150)
    names = [f"{r['board']} · {r['kind']}\n完整10日 n={r['n10']}" for r in summary]
    vals = [r['median10'] if r['median10'] is not None else 0 for r in summary]
    ax.barh(names, vals, color=['#315F87' if v >= 0 else '#BC4754' for v in vals])
    for i, r in enumerate(summary):
        ax.text(vals[i], i, ' —' if r['median10'] is None else f" {vals[i]:+.1f}%", va='center', fontsize=10)
    ax.axvline(0, color='#738399', lw=.8)
    ax.set_title('价格重建筛查：从预警进入日计算的后10交易日收益中位数')
    ax.set_xlabel('收益率（%）；仅描述历史路径，不是当前预测')
    ax.grid(axis='x', alpha=.15)
    fig.tight_layout()
    fig.savefig(out / 'abnormal_lianban_study.png', bbox_inches='tight')
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true', help='回放报告目录内证据快照，不联网')
    parser.add_argument('--as-of', help='截至已收盘交易日 YYYY-MM-DD，默认本地最新完整截面')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    backup = OUT / 'legacy_v1'
    if not backup.exists() and (OUT / 'abnormal_lianban_study.html').exists():
        backup.mkdir()
        for name in ('abnormal_lianban_study.html', 'abnormal_lianban_study.md', 'metadata.json', 'sideways_observation.csv', 'abnormal_lianban_study.png'):
            if (OUT / name).exists():
                shutil.copy2(OUT / name, backup / name)
    backup.mkdir(exist_ok=True)
    for name in ('entry_state_summary.csv', 'path_pattern_summary.csv', 'reboard_gap_summary.csv',
                 'reboard_sequences.csv', 'generator_v1_reference.py'):
        if (OUT / name).exists() and not (backup / name).exists():
            shutil.move(str(OUT / name), str(backup / name))
    paths = {n: ROOT / 'data' / n for n in ('price_history_cache.csv', '涨停历史缓存.csv', 'security_master.csv', 'trading_calendar_cache.csv')}
    before = {n: (p.stat().st_size, p.stat().st_mtime_ns) for n, p in paths.items()}
    prices = pd.read_csv(paths['price_history_cache.csv'], usecols=['date', 'code', 'close_raw', 'close_qfq'])
    prices['date'] = pd.to_datetime(prices.date)
    now = datetime.now(TZ)
    safe_day = (now - timedelta(days=1)).date() if now.hour < 15 else now.date()
    requested = min(pd.Timestamp(args.as_of or safe_day), pd.Timestamp(safe_day))
    prices = prices[prices.date.le(requested)].copy()
    counts = prices[prices.close_raw.notna()].groupby('date').code.nunique()
    cutoff = str(counts[counts >= .9 * counts.tail(11).median()].index.max())[:10]
    prices = prices[prices.date.le(pd.Timestamp(cutoff))].copy()
    ix, alerts, errors = evidence_snapshot(OUT, args.offline, prices[prices.close_raw.notna()].date.min(), cutoff)
    calendar = pd.to_datetime(pd.read_csv(paths['trading_calendar_cache.csv']).trade_date)
    calendar = calendar[(calendar >= prices[prices.close_raw.notna()].date.min()) & (calendar <= pd.Timestamp(cutoff))].sort_values().tolist()
    master = pd.read_csv(paths['security_master.csv'], dtype={'code': str})
    z = pd.read_csv(paths['涨停历史缓存.csv'], dtype={'日期': str, '代码': str})
    z = z[z['类型'].eq('ZT')].rename(columns={'日期': 'date', '代码': 'code'})
    z['date'] = pd.to_datetime(z.date, format='%Y%m%d')
    p = prepare_prices(prices, master, ix, z, calendar)
    events, landmarks, books = build_events_and_landmarks(p)
    crossings = build_crossing_events(books)
    targets = build_target_cases(ROOT, OUT, cutoff, refresh=not args.offline)
    current, matches = make_current(books, events, landmarks, alerts, cutoff, targets['cases'])
    summary = group_stats(events, ['board', 'kind'])
    sideways = group_stats(landmarks, ['board', 'days'])
    sources = [s for c in targets['cases'] for s in c.get('sources', [])]
    sources.append({'title': '深交所交易规则：累计偏离值公式与10/30日阈值',
                    'url': 'https://docs.static.szse.cn/www/lawrules/rule/trade/W020260424690713155663.pdf'})
    audits = [
        '包含300与301创业板代码；沪市600/601/603/605均纳入。',
        '累计偏离采用个股区间收益减指数同期收益；固定N日需要N+1个收盘价。',
        '同时保留固定10/30日与1…N交易日子窗口最大值；没有完整公告重置历史，因此仅称价格重建筛查。',
        '涨停优先历史池，结合原始收盘与涨停价重建；未知/连续平价不当作横盘。',
        '横盘2/3/5日分组；只按观察日已知特征选样，含随后失败；收益从该观察日重新起算。',
        '当前类比排除本股票、限定同板别/同窗口/同横盘天数，历史后10日必须已结束；每只历史股票最多一票。',
        '不足5只同类历史股票不显示预测分位；不再从后来创新高的赢家中反推当前上涨空间。',
        '未满5/10日后验为缺值；收盘新高参照包含事件日，后10日涨停连续段截于第10日。',
        '主板/创业板是上市板别，不能代替机器人、医药等同题材共振证据。',
    ]
    limits = ['价格重建事件未完整处理历史ST/IPO/复权修订、监管公告重置；不等于交易所正式名单。',
              '收益为收盘路径描述，未验证真实成交、费用、停牌/T+1与涨跌停无法成交约束。',
              '事件样本可能重叠；2/3/5日组分别解读，不合并为独立总体。',
              '报告为生成时快照，未接入自动更新；盘中提醒不会覆盖已完成收盘证据。']
    kpl_time = datetime.fromtimestamp(alerts['time'], TZ).isoformat() if alerts.get('time') else alerts.get('fetched_at')
    payload = {'meta': {'schema_version': 'abnormal-study/v2', 'generated_at': now.isoformat(), 'price_as_of': cutoff,
                       'index_as_of': str(ix.date.max())[:10], 'kpl_as_of': alerts.get('date'), 'kpl_timestamp': kpl_time,
                       'event_count': len(events), 'stock_count': int(p.code.nunique()),
                       'complete_10_count': int(events.return10.notna().sum()),
                       'method': '价格重建筛查；监管公告确认与重点监控另列', 'limitations': limits, 'offline': args.offline},
               'overview': ['凡拓以严重异常公告日10/23为事件锚；莱美以普通异动公告日10/14为事件锚，两者不混为同一种监管状态。',
                            '横盘本身不释放确定涨幅；窗口移出早期大涨后，数值空间可能变化，但再次上涨还需价格与题材承接。',
                            '旧版只挑后来创新高的样本计算“上涨空间”，相关预测已撤销；本版保留失败样本，少于5只合格历史对照时不显示预测。'],
               'current': current, 'events': events.to_dict('records'), 'summary': summary,
               'trigger_summary': group_stats(crossings, ['board', 'kind']), 'crossings': crossings.to_dict('records'),
               'sideways_summary': sideways, 'cases': targets['cases'],
               'rules': [
                   {'name': '接近阈值后整理', 'setup': '10日偏离80—100或30日180—200；连续2/3/5日非涨停且收盘区间≤15%、回撤<10%、在MA5上',
                    'confirm': '收盘突破此前整理上沿，次日回踩守住；量能和同题材强度另核', 'hold': '守住整理上沿/回踩低点，收盘继续保持MA5',
                    'fail': '收盘跌回箱体并跌破下沿，整理突破假设失效；持仓转退出评估', 'missing': '不是横盘固定天数到了就买；不承诺等到监管结束必涨'},
                   {'name': '公告后继续走强', 'setup': '严重异常已由公告确认，随后仍收复高点或连续涨停',
                    'confirm': '用后续收盘创新高/再板确认，关注回踩不破最近关键低点', 'hold': '公告后关键支撑保持，跟踪监控公告变化',
                    'fail': '冲高失败并收盘失守关键支撑，停止加仓观察、评估减仓', 'missing': '主动进入是走势描述；不证明主力意图或监管没有影响'},
                   {'name': '深回撤后反包', 'setup': '先破位后单次涨停；先核是否收复前高',
                    'confirm': '收复旧高后下一日仍能站稳，或形成连续再板；须允许突破失败分支', 'hold': '以已收复的旧高区与回踩低点为跟踪线',
                    'fail': '重新跌回旧高下方且不能收复，降为单板反抽/突破失败', 'missing': '单板反抽与二连板分开；成交量/盘口不足时不发已确认买入信号'}],
               'sources': sources, 'audit': audits + errors + targets.get('warnings', [])}
    payload = clean_json(payload)
    for name, rows in [('abnormal_threshold_events.csv', events), ('sideways_landmarks.csv', landmarks),
                       ('first_crossing_events.csv', crossings),
                       ('current_case_analysis.csv', pd.DataFrame(current)), ('sideways_observation.csv', pd.DataFrame(current)),
                       ('event_summary_by_board.csv', pd.DataFrame(summary)), ('sideways_summary.csv', pd.DataFrame(sideways)),
                       ('analog_matches.csv', pd.DataFrame(matches))]:
        rows.to_csv(OUT / name, index=False, encoding='utf-8-sig')
    ix.to_csv(OUT / 'formal_index_series.csv', index=False, encoding='utf-8-sig')
    pd.DataFrame([{'code': c['code'], 'name': c['name'], **r} for c in targets['cases'] for r in c['rows']]).to_csv(
        OUT / 'target_case_paths.csv', index=False, encoding='utf-8-sig')
    pd.DataFrame([{'source_date': alerts.get('date'), 'source_timestamp': kpl_time, **r} for r in alerts.get('alerts', [])]).to_csv(
        OUT / 'kaipanla_current_alerts.csv', index=False, encoding='utf-8-sig')
    save_json(OUT / 'study_payload.json', payload)
    save_json(OUT / 'metadata.json', payload['meta'])
    save_json(OUT / 'input_manifest.json', {n: {'size': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for n, p in paths.items()})
    from abnormal_report_view import render_report
    render_report(payload, OUT)
    chart(OUT, summary)
    after = {n: (p.stat().st_size, p.stat().st_mtime_ns) for n, p in paths.items()}
    save_json(OUT / 'verification.json', {'production_inputs_unchanged': before == after,
                                        'finite_json': True, 'event_keys_unique': not events.duplicated(['code', 'event_date']).any(),
                                        'landmark_keys_unique': not landmarks.duplicated(['code', 'episode_id', 'days']).any() if len(landmarks) else True})
    print(json.dumps(payload['meta'], ensure_ascii=False))


if __name__ == '__main__':
    main()
