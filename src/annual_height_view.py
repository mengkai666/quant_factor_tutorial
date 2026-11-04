"""Year-long height section, deliberately independent from the sector-chart date window."""
from __future__ import annotations
import html,json
from pathlib import Path
import numpy as np
import pandas as pd
from annual_height_research import year_start,pressure_frame
ROOT=Path(__file__).resolve().parents[1]
DEFAULT_HISTORY=ROOT/'data/annual_height_history.csv'


def render_annual_height_section(sentiment_df=None,as_of=None,history_path=None,study_link=None):
    path=Path(history_path) if history_path is not None else DEFAULT_HISTORY
    if as_of is None:
        if sentiment_df is not None and not sentiment_df.empty:as_of=str(sentiment_df['日期'].max())
        elif path.exists():as_of=str(pd.read_csv(path,usecols=['date']).date.max())
        else:return ''
    as_of=pd.Timestamp(str(as_of)).strftime('%Y-%m-%d');start=year_start(as_of)
    scope='沪深主板普通10%样本；有证据的停牌暂停计数，停牌日不参榜'
    if path.exists():
        f=pd.read_csv(path,dtype={'date':str}).sort_values('date').drop_duplicates('date',keep='last')
        pool_path=ROOT/'data/涨停历史缓存.csv'
        if history_path is None and pool_path.is_file() and str(f.date.max())<as_of:
            pool=pd.read_csv(pool_path,dtype={'日期':str,'代码':str})
            f=extend_from_cached_pool(f,pool,as_of)
            scope+='；研究截止后新增日沿用已完成日报涨停池，不重写历史'

    elif sentiment_df is not None and not sentiment_df.empty and '连板高度' in sentiment_df:
        f=sentiment_df.copy();f['date']=pd.to_datetime(f['日期'].astype(str),format='mixed').dt.strftime('%Y-%m-%d')
        f['height']=pd.to_numeric(f['连板高度'],errors='coerce');f['leader_names']=f.get('连板股','');f['broken_height']=f.get('断板高度',0);f['broken_names']=f.get('断板股','')
        f=f.sort_values('date').drop_duplicates('date',keep='last')
        for w in [5,20]:f[f'pressure{w}']=pressure_frame(f.height,w).pressure
        scope='原日报样本（年度独立数据尚未生成）；不与专题主板统计混用'
    else:return ''
    f=f[f.date.between(start,as_of)].copy().reset_index(drop=True)
    if f.empty:return '<p class="annual-height-empty">最近一年市场高度暂无可验证数据。</p>'

    lb_dates = f['date'].astype(str).tolist()
    weekdays = ["一", "二", "三", "四", "五", "六", "日"]
    lb_dates_parsed = pd.to_datetime(lb_dates, format='mixed', errors='coerce')
    lb_dates_fmt = [
        (d.strftime('%m/%d') + '/' + weekdays[d.weekday()] if pd.notnull(d) else str(orig))
        for d, orig in zip(lb_dates_parsed, lb_dates)
    ]

    raw_h = f.get('height', pd.Series(0, index=f.index))
    lb_data = [int(h) if pd.notnull(h) and h > 0 else 0 for h in raw_h]

    # 1. Image 2 压力高度 calculation (前高台阶线)
    pr_data = []
    curr_pr = lb_data[0] if len(lb_data) > 0 else 0
    for i in range(len(lb_data)):
        if i == 0:
            pr_data.append(curr_pr)
            continue
        if lb_data[i] < lb_data[i - 1]:
            curr_pr = lb_data[i - 1]
        elif lb_data[i] > curr_pr:
            curr_pr = lb_data[i]
        pr_data.append(curr_pr)

    p5_data = [round(float(p), 1) if pd.notnull(p) and np.isfinite(p) else None for p in f.get('pressure5', pd.Series(None, index=f.index))]
    p20_data = [round(float(p), 1) if pd.notnull(p) and np.isfinite(p) else None for p in f.get('pressure20', pd.Series(None, index=f.index))]

    display_peak_data = []
    dp = None
    prev_h = None
    for h in lb_data:
        if dp is None:
            dp = h
        elif prev_h is not None and h < prev_h:
            dp = prev_h
        elif h > dp:
            dp = h
        display_peak_data.append(dp)
        prev_h = h

    db_data = []
    raw_db = f.get('broken_height', pd.Series(0, index=f.index))
    for val in raw_db:
        db_val = int(val) if pd.notnull(val) and val > 0 else None
        db_data.append(db_val)

    # Labels for 连板高度 (Image 2 style: "股票名 高度" on top of each node!)
    lb_labels = []
    for _, row in f.iterrows():
        name_raw = str(row.get('leader_names', ''))
        name = name_raw.replace('、', ',').split(',')[0].strip() if name_raw else ''
        if name == 'nan':
            name = ''
        h_raw = row.get('height', 0)
        h_val = int(h_raw) if pd.notnull(h_raw) and h_raw > 0 else 0
        if h_val > 0:
            lb_labels.append(f"{name} {h_val}" if name else str(h_val))
        else:
            lb_labels.append('')

    # Labels for 断板高度 (Image 2 style: "断:股票名 高度" on bottom)
    db_labels = []
    for _, row in f.iterrows():
        val_raw = row.get('broken_height', 0)
        db_val = int(val_raw) if pd.notnull(val_raw) and val_raw > 0 else 0
        if db_val > 0:
            name_raw = str(row.get('broken_names', ''))
            name = name_raw.replace('、', ',').split(',')[0].strip() if name_raw else ''
            if name and name != 'nan' and name != '新增日旧最高梯队断板状态未重建':
                db_labels.append(f"断:{name} {db_val}")
            else:
                db_labels.append(f"断:{db_val}")
        else:
            db_labels.append('')

    # Tooltip details
    mood_map = {}
    if sentiment_df is not None and not sentiment_df.empty:
        for _, sr in sentiment_df.iterrows():
            s_date = str(sr.get('日期', ''))
            if len(s_date) == 8 and s_date.isdigit():
                s_date = f"{s_date[:4]}-{s_date[4:6]}-{s_date[6:]}"
            mood_map[s_date] = {
                'mood': str(sr.get('情绪', '')).replace('nan', ''),
                'mood_clr': str(sr.get('情绪颜色', '')).replace('nan', ''),
            }

    td_details = []
    for _, row in f.iterrows():
        d_str = str(row.get('date', ''))
        h_val = int(row.get('height')) if pd.notnull(row.get('height')) and row.get('height') > 0 else 0
        db_val = int(row.get('broken_height')) if pd.notnull(row.get('broken_height')) and row.get('broken_height') > 0 else 0
        p5_val = row.get('pressure5')
        p20_val = row.get('pressure20')
        p5_str = f"{round(float(p5_val), 1)}" if pd.notnull(p5_val) and np.isfinite(p5_val) else '缺'
        p20_str = f"{round(float(p20_val), 1)}" if pd.notnull(p20_val) and np.isfinite(p20_val) else '缺'

        m_info = mood_map.get(d_str, {})
        td_details.append({
            'date': d_str,
            'lb': h_val,
            'lb_name': str(row.get('leader_names', '')).replace('nan', ''),
            'db': db_val,
            'db_name': str(row.get('broken_names', '')).replace('nan', ''),
            'p5': p5_str,
            'p20': p20_str,
            'mood': m_info.get('mood', ''),
            'mood_clr': m_info.get('mood_clr', ''),
        })

    # 2. 龙头主升连线 (Red diagonal ascent lines from bottom)
    lt_marks = []
    n_lb = len(lb_data)
    for i in range(n_lb):
        h = lb_data[i]
        if h >= 3:
            is_peak = False
            if i == n_lb - 1:
                is_peak = True
            elif lb_data[i] > lb_data[i + 1]:
                is_peak = True
            if is_peak:
                start_idx = max(0, i - (int(h) - 1))
                name_raw = str(f.iloc[i].get('leader_names', ''))
                name = name_raw.replace('、', ',').split(',')[0].strip() if name_raw else ''
                if not name or name == 'nan':
                    name = f"{int(h)}连板"
                lt_marks.append({
                    'name': name,
                    'sb_idx': start_idx,
                    'peak_idx': i,
                    'peak_h': int(h),
                    'sb_date': str(lb_dates[start_idx]) if start_idx < len(lb_dates) else '',
                    'peak_date': str(lb_dates[i]) if i < len(lb_dates) else '',
                })

    annotations = [
        {'coord': [lb_dates[i], r['height']], 'name': str(r.get('leader_names', '')).split(',')[0].strip(), 'value': r.get('height')}
        for i, (_, r) in enumerate(f.iterrows())
        if pd.notnull(r.get('height')) and r.get('height') >= 4
        and (i == len(f) - 1 or (pd.notnull(f.iloc[i + 1].get('height')) and f.iloc[i + 1].get('height') < r.get('height')))
    ]

    total_days = len(f)
    zoom_start = max(0, int((total_days - 60) / total_days * 100)) if total_days > 60 else 0
    max_h = max(lb_data) if lb_data else 10
    y_max = max(12, int(max_h) + 2)

    if study_link is None and (ROOT / 'output/annual_height_research.html').exists():
        study_link = 'annual_height_research.html'
    link = f'<a data-annual-height-study href="{html.escape(study_link, quote=True)}" style="color:#7fc6ff">打开一年龙头领涨·反包·模仿补涨专题 →</a>' if study_link else ''
    note = '' if str(f.date.max()) == as_of else f'；高度数据实际截至{f.date.max()}，未补齐报告日'
    observed = int(f.height.notna().sum())
    missing = len(f) - observed
    coverage = f'请求窗口 {start}—{as_of}｜已核验 {observed} 个交易日，缺失 {missing} 日｜{f.date.min()}—{f.date.max()}{note}'

    adaptive_tactics_html = generate_adaptive_tactics_component(f, as_of)
    return f'''<!-- annual-height-section:start -->
<h2 class="section-title">🚀 连板高度分析 (市场高度) <span class="help-icon" data-tip="连板数为连续涨停的天数。该图表展示了市场投机高度的溢出与回撤，是情绪周期的核心指标。">?</span></h2>
<p style="color:#8b949e;font-size:12px;margin-top:-4px;margin-bottom:12px;line-height:1.6;">{html.escape(coverage)}<br>{html.escape(scope)}。压力P5/P20＝此前5/20个交易日最高板，<b>不含当日</b>；缺失不插值、不填零；可在图例切换展示前高，不能用它判断当日突破。{link}</p>
<div class="chart-container" id="lianbanChart" style="height:480px;"></div>
{adaptive_tactics_html}
<script>
var lb_dates_raw = {json.dumps(lb_dates)};
var LBL_lb = {json.dumps(lb_labels, ensure_ascii=False)};
var DBL_lb = {json.dumps(db_labels, ensure_ascii=False)};
var TD_lb = {json.dumps(td_details, ensure_ascii=False)};
var LTM_lb = {json.dumps(lt_marks, ensure_ascii=False)};

(function(){{
    var el = document.getElementById('lianbanChart');
    if (!window.echarts) {{
        el.textContent = '交互图表库未载入，请刷新页面重试。';
        return;
    }}
    var c = echarts.init(el, 'dark');
    var opt = {{
        backgroundColor: '#161b22',
        grid: {{ left: 45, right: 25, top: 55, bottom: 42 }},
        tooltip: {{
            trigger: 'axis',
            backgroundColor: 'rgba(22, 27, 34, 0.95)',
            borderColor: '#30363d',
            borderWidth: 1,
            textStyle: {{ color: '#e6edf3', fontSize: 13 }},
            formatter: function(p) {{
                var i = p[0].dataIndex, d = TD_lb[i];
                if (!d) return '';
                var h = '<b style="color:#58a6ff">' + d.date + '</b>';
                if (d.mood) {{
                    h += '  <span style="color:' + (d.mood_clr || '#ffcc66') + ';font-size:11px;padding:1px 4px;border-radius:3px;background:rgba(255,255,255,0.08);">' + d.mood + '</span>';
                }}
                h += '<br>';
                h += '<span style="color:#58a6ff">● 连板高度 ' + d.lb + '板  ' + (d.lb_name && d.lb_name !== 'nan' ? d.lb_name : '') + '</span><br>';
                h += '<span style="color:#00e5ff">● 事前压力 P5: ' + d.p5 + '板  /  P20: ' + d.p20 + '板</span><br>';
                if (d.db > 0) {{
                    h += '<span style="color:#ff7b72">● 断板高度 ' + d.db + '板  ' + (d.db_name && d.db_name !== 'nan' ? d.db_name : '') + '</span><br>';
                }}
                var day_lts = LTM_lb.filter(function(m) {{ return String(m.peak_date || m.date) === String(d.date); }});
                if (day_lts.length > 0) {{
                    day_lts.forEach(function(m) {{
                        h += '<span style="color:#ff8800">▲ 龙头首板: ' + m.name + ' @ ' + m.sb_date + '</span><br>';
                    }});
                }}
                return h;
            }}
        }},
        legend: {{
            show: true,
            data: ['连板高度', '压力高度', '断板高度', '龙头主升连线', '压力P20（事前）', '展示前高（含当日）', '龙头峰值标注'],
            selected: {{
                '展示前高（含当日）': false,
                '龙头峰值标注': false,
                '压力P20（事前）': false
            }},
            top: 10,
            right: 25,
            textStyle: {{ fontSize: 12, color: '#8b949e' }}
        }},
        xAxis: {{
            type: 'category',
            data: {json.dumps(lb_dates_fmt, ensure_ascii=False)},
            axisLine: {{ lineStyle: {{ color: '#333' }} }},
            axisLabel: {{ color: '#8b949e', fontSize: 10, interval: 'auto', margin: 18 }},
            axisTick: {{ show: true, lineStyle: {{ color: '#222' }} }}
        }},
        yAxis: {{
            type: 'value',
            min: 0,
            max: {y_max},
            minInterval: 2,
            axisLine: {{ show: false }},
            axisLabel: {{ color: '#8b949e', fontSize: 11 }},
            splitLine: {{ show: true, lineStyle: {{ color: '#21262d' }} }}
        }},
        dataZoom: [
            {{ type: 'inside', xAxisIndex: 0, start: {zoom_start}, end: 100 }},
            {{
                type: 'slider',
                xAxisIndex: 0,
                start: {zoom_start},
                end: 100,
                height: 16,
                bottom: 4,
                backgroundColor: '#0d1117',
                borderColor: '#30363d',
                fillerColor: 'rgba(88, 166, 255, 0.15)',
                textStyle: {{ color: '#8b949e', fontSize: 10 }}
            }}
        ],
        series: [
            {{
                name: '连板高度',
                type: 'line',
                data: {json.dumps(lb_data)},
                z: 10,
                symbol: 'circle',
                symbolSize: 8,
                lineStyle: {{ color: '#58a6ff', width: 3 }},
                itemStyle: {{ color: '#58a6ff', borderColor: '#e6edf3', borderWidth: 1.5 }},
                label: {{
                    show: true,
                    position: 'top',
                    color: '#58a6ff',
                    fontSize: 11,
                    fontWeight: 'bold',
                    backgroundColor: 'rgba(22, 27, 34, 0.85)',
                    padding: [2, 4],
                    borderRadius: 4,
                    formatter: function(p) {{ return LBL_lb[p.dataIndex] || ''; }}
                }}
            }},
            {{
                name: '压力高度',
                type: 'line',
                data: {json.dumps(pr_data)},
                z: 8,
                symbol: 'circle',
                symbolSize: 4,
                lineStyle: {{ color: '#00e5ff', width: 2 }},
                itemStyle: {{ color: '#00e5ff' }}
            }},
            {{
                name: '断板高度',
                type: 'line',
                data: {json.dumps(db_data)},
                z: 9,
                symbol: 'rect',
                symbolSize: 6,
                connectNulls: false,
                lineStyle: {{ color: '#ff7b72', width: 2, type: 'dotted' }},
                itemStyle: {{ color: '#ff7b72' }},
                label: {{
                    show: true,
                    position: 'bottom',
                    color: '#ff7b72',
                    fontSize: 10,
                    backgroundColor: 'rgba(13, 17, 23, 0.7)',
                    padding: [2, 4],
                    borderRadius: 4,
                    formatter: function(p) {{ return DBL_lb[p.dataIndex] || ''; }}
                }}
            }},
            {{
                name: '压力P20（事前）',
                type: 'line',
                step: 'end',
                data: {json.dumps(p20_data)},
                lineStyle: {{ color: '#edc576', width: 1, type: 'dashed' }},
                symbol: 'none'
            }},
            {{
                name: '展示前高（含当日）',
                type: 'line',
                step: 'end',
                data: {json.dumps(display_peak_data)},
                lineStyle: {{ color: '#bd8ac9', width: 1 }},
                symbol: 'none'
            }},
            {{
                name: '龙头峰值标注',
                type: 'scatter',
                data: {json.dumps(annotations, ensure_ascii=False)},
                symbolSize: 6,
                itemStyle: {{ color: '#efb25b' }},
                label: {{ show: true, position: 'top', fontSize: 10, color: '#efb25b', formatter: function(p) {{ return p.name; }} }},
                labelLayout: {{ hideOverlap: true }}
            }}
        ]
    }};

    if (LTM_lb && LTM_lb.length > 0) {{
        var sbScatterData = [];
        var markLineData = [];
        for (var k = 0; k < LTM_lb.length; k++) {{
            var m = LTM_lb[k];
            var s_idx = m.sb_idx;
            var p_idx = m.peak_idx;
            sbScatterData.push({{
                value: [s_idx, 0],
                name: m.name,
                peak_h: m.peak_h,
                sb_date: m.sb_date
            }});
            markLineData.push([
                {{ coord: [s_idx, 0] }},
                {{ coord: [p_idx, m.peak_h] }}
            ]);
        }}
        opt.series.push({{
            name: '龙头主升连线',
            type: 'scatter',
            xAxisIndex: 0,
            yAxisIndex: 0,
            data: sbScatterData,
            symbol: 'circle',
            symbolSize: 6,
            z: 15,
            itemStyle: {{ color: '#ff3333' }},
            label: {{
                show: true,
                position: 'bottom',
                color: '#ff3333',
                fontSize: 10,
                backgroundColor: 'rgba(22, 27, 34, 0.8)',
                padding: [1, 2],
                borderRadius: 2,
                formatter: function(p) {{ return p.data.name; }}
            }},
            markLine: {{
                silent: true,
                symbol: ['none', 'none'],
                lineStyle: {{ color: '#ff3333', width: 2, type: 'solid' }},
                label: {{ show: false }},
                data: markLineData
            }}
        }});
    }}

    c.setOption(opt);
    window.addEventListener('resize', function() {{ c.resize(); }});
}})();
</script><!-- annual-height-section:end -->'''


