"""升级 mobile_app/index.html 增加每日最新数据拉取与历史数据查看功能"""

import re
from pathlib import Path

INDEX_PATH = Path("mobile_app/index.html")

def patch_index():
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. 确保 history_index.js 引入
    if "history_index.js" not in content:
        content = content.replace(
            '<script src="bundle.js"></script>',
            '<script src="bundle.js"></script>\n  <script src="history_index.js"></script>'
        )

    # 2. 替换 header 为带日期切换和状态药丸的 header
    header_old_regex = r'<header class="header">.*?<\/header>'
    header_new = '''<header class="header">
    <div style="display:flex; flex-direction:column; gap:4px;">
      <div style="display:flex; align-items:center; gap:8px;">
        <div class="header-title" id="page-heading">大盘情绪周期</div>
        <div class="date-selector-pill" onclick="openDateModal()" title="点击切换历史复盘交易日">
          <span>📅</span>
          <span id="current-date-label">最新交易日</span>
          <span style="font-size:9px; opacity:0.7;">▾</span>
        </div>
      </div>
      <div style="display:flex; align-items:center; gap:8px;">
        <span class="status-pill status-online" id="sync-status-pill" onclick="openSettingsModal()">● 云端已连接</span>
        <div class="header-sub" id="data-meta">正在加载实时行情...</div>
      </div>
    </div>
    <div style="display:flex; align-items:center; gap:6px;">
      <button class="btn-refresh" onclick="openDateModal()" title="切换历史日期" style="padding:6px 8px;">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
        <span>历史</span>
      </button>
      <button class="btn-refresh" id="btn-refresh-data" onclick="manualRefresh()" title="刷新行情" style="padding:6px 8px;">
        <svg id="refresh-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.3"/></svg>
        <span>刷新</span>
      </button>
      <button class="btn-refresh" onclick="openSettingsModal()" title="数据源与设置" style="padding:6px 8px; color:var(--muted);">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
      </button>
    </div>
  </header>

  <!-- 历史复盘提示条 -->
  <div id="hist-banner" class="hist-banner" style="display:none;">
    <div style="display:flex; align-items:center; gap:6px;">
      <span>📜 当前复盘:</span>
      <b id="hist-banner-date" style="color:#FFF;">--</b>
      <span id="hist-banner-tag" class="badge badge-emerald" style="margin-left:4px;">极度冰点期</span>
    </div>
    <button class="btn-hist-back" onclick="switchToToday()">返回今日最新 ➔</button>
  </div>'''
    content = re.sub(header_old_regex, header_new, content, flags=re.DOTALL)

    # 3. 在 pane-mood 中加入「近15日情绪周期走势」卡片
    mood_chart_html = '''
      <!-- 情绪温度计 -->'''
    
    chart_card = '''      <!-- 情绪温度计 -->
      <div class="card">
        <div class="card-title">
          <span>情绪周期历史演进 (近15日走势)</span>
          <span style="font-size:11px; color:var(--sky); cursor:pointer;" onclick="openDateModal()">完整 31 日归档 ›</span>
        </div>
        <div id="mood-history-chart" style="width:100%; min-height:85px; overflow-x:auto;">
          <!-- SVG 动态绘制历史周期波峰波谷曲线 -->
        </div>
        <div style="font-size:10px; color:var(--muted); margin-top:6px; display:flex; justify-content:space-between;">
          <span>← 往期冰点/沸点</span>
          <span>点击上方节点可直接下钻复盘 →</span>
        </div>
      </div>
'''
    if 'id="mood-history-chart"' not in content:
        content = content.replace('      <!-- 情绪温度计 -->', chart_card, 1)

    # 4. 在 </body> 之前加入弹层 HTML
    modals_html = '''
  <!-- 历史交易日选择弹层 -->
  <div class="modal-mask" id="modal-date-mask" onclick="closeDateModal()"></div>
  <div class="modal-sheet" id="modal-date-sheet">
    <div class="modal-h">
      <span class="modal-t">📅 切换复盘交易日</span>
      <button class="modal-x" onclick="closeDateModal()">×</button>
    </div>
    <div class="modal-b">
      <!-- 快捷标签 -->
      <div style="font-size:12px; color:var(--muted); margin-bottom:8px;">快捷选择:</div>
      <div class="date-quick-chips" id="date-quick-chips"></div>

      <!-- 日历控件 -->
      <div style="display:flex; align-items:center; gap:8px; margin:12px 0; background:var(--card-sub); padding:8px 12px; border-radius:10px; border:1px solid var(--border);">
        <span style="font-size:12px; color:var(--muted);">指定日期:</span>
        <input type="date" id="calendar-date-input" style="background:transparent; border:none; color:#FFF; font-size:13px; font-family:inherit; flex:1;" onchange="onCalendarDatePicked(this.value)">
      </div>

      <!-- 历史交易日归档列表 -->
      <div style="font-size:12px; color:var(--muted); margin-bottom:8px; display:flex; justify-content:space-between;">
        <span>历史交易日归档 (<span id="hist-total-count">31</span> 个交易日)</span>
        <span style="font-size:11px; color:var(--sky);">点击直接切换</span>
      </div>
      <div id="hist-date-list" style="display:flex; flex-direction:column; gap:8px;"></div>
    </div>
  </div>

  <!-- 数据源与网络设置弹层 -->
  <div class="modal-mask" id="modal-settings-mask" onclick="closeSettingsModal()"></div>
  <div class="modal-sheet" id="modal-settings-sheet">
    <div class="modal-h">
      <span class="modal-t">⚙️ 数据源与网络同步</span>
      <button class="modal-x" onclick="closeSettingsModal()">×</button>
    </div>
    <div class="modal-b">
      <!-- 当前状态 -->
      <div class="card" style="margin-bottom:12px; padding:12px;">
        <div style="font-size:12px; color:var(--muted); margin-bottom:8px;">当前数据状态</div>
        <div style="font-size:13px; color:#FFF; line-height:1.8;">
          <div>交易归档日期: <b id="cfg-date-show" style="color:var(--sky);">--</b></div>
          <div>最后同步时间: <span id="cfg-time-show" style="color:var(--muted);">--</span></div>
          <div>当前数据通道: <span id="cfg-channel-show" class="badge badge-emerald">云端同步</span></div>
        </div>
      </div>

      <!-- 自定义服务器配置 -->
      <div class="card" style="margin-bottom:12px; padding:12px;">
        <div style="font-size:12px; color:var(--muted); margin-bottom:8px;">自定义数据服务器 (可选)</div>
        <div style="display:flex; gap:8px;">
          <input type="text" id="custom-server-input" placeholder="例如 http://192.168.1.7:8088" style="flex:1; background:var(--card-sub); border:1px solid var(--border); border-radius:8px; padding:8px 10px; color:#FFF; font-size:12px;">
          <button onclick="saveCustomServer()" style="background:var(--sky); color:#0B0E14; border:none; border-radius:8px; padding:0 12px; font-size:12px; font-weight:700; cursor:pointer;">保存</button>
        </div>
        <div style="font-size:10px; color:var(--muted); margin-top:6px;">
          若电脑运行了本地服务，可填入电脑局域网 IP；留空则默认使用云端自动拉取。
        </div>
      </div>

      <!-- 操作按钮集 -->
      <div style="display:flex; flex-direction:column; gap:8px;">
        <button class="btn-action-primary" onclick="syncFromCloud()">
          <span>🚀 从云端拉取今日最新数据</span>
        </button>
        <button class="btn-action-secondary" onclick="syncDirectLive()">
          <span>⚡ 直连实时行情源刷新 (财联社/开盘啦)</span>
        </button>
        <button class="btn-action-outline" onclick="clearCacheAndReload()">
          <span>🧹 清空本地缓存并重置</span>
        </button>
      </div>

      <div id="sync-log-box" style="margin-top:12px; font-size:11px; color:var(--muted); background:var(--card-sub); padding:10px; border-radius:8px; border:1px solid var(--border); max-height:90px; overflow-y:auto; line-height:1.5;">
        准备就绪。点击上方按钮可主动同步最新行情或测试网络。
      </div>
    </div>
  </div>
'''
    if 'id="modal-date-sheet"' not in content:
        content = content.replace('</body>', modals_html + '\n</body>')

    # 5. 更新 JavaScript 数据管理逻辑
    js_logic = '''
    // ================= 全局状态与网络管理 =================
    let appData = null;
    let currentDate = null;       // 当前查看的交易日 (null 代表最新)
    let latestDataDate = null;    // 系统最新交易日
    let historyIndex = window.__HISTORY_INDEX__ || [];
    let customServer = localStorage.getItem('quant_custom_server') || '';

    // 初始化时从本地缓存尝试加载
    try {
      const cachedLatest = localStorage.getItem('quant_bundle_latest');
      if (cachedLatest) {
        const parsed = JSON.parse(cachedLatest);
        if (parsed && parsed.meta && parsed.meta.date) {
          appData = parsed;
          latestDataDate = parsed.meta.date;
        }
      }
    } catch (e) {}

    // Tab 切换逻辑
    function switchTab(key, title) {
      document.getElementById('page-heading').textContent = title;
      document.querySelectorAll('.tab-pane').forEach(p => {
        p.classList.toggle('active', p.id === 'pane-' + key);
      });
      document.querySelectorAll('.tab-item').forEach(t => {
        t.classList.toggle('active', t.getAttribute('data-key') === key);
      });
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    // ================= 历史索引加载与渲染 =================
    async function loadHistoryIndex() {
      // 候选端点
      const endpoints = [
        './history_index.json',
        (customServer ? customServer + '/api/history_index.json' : null),
        (customServer ? customServer + '/mobile_app/history_index.json' : null),
        'https://mengkai666.github.io/quant_factor_tutorial/api/history_index.json',
        'https://raw.githubusercontent.com/mengkai666/quant_factor_tutorial/gh-pages/api/history_index.json'
      ].filter(Boolean);

      for (const url of endpoints) {
        try {
          const res = await fetch(url + '?t=' + Date.now(), { cache: 'no-cache' });
          if (res.ok) {
            historyIndex = await res.json();
            break;
          }
        } catch (e) {}
      }

      if (historyIndex && historyIndex.length) {
        renderMoodHistoryChart(historyIndex);
        renderDateModalList(historyIndex);
      }
    }

    // 绘制情绪周期近期演进 SVG 图表
    function renderMoodHistoryChart(list) {
      const box = document.getElementById('mood-history-chart');
      if (!box || !list || !list.length) return;
      
      // 取最近 15 个交易日并按时间顺序排列
      const recent = list.slice(0, 15).reverse();
      const w = Math.max(box.clientWidth || 340, recent.length * 44);
      const h = 75;
      const padX = 22;
      const padY = 16;
      const innerW = w - padX * 2;
      const innerH = h - padY * 2;

      // 坐标映射
      const pts = recent.map((item, idx) => {
        const x = padX + (idx / (recent.length - 1)) * innerW;
        const normY = Math.max(0, Math.min(100, item.degree));
        const y = padY + (1 - normY / 100) * innerH;
        return { x, y, item };
      });

      // 生成平滑折线路径
      let pathD = `M ${pts[0].x} ${pts[0].y}`;
      for (let i = 1; i < pts.length; i++) {
        const prev = pts[i - 1];
        const cur = pts[i];
        const cx = (prev.x + cur.x) / 2;
        pathD += ` C ${cx} ${prev.y}, ${cx} ${cur.y}, ${cur.x} ${cur.y}`;
      }

      const activeDate = currentDate || (appData && appData.meta ? appData.meta.date : '');

      let nodesSvg = '';
      pts.forEach(p => {
        const d = p.item;
        const isCurrent = d.date === activeDate;
        const color = d.degree >= 70 ? 'var(--rose)' : (d.degree >= 50 ? 'var(--amber)' : (d.degree >= 30 ? 'var(--sky)' : 'var(--emerald)'));
        const dateShort = d.date.slice(5);

        nodesSvg += `
          <g class="chart-node" style="cursor:pointer;" onclick="switchToDate('${d.date}')">
            ${isCurrent ? `<circle cx="${p.x}" cy="${p.y}" r="8" fill="none" stroke="${color}" stroke-width="2" opacity="0.6"/>` : ''}
            <circle cx="${p.x}" cy="${p.y}" r="${isCurrent ? '5' : '3.5'}" fill="${color}" stroke="#0B0E14" stroke-width="1.5"/>
            <text x="${p.x}" y="${p.y - 8}" text-anchor="middle" font-size="9" fill="${color}" font-weight="700" font-family="monospace">${d.degree}</text>
            <text x="${p.x}" y="${h - 2}" text-anchor="middle" font-size="8.5" fill="${isCurrent ? '#FFF' : 'var(--muted)'}" font-weight="${isCurrent ? '700' : '400'}">${dateShort}</text>
          </g>
        `;
      });

      box.innerHTML = `
        <svg width="${w}" height="${h}" style="display:block; overflow:visible;">
          <defs>
            <linearGradient id="chartGrad" x1="0%" y1="0%" x2="0%" y2="100%">
              <stop offset="0%" stop-color="#F43F5E" stop-opacity="0.3"/>
              <stop offset="50%" stop-color="#F59E0B" stop-opacity="0.15"/>
              <stop offset="100%" stop-color="#10B981" stop-opacity="0.05"/>
            </linearGradient>
          </defs>
          <line x1="${padX}" y1="${padY + innerH * 0.5}" x2="${w - padX}" y2="${padY + innerH * 0.5}" stroke="rgba(255,255,255,0.08)" stroke-dasharray="3 3"/>
          <path d="${pathD}" fill="none" stroke="rgba(255,255,255,0.3)" stroke-width="1.8"/>
          ${nodesSvg}
        </svg>
      `;
    }

    // ================= 多通道数据拉取引擎 =================
    async function loadData(forceRemote = false, targetDate = null) {
      const icon = document.getElementById('refresh-icon');
      if (icon) icon.classList.add('spin');
      
      const statusPill = document.getElementById('sync-status-pill');

      // 1. 若请求的是历史交易日
      if (targetDate) {
        currentDate = targetDate;
        updateDateHeaders(targetDate, false);

        // 尝试从内存或 localStorage 恢复该日
        const cacheKey = 'quant_bundle_' + targetDate;
        const localCached = localStorage.getItem(cacheKey);
        if (localCached) {
          try {
            appData = JSON.parse(localCached);
            renderAll(appData);
            if (statusPill) {
              statusPill.textContent = '● 历史本地';
              statusPill.className = 'status-pill status-cached';
            }
          } catch (e) {}
        }

        // 候选历史端点
        const histCandidates = [
          `./history/bundle_${targetDate}.json`,
          (customServer ? `${customServer}/api/history/bundle_${targetDate}.json` : null),
          (customServer ? `${customServer}/mobile_app/history/bundle_${targetDate}.json` : null),
          `https://mengkai666.github.io/quant_factor_tutorial/api/history/bundle_${targetDate}.json`,
          `https://raw.githubusercontent.com/mengkai666/quant_factor_tutorial/gh-pages/api/history/bundle_${targetDate}.json`
        ].filter(Boolean);

        for (const url of histCandidates) {
          try {
            const res = await fetch(url + '?t=' + Date.now(), { cache: 'no-cache' });
            if (res.ok) {
              appData = await res.json();
              localStorage.setItem(cacheKey, JSON.stringify(appData));
              renderAll(appData);
              if (statusPill) {
                statusPill.textContent = '● 历史归档';
                statusPill.className = 'status-pill status-online';
              }
              break;
            }
          } catch (e) {}
        }

        if (icon) setTimeout(() => icon.classList.remove('spin'), 400);
        return;
      }

      // 2. 加载今日/最新数据
      currentDate = null; // 切回最新状态
      
      // 离线首选渲染
      if (!appData && window.__INITIAL_BUNDLE__) {
        appData = window.__INITIAL_BUNDLE__;
        latestDataDate = appData.meta ? appData.meta.date : null;
        renderAll(appData);
      }

      // 候选最新数据端点列表 (云端优先 -> 自定义优先 -> 局域网 -> 离线本地)
      const candidates = [
        (customServer ? `${customServer}/api/bundle.json` : null),
        (customServer ? `${customServer}/mobile_app/bundle.json` : null),
        'https://mengkai666.github.io/quant_factor_tutorial/api/bundle.json',
        'https://cdn.jsdelivr.net/gh/mengkai666/quant_factor_tutorial@gh-pages/api/bundle.json',
        'https://raw.githubusercontent.com/mengkai666/quant_factor_tutorial/gh-pages/api/bundle.json',
        'http://192.168.1.7:8088/mobile_app/bundle.json',
        'http://10.0.2.2:8088/mobile_app/bundle.json',
        './bundle.json'
      ].filter(Boolean);

      let fetched = false;
      for (const url of candidates) {
        try {
          const controller = new AbortController();
          const timeoutId = setTimeout(() => controller.abort(), 4000);
          const res = await fetch(url + '?t=' + Date.now(), {
            cache: 'no-cache',
            signal: controller.signal
          });
          clearTimeout(timeoutId);

          if (res.ok) {
            const fresh = await res.json();
            if (fresh && fresh.meta) {
              appData = fresh;
              latestDataDate = fresh.meta.date;
              localStorage.setItem('quant_bundle_latest', JSON.stringify(fresh));
              renderAll(appData);
              fetched = true;

              if (statusPill) {
                if (url.includes('127.0.0.1') || url.includes('192.168') || url.includes('10.0.2.2')) {
                  statusPill.textContent = '● 局域网直连';
                  statusPill.className = 'status-pill status-direct';
                } else if (url.includes('github') || url.includes('jsdelivr')) {
                  statusPill.textContent = '● 云端已同步';
                  statusPill.className = 'status-pill status-online';
                } else {
                  statusPill.textContent = '● 离线内置';
                  statusPill.className = 'status-pill status-cached';
                }
              }
              break;
            }
          }
        } catch (e) {}
      }

      // 若所有 bundle 端点均无法访问，尝试直连财联社实时接口降级补全
      if (!fetched && (!appData || forceRemote)) {
        await syncDirectLive(true);
      }

      if (icon) setTimeout(() => icon.classList.remove('spin'), 400);
    }

    // 手动刷新按钮入口
    function manualRefresh() {
      loadData(true, currentDate);
    }

    // 直连财联社与开盘啦实时源更新
    async function syncDirectLive(silent = false) {
      const logBox = document.getElementById('sync-log-box');
      const statusPill = document.getElementById('sync-status-pill');
      if (logBox) logBox.innerHTML = '正在直连财联社行情终端协议 (x-quote.cls.cn)...<br>';

      try {
        const url = 'https://x-quote.cls.cn/v2/quote/a/stock/emotion?app=cailianpress&os=android&sv=8.5.4&sign=5b4c4c6a3b9db1dd279a0d713111c4d0';
        const res = await fetch(url, { cache: 'no-cache' });
        if (res.ok) {
          const json = await res.json();
          if (json.code === 200 && json.data) {
            const d = json.data;
            if (!appData) appData = { meta: {}, tab_mood: {}, tab_crowding: {}, tab_focus: [], tab_strategy: {} };
            
            // 实时更新情绪指标
            const degree = Number(d.market_degree || 50);
            let statusText = '中性震荡期';
            let colorTag = 'amber';
            if (degree >= 70) { statusText = '亢奋共振期'; colorTag = 'rose'; }
            else if (degree >= 50) { statusText = '活跃分歧期'; colorTag = 'amber'; }
            else if (degree >= 30) { statusText = '混沌震荡期'; colorTag = 'blue'; }
            else { statusText = '极度冰点期'; colorTag = 'emerald'; }

            appData.tab_mood.degree = degree;
            appData.tab_mood.status_text = statusText;
            appData.tab_mood.color_tag = colorTag;
            if (d.turnover) appData.tab_mood.turnover = (Number(d.turnover) / 1e12).toFixed(2) + '万亿';
            if (d.up_num !== undefined) appData.tab_mood.up_count = Number(d.up_num);
            if (d.down_num !== undefined) appData.tab_mood.down_count = Number(d.down_num);
            if (d.limit_up_num !== undefined) appData.tab_mood.zt_count = Number(d.limit_up_num);
            if (d.limit_down_num !== undefined) appData.tab_mood.dt_count = Number(d.limit_down_num);

            renderAll(appData);

            if (statusPill) {
              statusPill.textContent = '⚡ 直连实时';
              statusPill.className = 'status-pill status-direct';
            }
            if (logBox) logBox.innerHTML += `<span style="color:var(--emerald);">✔ 直连成功！获取实时度数: ${degree} 度 (${statusText})，两市成交: ${appData.tab_mood.turnover}</span><br>`;
            return true;
          }
        }
      } catch (err) {
        if (logBox) logBox.innerHTML += `<span style="color:var(--rose);">✘ 直连失败: ${err.message}</span><br>`;
      }
      return false;
    }

    // ================= 日期切换与弹层逻辑 =================
    function updateDateHeaders(date, isLatest) {
      const label = document.getElementById('current-date-label');
      const histBanner = document.getElementById('hist-banner');
      const bannerDate = document.getElementById('hist-banner-date');
      const bannerTag = document.getElementById('hist-banner-tag');

      if (isLatest || !date) {
        if (label) label.textContent = (latestDataDate || '今日') + ' (最新)';
        if (histBanner) histBanner.style.display = 'none';
      } else {
        if (label) label.textContent = date + ' (历史)';
        if (histBanner) histBanner.style.display = 'flex';
        if (bannerDate) bannerDate.textContent = date;
        if (bannerTag && appData && appData.tab_mood) {
          bannerTag.textContent = appData.tab_mood.status_text || '历史复盘';
          bannerTag.className = `badge badge-${appData.tab_mood.color_tag || 'emerald'}`;
        }
      }

      // 重绘走势图以更新高亮节点
      if (historyIndex && historyIndex.length) {
        renderMoodHistoryChart(historyIndex);
      }
    }

    function openDateModal() {
      document.getElementById('modal-date-mask').classList.add('on');
      document.getElementById('modal-date-sheet').classList.add('on');
      renderDateModalList(historyIndex);
    }

    function closeDateModal() {
      document.getElementById('modal-date-mask').classList.remove('on');
      document.getElementById('modal-date-sheet').classList.remove('on');
    }

    function switchToDate(dateStr) {
      closeDateModal();
      loadData(false, dateStr);
    }

    function switchToToday() {
      currentDate = null;
      loadData(false, null);
    }

    function onCalendarDatePicked(val) {
      if (val) switchToDate(val);
    }

    function renderDateModalList(list) {
      if (!list || !list.length) return;
      const countEl = document.getElementById('hist-total-count');
      if (countEl) countEl.textContent = list.length;

      const chipsBox = document.getElementById('date-quick-chips');
      const listBox = document.getElementById('hist-date-list');
      const activeDate = currentDate || (appData && appData.meta ? appData.meta.date : '');

      // 1. 快捷 Chips
      if (chipsBox) {
        chipsBox.innerHTML = list.slice(0, 8).map(item => {
          const isActive = item.date === activeDate;
          const label = item.is_latest ? `${item.date.slice(5)} (最新)` : item.date.slice(5);
          return `
            <div class="chip-btn ${isActive ? 'active' : ''}" onclick="switchToDate('${item.date}')">
              <span>${label}</span>
              <span style="font-size:10px; opacity:0.8;">${item.degree}度 · ${item.status.split('·')[0].slice(0, 4)}</span>
            </div>
          `;
        }).join('');
      }

      // 2. 详细列表
      if (listBox) {
        listBox.innerHTML = list.map(item => {
          const isActive = item.date === activeDate;
          const colorClass = item.degree >= 70 ? 'badge-rose' : (item.degree >= 50 ? 'badge-amber' : (item.degree >= 30 ? 'badge-blue' : 'badge-emerald'));
          return `
            <div class="hist-item-row ${isActive ? 'active' : ''}" onclick="switchToDate('${item.date}')">
              <div>
                <div style="font-size:13px; font-weight:700; color:#FFF; display:flex; align-items:center; gap:6px;">
                  <span>${item.date}</span>
                  ${item.is_latest ? '<span class="badge badge-rose">最新</span>' : ''}
                  <span class="badge ${colorClass}">${item.status}</span>
                </div>
                <div style="font-size:11px; color:var(--muted); margin-top:3px;">
                  成交 ${item.turnover || '--'} · 涨停 ${item.zt_count || 0}只 · 跌停 ${item.dt_count || 0}只 · 建议 ${item.position || '--'}
                </div>
              </div>
              <div style="text-align:right;">
                <div style="font-size:16px; font-weight:800; color:#FFF; font-family:monospace;">${item.degree}<small style="font-size:10px; font-weight:400; color:var(--muted);">度</small></div>
                <div style="font-size:10.5px; color:var(--sky);">${item.top_stock || '--'} (${item.max_height || '--'})</div>
              </div>
            </div>
          `;
        }).join('');
      }
    }

    // ================= 设置与网络管理弹层 =================
    function openSettingsModal() {
      document.getElementById('modal-settings-mask').classList.add('on');
      document.getElementById('modal-settings-sheet').classList.add('on');
      
      const serverInput = document.getElementById('custom-server-input');
      if (serverInput) serverInput.value = customServer;

      if (appData && appData.meta) {
        document.getElementById('cfg-date-show').textContent = appData.meta.date || '--';
        document.getElementById('cfg-time-show').textContent = (appData.meta.updated_at || '--').split(' ')[1] || '--';
      }
    }

    function closeSettingsModal() {
      document.getElementById('modal-settings-mask').classList.remove('on');
      document.getElementById('modal-settings-sheet').classList.remove('on');
    }

    function saveCustomServer() {
      const val = document.getElementById('custom-server-input').value.trim();
      customServer = val;
      localStorage.setItem('quant_custom_server', val);
      const logBox = document.getElementById('sync-log-box');
      if (logBox) logBox.innerHTML += `<span style="color:var(--emerald);">✔ 服务器已保存: ${val || '已清空，使用云端'}</span><br>`;
      loadData(true);
    }

    async function syncFromCloud() {
      const logBox = document.getElementById('sync-log-box');
      if (logBox) logBox.innerHTML = '正在连接云端 GitHub Pages API...<br>';
      await loadData(true);
      if (logBox) logBox.innerHTML += '<span style="color:var(--emerald);">✔ 云端数据拉取完成！</span><br>';
    }

    function clearCacheAndReload() {
      localStorage.removeItem('quant_bundle_latest');
      Object.keys(localStorage).forEach(k => {
        if (k.startsWith('quant_bundle_')) localStorage.removeItem(k);
      });
      const logBox = document.getElementById('sync-log-box');
      if (logBox) logBox.innerHTML += '<span style="color:var(--amber);">✔ 本地所有历史与行情缓存已清空，正在重载...</span><br>';
      setTimeout(() => location.reload(), 500);
    }
'''

    # 替换原本的 loadData 及相关脚本头部
    old_script_head = '''  <script>
    let appData = null;

    // Tab 切换逻辑（按 key 匹配，顺序解耦）
    function switchTab(key, title) {
      document.getElementById('page-heading').textContent = title;
      document.querySelectorAll('.tab-pane').forEach(p => {
        p.classList.toggle('active', p.id === 'pane-' + key);
      });
      document.querySelectorAll('.tab-item').forEach(t => {
        t.classList.toggle('active', t.getAttribute('data-key') === key);
      });
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    // 数据加载与渲染
    async function loadData() {'''

    if old_script_head in content:
        # 找到 loadData 函数结尾并整体替换
        end_idx = content.find('function renderAll(data) {')
        if end_idx != -1:
            head_idx = content.find('  <script>')
            content = content[:head_idx] + '  <script>\n' + js_logic + '\n\n    ' + content[end_idx:]

    # 在 renderAll 顶部注入日期标题与走势图更新
    render_all_old = 'function renderAll(data) {'
    render_all_new = '''function renderAll(data) {
      if (!data || !data.meta) return;
      updateDateHeaders(data.meta.date, !currentDate || currentDate === data.meta.date);'''
    content = content.replace(render_all_old, render_all_new, 1)

    # 在 DOMContentLoaded 时一并加载历史索引
    dom_load_old = "window.addEventListener('DOMContentLoaded', loadData);"
    dom_load_new = "window.addEventListener('DOMContentLoaded', () => { loadHistoryIndex(); loadData(); });"
    content = content.replace(dom_load_old, dom_load_new, 1)

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        f.write(content)

    print("Successfully patched mobile_app/index.html!")

if __name__ == "__main__":
    patch_index()
