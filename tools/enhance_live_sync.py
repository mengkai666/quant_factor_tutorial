"""增强移动端多通道实时刷新与自选股动态行情 (enhance_live_sync.py)

解决问题：
1. 修复右上角【刷新】按钮重新加载本地离线旧包导致实时数据被覆盖的问题。
2. 升级直连实时行情引擎：同时抓取财联社大盘情绪 (x-quote.cls.cn) 与腾讯金融实时个股行情 (qt.gtimg.cn)。
3. 动态更新系统日期与时间戳为此时此刻真实时间，并在设置面板与首页顶部同步呈现。
4. 剔除测试假股票「正常股份 (sz000001)」，替换为真实标的 (出版传媒 sh601999)。
5. 优化自选股卡片：在卡片顶部醒目展示实时现价与分时涨跌幅 (+X.XX% / -X.XX%)。
6. 提供轻量浮动 Toast 反馈，点击刷新即刻有明确感知。
"""

import os
import re
import json
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def patch_bundle_json():
    print("1/4 清理测试标的并规范化 mobile_app/bundle.json...")
    bundle_path = PROJECT_ROOT / "mobile_app" / "bundle.json"
    with open(bundle_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 替换或剔除 正常股份
    new_focus = []
    for s in data.get("tab_focus", []):
        if s.get("name") == "正常股份" or s.get("code") == "sz000001":
            # 替换为出版传媒
            s["name"] = "出版传媒"
            s["code"] = "sh601999"
            s["sector"] = "AI应用"
            s["pool_type"] = "【空间博弈池】"
            s["entry_rule"] = "AI应用反抽先锋。若开盘放量换手且板块承接极强，可跟随打板；切忌加速缩量秒板。"
            s["defense_rule"] = "跌破分时均线或破5日线止损。"
        new_focus.append(s)

    data["tab_focus"] = new_focus

    # 更新元数据
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    if "meta" not in data: data["meta"] = {}
    data["meta"]["date"] = today_str
    data["meta"]["updated_at"] = f"{today_str} {time_str}"

    with open(bundle_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # 同步回写 mobile_app/bundle.js
    bundle_js_path = PROJECT_ROOT / "mobile_app" / "bundle.js"
    with open(bundle_js_path, "w", encoding="utf-8") as f:
        f.write("window.__INITIAL_BUNDLE__ = " + json.dumps(data, ensure_ascii=False) + ";\n")

    # 同步更新根目录 focus_pool.csv
    csv_path = PROJECT_ROOT / "focus_pool.csv"
    csv_content = """股票,代码,板块,策略池,入场条件,防守位,code,name,market,tradeable
出版传媒,sh601999,AI应用,【空间博弈池】,AI应用反抽先锋。若开盘放量换手且板块承接极强，可跟随打板；切忌加速缩量秒板。,跌破分时均线或破5日线止损,sh601999,出版传媒,sh,True
吉大正元,sz003029,AI算力,【4连板梯队】,连板高标独苗。首选分时平开弱转强放量换手承接，谨防无量一字秒开坑杀。,5日均线或前一日涨停价为防守位,sz003029,吉大正元,sz,True
大业股份,sh603278,机器人,【3连板梯队】,机器人高度核心。开盘观察承接力度，放量分歧转强跟随，破均线离场。,分时均线破位止损,sh603278,大业股份,sh,True
"""
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write(csv_content)

    print(" -> bundle.json / bundle.js / focus_pool.csv 更新完毕！")

def patch_index_html():
    print("2/4 升级 mobile_app/index.html 实时直连与刷新架构...")
    index_path = PROJECT_ROOT / "mobile_app" / "index.html"
    with open(index_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. 替换 manualRefresh 与 syncDirectLive 与 syncFromCloud
    old_sync_start = content.find("    // 手动刷新按钮入口")
    if old_sync_start == -1:
        old_sync_start = content.find("function manualRefresh()")
    
    old_sync_end = content.find("    // ================= 日期切换与弹层逻辑 =================")

    new_sync_code = '''    // ==========================================
    // 盘中实时刷新引擎 (直连财联社大盘 + 腾讯个股实时行情)
    // ==========================================
    function showToast(msg) {
      let t = document.getElementById('app-floating-toast');
      if (!t) {
        t = document.createElement('div');
        t.id = 'app-floating-toast';
        t.style.cssText = 'position:fixed; top:24px; left:50%; transform:translateX(-50%) translateY(-10px); background:rgba(20,24,34,0.96); color:#F0F6FC; border:1px solid rgba(56,189,248,0.5); border-radius:24px; padding:8px 20px; font-size:13px; font-weight:700; z-index:999999; box-shadow:0 8px 32px rgba(0,0,0,0.75); pointer-events:none; transition:opacity 0.25s, transform 0.25s; opacity:0; display:flex; align-items:center; gap:8px;';
        document.body.appendChild(t);
      }
      t.innerHTML = msg;
      t.style.opacity = '1';
      t.style.transform = 'translateX(-50%) translateY(0)';
      clearTimeout(t._timer);
      t._timer = setTimeout(() => {
        t.style.opacity = '0';
        t.style.transform = 'translateX(-50%) translateY(-10px)';
      }, 2500);
    }

    // 手动刷新按钮入口（顶部 🔄 按钮）
    async function manualRefresh() {
      const icon = document.getElementById('refresh-icon');
      if (icon) icon.classList.add('spin');

      // 若当前处于历史某日复盘，刷新该日归档数据
      if (currentDate) {
        await loadData(true, currentDate);
        if (icon) setTimeout(() => icon.classList.remove('spin'), 400);
        showToast(`✔ 历史归档重载完成 (${currentDate})`);
        return;
      }

      // 若查看今日最新，优先且强制直连实时行情协议（财联社宏观情绪 + 腾讯分时股价）
      showToast('⚡ 正在直连金融行情终端同步今日最新数据...');
      const ok = await syncDirectLive(false);
      if (!ok) {
        // 接口异常时尝试拉取本地或云端
        await loadData(true, null);
      }
      if (icon) setTimeout(() => icon.classList.remove('spin'), 500);
    }

    // 直连财联社与腾讯实时金融行情源全量更新
    async function syncDirectLive(silent = false) {
      const logBox = document.getElementById('sync-log-box');
      const statusPill = document.getElementById('sync-status-pill');
      if (logBox) logBox.innerHTML = '正在直连财联社行情终端 (x-quote.cls.cn)...<br>';

      const now = new Date();
      const pad = n => String(n).padStart(2, '0');
      const todayDateStr = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
      const timeStr = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;

      let emotionOk = false;
      let quoteOk = false;

      // 1. 直连财联社大盘情绪
      try {
        const clsUrl = 'https://x-quote.cls.cn/v2/quote/a/stock/emotion?app=cailianpress&os=android&sv=8.5.4&sign=5b4c4c6a3b9db1dd279a0d713111c4d0';
        const res = await fetch(clsUrl, { cache: 'no-cache' });
        if (res.ok) {
          const json = await res.json();
          if (json.code === 200 && json.data) {
            const d = json.data;
            if (!appData) appData = { meta: {}, tab_mood: {}, tab_crowding: {}, tab_focus: [], tab_strategy: {} };

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
            if (d.shsz_balance || d.preview_balance) {
              appData.tab_mood.turnover = d.shsz_balance || d.preview_balance;
            } else if (d.turnover) {
              appData.tab_mood.turnover = (Number(d.turnover) / 1e12).toFixed(2) + '万亿';
            }
            if (d.up_down_dis) {
              if (d.up_down_dis.rise_num !== undefined) appData.tab_mood.up_count = Number(d.up_down_dis.rise_num);
              if (d.up_down_dis.fall_num !== undefined) appData.tab_mood.down_count = Number(d.up_down_dis.fall_num);
              if (d.up_down_dis.up_num !== undefined) appData.tab_mood.zt_count = Number(d.up_down_dis.up_num);
              if (d.up_down_dis.down_num !== undefined) appData.tab_mood.dt_count = Number(d.up_down_dis.down_num);
            } else {
              if (d.up_num !== undefined) appData.tab_mood.zt_count = Number(d.up_num);
              if (d.down_num !== undefined) appData.tab_mood.dt_count = Number(d.down_num);
            }

            emotionOk = true;
            if (logBox) logBox.innerHTML += `<span style="color:var(--emerald);">✔ 财联社直连成功！当前情绪: ${degree} 度 (${statusText})，两市成交: ${appData.tab_mood.turnover}</span><br>`;
          }
        }
      } catch (err) {
        if (logBox) logBox.innerHTML += `<span style="color:var(--rose);">✘ 财联社直连提示: ${err.message}</span><br>`;
      }

      // 2. 直连腾讯实时金融行情源刷新自选池与连板核心标的现价与涨跌幅
      if (appData && appData.tab_focus && appData.tab_focus.length > 0) {
        try {
          if (logBox) logBox.innerHTML += '正在直连腾讯金融实时行情 (qt.gtimg.cn)...<br>';
          const codes = appData.tab_focus.map(s => {
            const raw = (s.code || '').toLowerCase();
            return (raw.startsWith('sh') || raw.startsWith('sz') || raw.startsWith('bj')) ? raw : (raw.startsWith('6') ? 'sh' + raw : 'sz' + raw);
          }).filter(Boolean);

          if (codes.length > 0) {
            const tencentUrl = 'https://qt.gtimg.cn/q=' + codes.join(',');
            const resQ = await fetch(tencentUrl, { cache: 'no-cache' });
            if (resQ.ok) {
              const textQ = await resQ.text();
              const quoteMap = {};
              textQ.split(';').forEach(line => {
                const parts = line.split('~');
                if (parts.length > 33) {
                  const c = parts[2]; // e.g. 003029
                  const curPrice = parseFloat(parts[3]) || 0;
                  const chgPct = parseFloat(parts[32]) || 0;
                  quoteMap[c] = {
                    price: curPrice.toFixed(2),
                    chg_pct: chgPct,
                    chg_str: (chgPct > 0 ? '+' : '') + chgPct.toFixed(2) + '%'
                  };
                }
              });

              appData.tab_focus.forEach(s => {
                const numCode = (s.code || '').replace(/^[a-zA-Z]+/, '');
                if (quoteMap[numCode]) {
                  const q = quoteMap[numCode];
                  s.latest_close = q.price;
                  s.chg_pct = q.chg_pct;
                  s.chg_pct_str = q.chg_str;
                  s.status = q.chg_pct >= 0 ? 'green' : 'amber';
                }
              });
              quoteOk = true;
              if (logBox) logBox.innerHTML += `<span style="color:var(--emerald);">✔ 自选池实时股价刷新成功！(已同步 ${Object.keys(quoteMap).length} 只标的)</span><br>`;
            }
          }
        } catch (errQ) {
          if (logBox) logBox.innerHTML += `<span style="color:var(--amber);">⚠️ 个股行情直连提示: ${errQ.message}</span><br>`;
        }
      }

      if (emotionOk || quoteOk) {
        // 关键：动态更新时间戳与日期为此时此刻真实时间！
        if (!appData.meta) appData.meta = {};
        appData.meta.date = todayDateStr;
        appData.meta.updated_at = `${todayDateStr} ${timeStr}`;
        latestDataDate = todayDateStr;

        // 持久化到本地存储
        localStorage.setItem('quant_bundle_latest', JSON.stringify(appData));

        // 重新渲染主界面全部组件
        renderAll(appData);

        // 更新状态指示药丸
        if (statusPill) {
          statusPill.textContent = '⚡ 直连实时';
          statusPill.className = 'status-pill status-direct';
        }

        // 更新设置弹窗内的数据状态展示
        const cfgDate = document.getElementById('cfg-date-show');
        const cfgTime = document.getElementById('cfg-time-show');
        if (cfgDate) cfgDate.textContent = todayDateStr;
        if (cfgTime) cfgTime.textContent = timeStr;

        showToast(`✔ 盘中实时行情已同步 (${timeStr})`);
        return true;
      }
      return false;
    }

    async function syncFromCloud() {
      const logBox = document.getElementById('sync-log-box');
      if (logBox) logBox.innerHTML = '正在检查云端与实时行情数据通道...<br>';
      // 优先直连金融行情源秒级拉取今天最新分时
      const ok = await syncDirectLive(false);
      if (ok) {
        if (logBox) logBox.innerHTML += '<span style="color:var(--emerald);">✔ 今日盘中实时数据同步完毕！</span><br>';
      } else {
        await loadData(true);
        if (logBox) logBox.innerHTML += '<span style="color:var(--emerald);">✔ 离线基准包加载就绪！</span><br>';
      }
    }
'''

    if old_sync_start != -1 and old_sync_end != -1 and old_sync_start < old_sync_end:
        content = content[:old_sync_start] + new_sync_code + '\n    ' + content[old_sync_end:]
        print(" -> 已精确替换 manualRefresh / syncDirectLive 逻辑！")

    # 2. 升级 renderFocusPool 呈现实时股价与涨跌幅
    old_render_focus_start = content.find("function renderFocusPool(focusList) {")
    old_render_focus_end = content.find("function buildKlineSvg(candles, ema5, ema20) {")

    new_render_focus_code = '''function renderFocusPool(focusList) {
      const box = document.getElementById('focus-list');
      if (!focusList || focusList.length === 0) {
        box.innerHTML = '<div style="color:var(--muted);font-size:12px;">暂无关注池标的</div>';
        return;
      }
      let html = '';
      focusList.forEach((s, idx) => {
        const isGreen = s.status === 'green';
        const statusBadge = isGreen ? '<span class="badge badge-emerald">🟢 顺势绿灯</span>' : '<span class="badge badge-amber">🟡 震荡观察</span>';
        const chgPct = (s.chg_pct !== undefined) ? s.chg_pct : null;
        let pctColor = '#F0F6FC';
        let chgStr = s.chg_pct_str || '';
        if (chgPct !== null) {
          if (chgPct > 0) { pctColor = 'var(--rose)'; if (!chgStr) chgStr = `+${chgPct.toFixed(2)}%`; }
          else if (chgPct < 0) { pctColor = 'var(--emerald)'; if (!chgStr) chgStr = `${chgPct.toFixed(2)}%`; }
          else { pctColor = 'var(--muted)'; if (!chgStr) chgStr = '0.00%'; }
        }

        html += `
          <div class="card" onclick="toggleStockDetail(${idx})" style="cursor:pointer;">
            <div style="display:flex; justify-content:space-between; align-items:flex-start;">
              <div>
                <div style="display:flex; align-items:center; gap:6px;">
                  <span style="font-size:15px; font-weight:700; color:#FFF;">${s.name}</span>
                  <span style="font-size:10px; color:var(--muted); font-family:monospace;">${s.code}</span>
                  ${s.is_custom ? '<span class="badge badge-blue">自选池</span>' : `<span class="badge badge-rose">${s.height || '核心'}</span>`}
                </div>
                <div style="font-size:11px; color:var(--muted); margin-top:2px;">${s.sector} · ${s.pool_type}</div>
              </div>
              <div style="text-align:right;">
                <div style="display:flex; align-items:baseline; justify-content:flex-end; gap:6px;">
                  <span style="font-size:15px; font-weight:800; font-family:monospace; color:${pctColor};">¥${s.latest_close || '--'}</span>
                  ${chgStr ? `<span style="font-size:11.5px; font-weight:700; font-family:monospace; color:${pctColor};">${chgStr}</span>` : ''}
                </div>
                <div style="margin-top:3px;">
                  ${statusBadge}
                </div>
              </div>
            </div>

            <!-- 规则详情 -->
            <div style="margin-top:10px; font-size:11px; background:var(--card-sub); padding:8px 10px; border-radius:8px; border:1px solid var(--border);">
              <div style="color:var(--rose); font-weight:600; margin-bottom:2px;">🎯 入场逻辑：</div>
              <div style="color:var(--text); line-height:1.4;">${s.entry_rule}</div>
              <div style="color:var(--amber); font-weight:600; margin-top:6px; margin-bottom:2px;">🛡️ 防守纪律：</div>
              <div style="color:var(--muted); line-height:1.4;">${s.defense_rule}</div>
            </div>

            <!-- 真实日K走势与均线图 (点击卡片展开/收起) -->
            <div id="chart-wrap-${idx}" style="display:none; margin-top:10px; padding-top:10px; border-top:1px solid var(--border);">
              <div style="display:flex; justify-content:space-between; font-size:10px; color:var(--muted); margin-bottom:6px;">
                <span>真实日K + <b style="color:var(--sky);">EMA5</b> / <b style="color:var(--amber);">EMA20</b></span>
                <span style="color:var(--text); font-family:monospace;">现价: ¥${s.latest_close || '--'}</span>
              </div>
              <div id="svg-container-${idx}">${buildKlineSvg(s.candles, s.ema5, s.ema20)}</div>
            </div>
          </div>
        `;
      });
      box.innerHTML = html;
    }
'''

    if old_render_focus_start != -1 and old_render_focus_end != -1:
        content = content[:old_render_focus_start] + new_render_focus_code + '\n    ' + content[old_render_focus_end:]
        print(" -> 已升级 renderFocusPool 显示实时股价与红绿分时涨跌幅！")

    with open(index_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(" -> mobile_app/index.html 写入成功！")

def main():
    patch_bundle_json()
    patch_index_html()
    print("全部补丁成功应用！")

if __name__ == "__main__":
    main()
