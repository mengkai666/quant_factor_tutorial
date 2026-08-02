import pandas as pd
import baostock as bs
import json
import datetime
from time_utils import get_latest_date

def fetch_stock_data(stock_code, days=120):
    bs.login()
    end_date = get_latest_date().strftime('%Y-%m-%d')
    start_date = (get_latest_date() - datetime.timedelta(days=days*2)).strftime('%Y-%m-%d')
    rs = bs.query_history_k_data_plus(
        stock_code, 
        "date,open,high,low,close,volume",
        start_date=start_date, 
        end_date=end_date, 
        frequency="d", 
        adjustflag="3"
    )
    data_list = []
    while (rs.error_code == '0') & rs.next():
        data_list.append(rs.get_row_data())
    df = pd.DataFrame(data_list, columns=rs.fields)
    
    # Convert types
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col])
        
    df = df.tail(days).reset_index(drop=True)
    bs.logout()
    return df

def calc_indicators(df):
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['ema5'] = df['close'].ewm(span=5, adjust=False).mean()
    
    # MACD
    df['dif'] = df['close'].ewm(span=12, adjust=False).mean() - df['close'].ewm(span=26, adjust=False).mean()
    df['dea'] = df['dif'].ewm(span=9, adjust=False).mean()
    df['macd'] = (df['dif'] - df['dea']) * 2
    
    # BIAS base on EMA5
    df['bias'] = (df['close'] - df['ema5']) / df['ema5'] * 100
    
    # State Logic
    states = []
    prev_macd = 0
    for i in range(len(df)):
        close = df.loc[i, 'close']
        ema20 = df.loc[i, 'ema20']
        macd = df.loc[i, 'macd']
        
        if close < ema20:
            states.append('red')
        else:
            if macd > prev_macd:
                states.append('green')
            else:
                states.append('yellow')
        prev_macd = macd
        
    df['state'] = states
    return df