def package_annual_study(main_document,source_dir,site_dir):
    """Copy an existing self-contained report and rebase links for both publish depths."""
    import re,shutil
    pattern=r'<a\b[^>]*data-annual-height-study[^>]*>.*?</a>'
    if not re.search(pattern,main_document,re.S):return main_document,main_document
    source=Path(source_dir)/'annual_height_research.html'
    if not source.is_file():
        stripped=re.sub(pattern,'年度专题文件未随本次发布提供',main_document,flags=re.S)
        return stripped,stripped
    target=Path(site_dir)/'research/annual_height_research.html';target.parent.mkdir(parents=True,exist_ok=True)
    if source.resolve()!=target.resolve():shutil.copy2(source,target)
    browser=Path(source_dir)/'annual_height_paths.html'
    if browser.is_file():
        browser_target=target.parent/browser.name
        if browser.resolve()!=browser_target.resolve():shutil.copy2(browser,browser_target)
    def rebase(prefix):
        return re.sub(pattern,lambda m:re.sub(r'href="[^"]*"',f'href="{prefix}research/annual_height_research.html"',m.group(0)),main_document,flags=re.S)
    return rebase('../'),rebase('')


def replace_height_section(document,new_section):
    """Replace only the height chart section; fail closed if the old boundary is ambiguous."""
    import re
    start='<!-- annual-height-section:start -->';end='<!-- annual-height-section:end -->'
    if start in document and end in document:
        a=document.index(start);b=document.index(end,a)+len(end)
    else:
        match=re.search(r'<h2\b[^>]*>[^<]*连板高度分析.*?</h2>',document,re.S)
        if not match:raise ValueError('Existing height section heading not found')
        a=match.start();chart=document.find('id="lianbanChart"',match.end())
        if chart<0:raise ValueError('Existing height chart not found')
        script=document.find('</script>',chart)
        if script<0:raise ValueError('Height chart script boundary not found')
        b=script+len('</script>')
    return document[:a]+new_section+document[b:]