def build_html(df, code, name):
    # Prepare JSON data
    candle_data = []
    vol_data = []
    ema_long_data = []
    ema_short_data = []
    macd_data = []
    dif_data = []
    dea_data = []
    bias_data = []
    state_data = []
    markers = []
    
    prev_state = None
    for i, row in df.iterrows():
        t = row['date']
        
        # Candle
        candle_data.append({
            "time": t, "open": float(row['open']), "high": float(row['high']), 
            "low": float(row['low']), "close": float(row['close'])
        })
        
        # Vol
        v_color = "rgba(38,166,154,0.5)" if row['close'] >= row['open'] else "rgba(239,83,80,0.5)"
        vol_data.append({"time": t, "value": int(row['volume']), "color": v_color})
        
        # EMAs
        ema_long_data.append({"time": t, "value": round(row['ema20'], 4)})
        ema_short_data.append({"time": t, "value": round(row['ema5'], 4)})
        
        # MACD
        m_color = "#26a69a" if row['macd'] >= 0 else "#ef5350"
        macd_data.append({"time": t, "value": round(row['macd'], 4), "color": m_color})
        dif_data.append({"time": t, "value": round(row['dif'], 4)})
        dea_data.append({"time": t, "value": round(row['dea'], 4)})
        
        # BIAS
        bias_data.append({"time": t, "value": round(row['bias'], 4)})
        
        # State & Markers
        st = row['state']
        state_data.append({"time": t, "state": st})
        
        if st != prev_state and prev_state is not None:
            if st == 'green':
                markers.append({"time": t, "position": "belowBar", "color": "#00e676", "shape": "arrowUp", "text": "绿"})
            elif st == 'yellow':
                markers.append({"time": t, "position": "aboveBar", "color": "#ffd600", "shape": "circle", "text": "黄"})
            elif st == 'red':
                markers.append({"time": t, "position": "aboveBar", "color": "#ff1744", "shape": "arrowDown", "text": "红"})
        prev_state = st
    
    # Header values
    last_row = df.iloc[-1]
    prev_row = df.iloc[-2] if len(df) > 1 else last_row
    chg = (last_row['close'] - prev_row['close']) / prev_row['close'] * 100
    chg_cls = "up" if chg >= 0 else "down"
    chg_str_sign = "+" if chg >= 0 else ""
    
    html_template = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__NAME__量化分析</title>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #0f1119; color: #d1d4dc; font-family: -apple-system, BlinkMacSystemFont, sans-serif; overflow-x: hidden; height: 100vh; display: flex; flex-direction: column; }
  .header { display: flex; align-items: center; justify-content: space-between; padding: 10px 24px; background: #1a1e2e; border-bottom: 1px solid #2a2e39; flex-shrink: 0; }
  .header-left { display: flex; align-items: center; gap: 20px; }
  .stock-name { font-size: 18px; font-weight: 700; color: #fff; }
  .stock-price { font-size: 20px; font-weight: 700; }
  .stock-change { font-size: 13px; padding: 2px 10px; border-radius: 4px; font-weight: 600; }
  .up { color: #26a69a; }
  .down { color: #ef5350; }
  .up-bg { background: rgba(38,166,154,0.12); color: #26a69a; }
  .down-bg { background: rgba(239,83,80,0.12); color: #ef5350; }
  .source-tag { font-size: 11px; color: #6b7280; background: #1e2235; padding: 3px 10px; border-radius: 4px; border: 1px solid #2a2e39; }
  .header-right { display: flex; align-items: center; gap: 12px; }
  .state-badge { font-size: 13px; font-weight: 600; padding: 5px 14px; border-radius: 6px; border: 1px solid;}
  .state-red { color: #ff1744; border-color: #ff174440; background: rgba(255,23,68,0.08); }
  .state-green { color: #00e676; border-color: #00e67640; background: rgba(0,230,118,0.08); }
  .state-yellow { color: #ffd600; border-color: #ffd60040; background: rgba(255,214,0,0.08); }
  .factor-cards { display: flex; align-items: stretch; gap: 1px; background: #2a2e39; border-bottom: 1px solid #2a2e39; flex-shrink: 0; }
  .factor-card { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 6px 8px; background: #161928; }
  .factor-card-label { font-size: 10px; color: #6b7280; text-transform: uppercase; margin-bottom: 2px; }
  .factor-card-value { font-size: 14px; font-weight: 700; }
  .stats-bar { display: flex; height: 3px; flex-shrink: 0; }
  .stats-red { background: #ff1744; } .stats-green { background: #00e676; } .stats-yellow { background: #ffd600; }
  .charts-area { flex: 1; display: flex; flex-direction: column; position: relative; }
  .panel { position: relative; border-bottom: 1px solid #1e2235; }
  .panel-label { position: absolute; top: 4px; left: 10px; font-size: 10px; font-weight: 600; color: #4a5068; z-index: 10; pointer-events: none; }
  .tooltip { position: absolute; top: 8px; left: 12px; background: rgba(22,25,40,0.95); border: 1px solid #2a2e3980; border-radius: 8px; padding: 12px 16px; font-size: 12px; z-index: 100; pointer-events: none; min-width: 200px; }
  .tooltip-title { font-weight: 700; margin-bottom: 6px; color: #fff; border-bottom: 1px solid #2a2e39; padding-bottom: 6px; }
  .tooltip-section { margin: 4px 0; padding: 4px 0; }
  .tooltip-section-title { font-size: 10px; color: #4a5068; margin-bottom: 2px; }
  .tooltip-row { display: flex; justify-content: space-between; gap: 20px; }
  .tooltip-label { color: #6b7280; } .tooltip-val { color: #d1d4dc; font-weight: 600; }
  .legend-bar { display: flex; justify-content: center; gap: 20px; padding: 6px 16px; font-size: 11px; color: #4a5068; flex-shrink: 0; }
  .legend-item { display: flex; align-items: center; gap: 5px; }
  .legend-dot { width: 8px; height: 8px; border-radius: 2px; display: inline-block; }
</style>
</head>
<body>
<div class="header">
  <div class="header-left">
    <span class="stock-name">__NAME__ (__CODE__)</span>
    <span class="stock-price __CHGCLS__">__PRICE__</span>
    <span class="stock-change __CHGCLS__-bg">__CHGPCT__%</span>
    <span class="source-tag">baostock · 100交易日</span>
  </div>
  <div class="header-right">__STATE_HTML__</div>
</div>
<div class="factor-cards">
  <div class="factor-card"><span class="factor-card-label">EMA(20)</span><span class="factor-card-value" style="color:#2962ff">__EMA20__</span></div>
  <div class="factor-card"><span class="factor-card-label">EMA(5)</span><span class="factor-card-value" style="color:#f7a21b">__EMA5__</span></div>
  <div class="factor-card"><span class="factor-card-label">MACD</span><span class="factor-card-value" style="color:#26a69a">__MACD__</span></div>
  <div class="factor-card"><span class="factor-card-label">BIAS</span><span class="factor-card-value">__BIAS__%</span></div>
</div>
<div class="stats-bar">
  <div class="stats-red" style="width:33.3%"></div><div class="stats-green" style="width:33.3%"></div><div class="stats-yellow" style="width:33.3%"></div>
</div>
<div class="charts-area" id="charts-area">
  <div class="panel" id="panel-main" style="flex:55"><span class="panel-label">● K线 + EMA</span></div>
  <div class="panel" id="panel-vol" style="flex:10"><span class="panel-label">●成交量</span></div>
  <div class="panel" id="panel-macd" style="flex:20"><span class="panel-label">● MACD</span></div>
  <div class="panel" id="panel-bias" style="flex:15"><span class="panel-label">● BIAS</span></div>
  <div id="tooltip" class="tooltip" style="display:none"></div>
</div>
<div class="legend-bar"><div class="legend-item"><span class="legend-dot" style="background:#ff1744"></span>红灯</div><div class="legend-item"><span class="legend-dot" style="background:#00e676"></span>绿灯</div><div class="legend-item"><span class="legend-dot" style="background:#ffd600"></span>黄灯</div></div>

<script>
const candleData = __CANDLE__;
const volData = __VOL__;
const emaLongData = __EMA20_DATA__;
const emaShortData = __EMA5_DATA__;
const macdData = __MACD_DATA__;
const difData = __DIF_DATA__;
const deaData = __DEA_DATA__;
const biasData = __BIAS_DATA__;
const stateData = __STATE_DATA__;
const markers = __MARKERS__;

const stateMap = {}; stateData.forEach(s => { stateMap[s.time] = s.state; });
const stateColorMap = { red: '#ff1744', green: '#00e676', yellow: '#ffd600' };
const stateLabel = { red: '🔴 红灯(禁区)', green: '🟢 绿灯(主升)', yellow: '🟡 黄灯(震荡)' };

const commonOpts = (container, showTimeScale) => ({ width: container.clientWidth, height: container.clientHeight, layout: { background: { type: 'solid', color: '#131722' }, textColor: '#d1d4dc', fontSize: 11 }, grid: { vertLines: { color: '#1e2235' }, horzLines: { color: '#1e2235' } }, crosshair: { mode: LightweightCharts.CrosshairMode.Normal, vertLine: { color: '#787b8640', width: 1, style: 2, labelVisible: false }, horzLine: { color: '#787b8640', width: 1, style: 2 } }, rightPriceScale: { borderColor: '#1e2235', scaleMargins: { top: 0.1, bottom: 0.1 } }, timeScale: { borderColor: '#1e2235', visible: showTimeScale, timeVisible: false, rightOffset: 5 } }); 
const panelMain = document.getElementById('panel-main'); const panelVol = document.getElementById('panel-vol'); const panelMacd = document.getElementById('panel-macd'); const panelBias = document.getElementById('panel-bias'); const chartsArea = document.getElementById('charts-area');
const fixedH = document.querySelector('.header').offsetHeight + document.querySelector('.factor-cards').offsetHeight + document.querySelector('.stats-bar').offsetHeight + document.querySelector('.legend-bar').offsetHeight;
const availH = Math.max(400, window.innerHeight - fixedH);
chartsArea.style.height = availH + 'px'; chartsArea.style.flex = 'none';
panelMain.style.height = Math.floor(availH*0.55) + 'px';  panelVol.style.height = Math.floor(availH*0.1) + 'px'; panelMacd.style.height = Math.floor(availH*0.2) + 'px'; panelBias.style.height = (availH - Math.floor(availH*0.85)) + 'px';

const chart1 = LightweightCharts.createChart(panelMain, commonOpts(panelMain, false));
const chart2 = LightweightCharts.createChart(panelVol, commonOpts(panelVol, false));
const chart3 = LightweightCharts.createChart(panelMacd, commonOpts(panelMacd, false));
const chart4 = LightweightCharts.createChart(panelBias, commonOpts(panelBias, true));
const allCharts = [chart1, chart2, chart3, chart4];

const candleSeries = chart1.addCandlestickSeries({ upColor: '#26a69a', downColor: '#ef5350', borderUpColor: '#26a69a', borderDownColor: '#ef5350', wickUpColor: '#26a69a', wickDownColor: '#ef5350' });
candleSeries.setData(candleData); candleSeries.setMarkers(markers);
const emaLongSeries = chart1.addLineSeries({ color: '#2962ff', lineWidth: 2, lastValueVisible: false, priceLineVisible: false }); emaLongSeries.setData(emaLongData);
const emaShortSeries = chart1.addLineSeries({ color: '#f7a21b', lineWidth: 1, lastValueVisible: false, priceLineVisible: false }); emaShortSeries.setData(emaShortData);

const volSeries = chart2.addHistogramSeries({ priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false }); volSeries.setData(volData);

const macdSeries = chart3.addHistogramSeries({ priceFormat: { type: 'price', precision: 4, minMove: 0.0001 }, lastValueVisible: false, priceLineVisible: false }); macdSeries.setData(macdData);
const difSeries = chart3.addLineSeries({ color: '#2962ff', lineWidth: 1.5, lastValueVisible: false, priceLineVisible: false }); difSeries.setData(difData);
const deaSeries = chart3.addLineSeries({ color: '#ff6d00', lineWidth: 1.5, lastValueVisible: false, priceLineVisible: false }); deaSeries.setData(deaData);

const biasSeries = chart4.addLineSeries({ color: '#ba68c8', lineWidth: 2, priceFormat: { type: 'price', precision: 2, minMove: 0.01 }, lastValueVisible: false, priceLineVisible: false }); biasSeries.setData(biasData);
const biasUpperLine = chart4.addLineSeries({ color: 'rgba(239,83,80,0.3)', lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false }); biasUpperLine.setData(biasData.map(d => ({time: d.time, value: 5.0})));
const biasLowerLine = chart4.addLineSeries({ color: 'rgba(38,166,154,0.3)', lineWidth: 1, lineStyle: 2, lastValueVisible: false, priceLineVisible: false }); biasLowerLine.setData(biasData.map(d => ({time: d.time, value: -1.0})));
const biasZeroLine = chart4.addLineSeries({ color: 'rgba(42,46,57,0.6)', lineWidth: 1, lastValueVisible: false, priceLineVisible: false }); biasZeroLine.setData(biasData.map(d => ({time: d.time, value: 0})));

let isSyncing = false;
allCharts.forEach(c => { c.timeScale().subscribeVisibleLogicalRangeChange(range => { if (isSyncing || !range) return; isSyncing = true; allCharts.forEach(t => { if (t !== c) t.timeScale().setVisibleLogicalRange(range); }); isSyncing = false; }); });
chart1.subscribeCrosshairMove(param => {
  if (!param || !param.time || param.point === undefined) { tooltip.style.display = 'none'; allCharts.forEach(t => t.clearCrosshairPosition()); return; }
  allCharts.forEach(t => { if (t !== chart1) t.setCrosshairPosition(param.point.x, param.point.y, chart1.timeScale()); });
  const t = param.time; const candle = param.seriesData.get(candleSeries); if (!candle) { tooltip.style.display = 'none'; return; }
  const state = stateMap[t] || '-'; const stCol = stateColorMap[state] || '#6b7280';
  const findVal = (arr) => { const r = arr.find(x => x.time === t); return r ? r.value : null; };
  tooltip.innerHTML = `<div class="tooltip-title">${t}</div><div class="tooltip-section"><div class="tooltip-row"><span class="tooltip-label">收</span><span class="tooltip-val">${candle.close.toFixed(2)}</span></div><div class="tooltip-row"><span class="tooltip-label">状态</span><span class="tooltip-val" style="color:${stCol}">${stateLabel[state] || '-'}</span></div></div>`;
  tooltip.style.display = 'block';
});
window.addEventListener('resize', () => { allCharts.forEach((c, i) => { const el = [panelMain, panelVol, panelMacd, panelBias][i]; c.applyOptions({ width: el.clientWidth, height: el.clientHeight }); }); });
allCharts.forEach(c => c.timeScale().fitContent());
</script>
</body>
</html>"""

    # Replace values
    html = html_template.replace("__NAME__", name)
    html = html.replace("__CODE__", code.split('.')[1] if '.' in code else code)
    html = html.replace("__PRICE__", f"{last_row['close']:.2f}")
    html = html.replace("__CHGCLS__", chg_cls)
    html = html.replace("__CHGPCT__", f"{chg_str_sign}{chg:.2f}")
    
    # States calculation (simplified badge)
    state_html = '<span class="state-badge state-green">🟢 绿灯(主升)</span>'
    if last_row['state'] == 'red':
        state_html = '<span class="state-badge state-red">🔴 红灯(禁区)</span>'
    elif last_row['state'] == 'yellow':
        state_html = '<span class="state-badge state-yellow">🟡 黄灯(震荡)</span>'
        
    html = html.replace("__STATE_HTML__", state_html)
    html = html.replace("__EMA20__", f"{last_row['ema20']:.2f}")
    html = html.replace("__EMA5__", f"{last_row['ema5']:.2f}")
    html = html.replace("__MACD__", f"{last_row['macd']:.4f}")
    html = html.replace("__BIAS__", f"{last_row['bias']:.2f}")
    
    # JSON arrays replace
    html = html.replace("__CANDLE__", json.dumps(candle_data))
    html = html.replace("__VOL__", json.dumps(vol_data))
    html = html.replace("__EMA20_DATA__", json.dumps(ema_long_data))
    html = html.replace("__EMA5_DATA__", json.dumps(ema_short_data))
    html = html.replace("__MACD_DATA__", json.dumps(macd_data))
    html = html.replace("__DIF_DATA__", json.dumps(dif_data))
    html = html.replace("__DEA_DATA__", json.dumps(dea_data))
    html = html.replace("__BIAS_DATA__", json.dumps(bias_data))
    html = html.replace("__STATE_DATA__", json.dumps(state_data))
    html = html.replace("__MARKERS__", json.dumps(markers))
    
    filename = f"{name}_TradingView分析.html"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(html)
        
    print(f"Generated {filename}")

if __name__ == "__main__":
    code = 'sz.000533'
    name = '顺钠股份'
    df = fetch_stock_data(code, days=365)
    df = calc_indicators(df)
    build_html(df, code, name)