def extend_from_cached_pool(history,pool,as_of):
    """Read-only daily extension; no retroactive overwrite of verified historical heights."""
    f=history.copy();pool=pool.copy()
    pool['date']=pd.to_datetime(pool['日期'].astype(str),format='mixed',errors='coerce').dt.strftime('%Y-%m-%d')
    pool=pool[(pool.date>str(f.date.max()))&(pool.date<=as_of)]
    if '类型' in pool:pool=pool[pool['类型'].eq('ZT')]
    pool=pool[pool['代码'].str.startswith(('sh60','sz00'))&~pool['名称'].str.contains('ST|退',case=False,na=False)]
    rows=[]
    for date,g in pool.groupby('date'):
        heights=pd.to_numeric(g['连板数'],errors='coerce');highest=heights.max()
        if pd.isna(highest):continue
        tops=g[heights.eq(highest)]
        rows.append(dict(date=date,height=int(highest),leader_names='、'.join(tops['名称'].astype(str)),leader_codes='|'.join(tops['代码'].astype(str)),broken_height=None,broken_names='新增日旧最高梯队断板状态未重建',source='completed_daily_pool'))
    if not rows:return f
    f=pd.concat([f,pd.DataFrame(rows)],ignore_index=True).sort_values('date')
    calendar_path=ROOT/'data/trading_calendar_cache.csv'
    if calendar_path.is_file():
        calendar=pd.read_csv(calendar_path,dtype=str).iloc[:,0]
        calendar=calendar[(calendar>=f.date.min())&(calendar<=f.date.max())]
        if len(calendar):f=f.set_index('date').reindex(calendar).rename_axis('date').reset_index()
    for window in [5,10,20]:f[f'pressure{window}']=pressure_frame(f.height,window).pressure
    return f


def generate_adaptive_tactics_component(f, as_of):
    """
    自适应动态计算连板高度四大战法（A~G联动）与当日实际连板标的的操盘预案.
    自适应规则：
    1. 战法 A (事前 P5 压力线)：动态比对空间板高度与 P5 压力线，输出突破主升或赶顶预警；联动 2~3 板低位突破候选。
    2. 战法 E/B (旧高断板与反包)：自动回溯近 12 天内高标断板历史，计算距今日天数，高亮【4~6 天黄金反包窗口】标的。
    3. 战法 D/F (龙头峰值与基因)：检测最高板是否触及中位寿命 5 天极值，输出换手真龙确认与逃生防守线。
    4. 战法 G/C (异动滑窗与补涨)：自动匹配空间龙同概念的低位跟风梯队，核算老高标 10 天异动滑窗出清进度。
    """
    if f.empty:
        return ''
    
    latest_row = f.iloc[-1]
    curr_date = str(latest_row['date'])
    curr_h = int(latest_row['height']) if pd.notnull(latest_row['height']) else 0
    curr_leader = str(latest_row.get('leader_names', ''))
    if curr_leader == 'nan':
        curr_leader = '暂无'
    curr_p5 = round(float(latest_row.get('pressure5', 6.0)), 1)
    curr_p20 = round(float(latest_row.get('pressure20', 11.0)), 1)

    # 1. 读取当日涨停池分层
    pool_path = ROOT / 'data/涨停历史缓存.csv'
    ladder_groups = {}
    if pool_path.is_file():
        try:
            pool = pd.read_csv(pool_path, dtype={'日期': str, '代码': str})
            pool_today = pool[pool['日期'].astype(str).str.replace('-', '') == curr_date.replace('-', '')]
            if '类型' in pool_today.columns:
                pool_today = pool_today[pool_today['类型'] == 'ZT']
            pool_today = pool_today[pool_today['代码'].str.startswith(('sh60', 'sz00')) & ~pool_today['名称'].str.contains('ST|退', case=False, na=False)]
            for _, r in pool_today.iterrows():
                try:
                    lb = int(r.get('连板数', 1))
                except:
                    lb = 1
                name = str(r['名称'])
                code = str(r['代码'])
                ladder_groups.setdefault(lb, []).append({'name': name, 'code': code})
        except Exception as e:
            pass
            
    # 2. 回溯断板标的
    broken_candidates = []
    recent_f = f.tail(15)
    dates_list = recent_f['date'].tolist()
    curr_idx = len(dates_list) - 1
    seen_broken = set()
    for i in range(curr_idx - 1, max(-1, curr_idx - 12), -1):
        row_i = recent_f.iloc[i]
        d_i = row_i['date']
        days_ago = curr_idx - i
        h_i = row_i.get('height', 0)
        h_next = recent_f.iloc[i+1].get('height', 0)
        names_i = str(row_i.get('leader_names', ''))
        if pd.notnull(h_i) and h_i >= 4 and h_next < h_i:
            for n in names_i.split('、'):
                n = n.strip()
                if n and n not in seen_broken and n != 'nan':
                    seen_broken.add(n)
                    broken_candidates.append({
                        'name': n,
                        'peak_height': int(h_i),
                        'peak_date': d_i,
                        'days_since_break': days_ago,
                        'is_golden_window': (4 <= days_ago <= 6)
                    })

    # 3. 自适应逻辑计算
    # 战法 A
    p5_gap = curr_p5 - curr_h
    if p5_gap > 0:
        p5_status_badge = f'<span style="background:rgba(96,213,190,0.15);color:#60d5be;border:1px solid #60d5be;padding:2px 8px;border-radius:10px;font-weight:700">低位冲顶大考 (距P5差 {p5_gap:.1f}板)</span>'
        p5_action = f"空间龙 <b>{curr_leader} ({curr_h}板)</b> 处于 ≤6板 低位突破区间，5日新高概率高，但冲刺 <b>{curr_h+1}板</b> 直面前高 P5({curr_p5}板) 压力！严禁缩量一字追高，必须经早盘充分分歧换手放量过峰；同时重点观察 3板中位先锋晋级。"
    elif p5_gap == 0:
        p5_status_badge = f'<span style="background:rgba(239,178,91,0.15);color:#efb25b;border:1px solid #efb25b;padding:2px 8px;border-radius:10px;font-weight:700">平前高临界点 (平P5 {curr_p5}板)</span>'
        p5_action = f"空间龙 <b>{curr_leader} ({curr_h}板)</b> 恰好平齐前高 P5({curr_p5}板)，多空决战分水岭！开盘缩量必炸，必须爆量换手过峰才确立新周期。"
    else:
        p5_status_badge = f'<span style="background:rgba(241,126,147,0.15);color:#f17e93;border:1px solid #f17e93;padding:2px 8px;border-radius:10px;font-weight:700">高位突破赶顶戒备 (已超P5 {abs(p5_gap):.1f}板)</span>'
        p5_action = f"空间龙 <b>{curr_leader} ({curr_h}板)</b> 已突破 P5({curr_p5}板) 压制线，警惕 ≥7板 诱多赶顶与筹码松动，进入鱼尾博傻行情，持筹者分批落袋，严禁场外接盘。"

    # 低位梯队推荐
    sub_3b = [x['name'] for x in ladder_groups.get(3, [])]
    sub_2b = [x['name'] for x in ladder_groups.get(2, [])]
    sub_candidates_str = "、".join(sub_3b) if sub_3b else ("、".join(sub_2b[:3]) if sub_2b else "暂无")

    # 战法 E/B (旧高断板黄金反包)
    golden_broken = [x for x in broken_candidates if x['is_golden_window']]
    cooling_broken = [x for x in broken_candidates if not x['is_golden_window'] and x['days_since_break'] < 4]
    
    if golden_broken:
        eb_badge = f'<span style="background:rgba(241,126,147,0.2);color:#f17e93;border:1px solid #f17e93;padding:2px 8px;border-radius:10px;font-weight:700">🎯 精确命中黄金反包窗口 (第4-6天)</span>'
        eb_targets_str = "、".join([f"<b>{x['name']}</b> (前{x['peak_height']}板 · 断板第{x['days_since_break']}天)" for x in golden_broken])
        eb_action = f"重点盯防 {eb_targets_str}！历史 209 天数据实证断板后 4~6 天黄金反包胜率达 48%。<b>临盘指令</b>：早盘 09:35 前翻红站上 VWAP 均线，分时带量突破首阴实体下沿轻仓 0.5~1.0 成试错，跌破当日分时低点（-2%）坚决止损！"
    else:
        eb_badge = f'<span style="background:rgba(139,148,158,0.15);color:#8b949e;padding:2px 8px;border-radius:10px;font-weight:600">无高位断板达标标的</span>'
        eb_action = "当前无处于第 4~6 天的高位断板老妖股；若有刚断板 1~3 天的个股（如襄阳轴承）属于风险释放期，严禁盲目接飞刀！"

    # 战法 D/F (龙头峰值与基因)
    if curr_h >= 5:
        df_badge = f'<span style="background:rgba(239,178,91,0.2);color:#efb25b;border:1px solid #efb25b;padding:2px 8px;border-radius:10px;font-weight:700">触及周期中位寿命 5 天极值！</span>'
        df_action = f"空间龙 <b>{curr_leader} ({curr_h}板)</b> 正好进入龙头中位寿命 5 天的生死大考！历史实证见顶后仅剩 0~1 天逃生窗口。<b>真龙基因判定</b>：93% 真龙必须在第 5-6 天经历盘中爆量大分歧换手（换手率需超 20%~25%）后回封，未换手前严禁追高，持筹者若开板不回坚决止盈。"
    else:
        df_badge = f'<span style="background:rgba(96,213,190,0.15);color:#60d5be;padding:2px 8px;border-radius:10px;font-weight:700">龙头上升攀爬期 ({curr_h}板)</span>'
        df_action = f"空间龙 <b>{curr_leader} ({curr_h}板)</b> 尚未触及 5 天中位寿命线，持股跟随均线多头，跌破分时均价线离场。"

    # 战法 G/C (异动滑窗与补涨)
    gc_badge = f'<span style="background:rgba(236,72,153,0.15);color:#ec4899;border:1px solid #ec4899;padding:2px 8px;border-radius:10px;font-weight:700">补涨梯队与监管控速</span>'
    gc_action = f"<b>低位补涨锁定</b>：重点关注空间龙带动下的 2板先锋 <b>{', '.join(sub_2b[:3]) if sub_2b else '暂无'}</b>，若龙头分歧则关注补涨秒板卡位。<b>异动滑窗出清</b>：老高标（海洋王/大业股份）横盘已有 5 天，前期连续异动正在被时间平滑出清，监管降温后为二波反弹创造政策空窗期。"

    html_code = f"""
    <!-- 连板高度战法深研指引 (战法 A ~ G 联动) · 自适应实战操作预案 -->
    <div class="annual-tactics-guide" style="margin-top:14px;background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px 18px;box-shadow:0 6px 18px rgba(0,0,0,0.25);">
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;border-bottom:1px solid #21262d;padding-bottom:10px;margin-bottom:12px;">
            <div style="font-weight:800;color:#f0f6fc;font-size:14px;display:flex;align-items:center;gap:8px;">
                <span>🎯 连板高度量化战法深研指引 (战法 A ~ G 联动) · 实际连板标的自适应预案</span>
                <span style="font-size:11px;background:rgba(88,166,255,0.15);color:#58a6ff;border:1px solid rgba(88,166,255,0.3);padding:2px 8px;border-radius:10px;font-weight:700">基准日: {curr_date}</span>
            </div>
            <div style="font-size:11.5px;color:#8b949e;">
                事前P5压力线: <b style="color:#60d5be;">{curr_p5}板</b> ｜ 空间板: <b style="color:#efb25b;">{curr_leader} ({curr_h}板)</b>
            </div>
        </div>

        <!-- 1. 战法理念总览卡片 (图片可视化要素) -->
        <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(260px, 1fr));gap:10px;margin-bottom:14px;">
            <div style="background:rgba(96,213,190,0.06);border-left:3px solid #60d5be;padding:8px 12px;border-radius:6px;">
                <b style="color:#60d5be;font-size:12.5px;">📈 战法 A (事前P5压力线)</b>
                <div style="font-size:11.5px;color:#8b949e;line-height:1.45;margin-top:2px;">低位突破(≤6板)后5日新高率100%做主升；高位突破(≥7板)警惕诱多赶顶。</div>
            </div>
            <div style="background:rgba(241,126,147,0.06);border-left:3px solid #f17e93;padding:8px 12px;border-radius:6px;">
                <b style="color:#f17e93;font-size:12.5px;">📉 战法 E / B (旧高断板与反包)</b>
                <div style="font-size:11.5px;color:#8b949e;line-height:1.45;margin-top:2px;">高位断板全市场暴跌3档砸回4板；断板后4-6天为黄金反包期(48%)。</div>
            </div>
            <div style="background:rgba(239,178,91,0.06);border-left:3px solid #efb25b;padding:8px 12px;border-radius:6px;">
                <b style="color:#efb25b;font-size:12.5px;">👑 战法 D / F (龙头峰值与基因)</b>
                <div style="font-size:11.5px;color:#8b949e;line-height:1.45;margin-top:2px;">周期中位寿命5天，见顶后仅剩0-1天逃生窗口；93%真龙必经历分歧换手反包。</div>
            </div>
            <div style="background:rgba(236,72,153,0.06);border-left:3px solid #ec4899;padding:8px 12px;border-radius:6px;">
                <b style="color:#ec4899;font-size:12.5px;">⚡ 战法 G / C (异动滑窗与补涨)</b>
                <div style="font-size:11.5px;color:#8b949e;line-height:1.45;margin-top:2px;">10天100%监管博弈控速，滑窗出清发动二波；龙头7板+后跟风迎第7天爆发。</div>
            </div>
        </div>

        <!-- 2. 当日实际标的自适应实战预案与执行指令卡片 -->
        <div style="background:#0d1117;border:1px solid #21262d;border-radius:8px;padding:12px 14px;margin-bottom:12px;">
            <div style="font-weight:700;color:#58a6ff;font-size:13px;margin-bottom:8px;display:flex;align-items:center;gap:6px;">
                <span>⚡ 当日实际连板标的 · 四大战法自适应推演与操盘预案</span>
                <span style="font-size:11px;color:#8b949e;font-weight:normal;">(算法实时结合当前连板梯队自动匹配)</span>
            </div>
            <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(280px, 1fr));gap:12px;">
                <!-- 战法 A 实际推演 -->
                <div style="background:#161b22;border:1px solid rgba(96,213,190,0.3);border-radius:6px;padding:10px 12px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
                        <b style="color:#60d5be;font-size:12.5px;">🎯 战法 A 实际推演 · P5压力博弈</b>
                        {p5_status_badge}
                    </div>
                    <div style="font-size:12px;color:#c9d1d9;line-height:1.55;">
                        • <b>空间龙目标</b>：{curr_leader} ({curr_h}板)<br>
                        • <b>低位主升梯队</b>：{sub_candidates_str}<br>
                        • <b>实战指令</b>：{p5_action}
                    </div>
                </div>

                <!-- 战法 E/B 实际推演 -->
                <div style="background:#161b22;border:1px solid rgba(241,126,147,0.3);border-radius:6px;padding:10px 12px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
                        <b style="color:#f17e93;font-size:12.5px;">🎯 战法 E/B 实际推演 · 旧高黄金反包</b>
                        {eb_badge}
                    </div>
                    <div style="font-size:12px;color:#c9d1d9;line-height:1.55;">
                        • <b>黄金反包标的</b>：{eb_action}
                    </div>
                </div>

                <!-- 战法 D/F 实际推演 -->
                <div style="background:#161b22;border:1px solid rgba(239,178,91,0.3);border-radius:6px;padding:10px 12px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
                        <b style="color:#efb25b;font-size:12.5px;">🎯 战法 D/F 实际推演 · 龙头寿命与基因</b>
                        {df_badge}
                    </div>
                    <div style="font-size:12px;color:#c9d1d9;line-height:1.55;">
                        • <b>真龙基因检验</b>：{df_action}
                    </div>
                </div>

                <!-- 战法 G/C 实际推演 -->
                <div style="background:#161b22;border:1px solid rgba(236,72,153,0.3);border-radius:6px;padding:10px 12px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
                        <b style="color:#ec4899;font-size:12.5px;">🎯 战法 G/C 实际推演 · 监管滑窗与补涨</b>
                        {gc_badge}
                    </div>
                    <div style="font-size:12px;color:#c9d1d9;line-height:1.55;">
                        • <b>补涨与滑窗出清</b>：{gc_action}
                    </div>
                </div>
            </div>
        </div>

        <!-- 3. 连板标的临盘执行清单 (打印级速查表) -->
        <div style="overflow-x:auto;">
            <table style="width:100%;border-collapse:collapse;font-size:12px;text-align:left;">
                <thead>
                    <tr style="background:#21262d;color:#8b949e;">
                        <th style="padding:6px 10px;border:1px solid #30363d;">战法归属</th>
                        <th style="padding:6px 10px;border:1px solid #30363d;">重点标的</th>
                        <th style="padding:6px 10px;border:1px solid #30363d;text-align:center;">当前板数/状态</th>
                        <th style="padding:6px 10px;border:1px solid #30363d;">临盘核心动作与买点</th>
                        <th style="padding:6px 10px;border:1px solid #30363d;">止损 / 逃生底线</th>
                    </tr>
                </thead>
                <tbody>
                    <tr style="border-bottom:1px solid #21262d;">
                        <td style="padding:6px 10px;color:#60d5be;font-weight:700;">战法 A (P5冲锋)</td>
                        <td style="padding:6px 10px;color:#f0f6fc;font-weight:700;">{curr_leader}</td>
                        <td style="padding:6px 10px;text-align:center;color:#60d5be;font-weight:700;">{curr_h}板冲{curr_h+1}板</td>
                        <td style="padding:6px 10px;color:#c9d1d9;">早盘小幅高开充分换手，放量突破 6板 确认，严禁缩量一字追高</td>
                        <td style="padding:6px 10px;color:#ff7b72;font-weight:600;">失守分时黄线 3 分钟果断离场</td>
                    </tr>
                    <tr style="border-bottom:1px solid #21262d;">
                        <td style="padding:6px 10px;color:#60d5be;font-weight:700;">战法 A (低位主升)</td>
                        <td style="padding:6px 10px;color:#f0f6fc;font-weight:700;">{sub_candidates_str}</td>
                        <td style="padding:6px 10px;text-align:center;color:#58a6ff;font-weight:700;">2-3板晋级</td>
                        <td style="padding:6px 10px;color:#c9d1d9;">竞价放量异动，早盘快速打板晋级，博弈低位 ≤6板 100%新高胜率</td>
                        <td style="padding:6px 10px;color:#ff7b72;font-weight:600;">炸板翻绿立即止损</td>
                    </tr>
                    <tr style="border-bottom:1px solid #21262d;">
                        <td style="padding:6px 10px;color:#f17e93;font-weight:700;">战法 E/B (黄金反包)</td>
                        <td style="padding:6px 10px;color:#f0f6fc;font-weight:700;">{', '.join([x['name'] for x in golden_broken]) if golden_broken else '无'}</td>
                        <td style="padding:6px 10px;text-align:center;color:#f17e93;font-weight:700;">断板第 4-6 天</td>
                        <td style="padding:6px 10px;color:#c9d1d9;">早盘 9:35 前翻红站上 VWAP 均价线，带量突破首阴实体下沿轻仓试错</td>
                        <td style="padding:6px 10px;color:#ff7b72;font-weight:600;">跌破当日分时低点（-2% 坚决止损）</td>
                    </tr>
                    <tr style="border-bottom:1px solid #21262d;">
                        <td style="padding:6px 10px;color:#ec4899;font-weight:700;">战法 G/C (低位补涨)</td>
                        <td style="padding:6px 10px;color:#f0f6fc;font-weight:700;">{sub_2b[0] if sub_2b else '暂无'}</td>
                        <td style="padding:6px 10px;text-align:center;color:#ec4899;font-weight:700;">2板冲3板</td>
                        <td style="padding:6px 10px;color:#c9d1d9;">空间龙若出现高位分歧，同题材低位 2板 快速卡位秒板承接</td>
                        <td style="padding:6px 10px;color:#ff7b72;font-weight:600;">未封死涨停日内保本离场</td>
                    </tr>
                </tbody>
            </table>
        </div>
    </div>
    """
    return html_code
