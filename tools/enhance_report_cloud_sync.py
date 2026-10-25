"""
为移动端 App 的【研报原篇】精读系统增加：
1. 云端/Git/局域网动态 Manifest 自动发现与合并
2. 研报按需多源网络拉取与持久化本地缓存 (离线秒开)
3. 优雅网络降级与失败重试
4. 顶部一键云端同步按钮与设置面板缓存管理
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = PROJECT_ROOT / "mobile_app" / "index.html"

def update_mobile_reports_system():
    content = INDEX_HTML.read_text(encoding="utf-8")

    # 1. 查找并替换 DEFAULT_MANIFEST 到 switchReportTab 函数末尾
    start_marker = "// 研报原篇精读系统 (集成 Manifest 与极速响应)"
    end_marker = "iframe.src = targetUrl;\n    }"

    idx_start = content.find(start_marker)
    idx_end = content.find(end_marker)

    if idx_start == -1 or idx_end == -1:
        print("❌ 未找到待替换的代码段", idx_start, idx_end)
        return False

    idx_end += len(end_marker)

    new_logic = '''// 研报原篇精读系统 (集成 Manifest、云端/Git动态探测与离线持久化缓存)
    // ==========================================
    const DEFAULT_MANIFEST = {"mainline": ["2026-10-22", "2026-10-21", "2026-10-20", "2026-10-19", "2026-10-15", "2026-10-14", "2026-10-13", "2026-10-12", "2026-10-09", "2026-10-08", "2026-09-30", "2026-09-28", "2026-09-24", "2026-09-22", "2026-09-21", "2026-09-18", "2026-09-17", "2026-09-16", "2026-09-15", "2026-09-11", "2026-09-10", "2026-09-09", "2026-09-07", "2026-09-04", "2026-09-03", "2026-09-02", "2026-09-01", "2026-08-31", "2026-08-28", "2026-08-27", "2026-08-26", "2026-08-25", "2026-08-24", "2026-08-21", "2026-08-20", "2026-08-19", "2026-08-18", "2026-08-17", "2026-08-14", "2026-08-13", "2026-08-12", "2026-08-11", "2026-08-10", "2026-08-07", "2026-08-06", "2026-08-05", "2026-08-04", "2026-08-03", "2026-07-31", "2026-07-30", "2026-07-29", "2026-07-28", "2026-07-27", "2026-07-24", "2026-07-23", "2026-07-22", "2026-07-21", "2026-07-20", "2026-07-17", "2026-07-16"], "plan": ["2026-10-22", "2026-10-21", "2026-10-20", "2026-10-19", "2026-10-15", "2026-10-14", "2026-10-13", "2026-10-12", "2026-10-09", "2026-10-08", "2026-09-30", "2026-09-28", "2026-09-24"], "crowd": ["2026-10-22", "2026-10-21", "2026-10-20", "2026-10-19", "2026-10-15", "2026-10-14", "2026-10-13", "2026-10-12", "2026-10-09", "2026-10-08", "2026-09-30"], "all_dates": ["2026-10-22", "2026-10-21", "2026-10-20", "2026-10-19", "2026-10-15", "2026-10-14", "2026-10-13", "2026-10-12", "2026-10-09", "2026-10-08", "2026-09-30", "2026-09-28", "2026-09-24", "2026-09-22", "2026-09-21", "2026-09-18", "2026-09-17", "2026-09-16", "2026-09-15", "2026-09-11", "2026-09-10", "2026-09-09", "2026-09-07", "2026-09-04", "2026-09-03", "2026-09-02", "2026-09-01", "2026-08-31", "2026-08-28", "2026-08-27", "2026-08-26", "2026-08-25", "2026-08-24", "2026-08-21", "2026-08-20", "2026-08-19", "2026-08-18", "2026-08-17", "2026-08-14", "2026-08-13", "2026-08-12", "2026-08-11", "2026-08-10", "2026-08-07", "2026-08-06", "2026-08-05", "2026-08-04", "2026-08-03", "2026-07-31", "2026-07-30", "2026-07-29", "2026-07-28", "2026-07-27", "2026-07-24", "2026-07-23", "2026-07-22", "2026-07-21", "2026-07-20", "2026-07-17", "2026-07-16"]};
    let currentReportType = 'plan';
    let currentReportDate = null;
    let _reportTipTimer = null;
    let _isSyncingManifest = false;

    function getReportsManifest() {
      let base = (window.__REPORTS_MANIFEST__ && window.__REPORTS_MANIFEST__.all_dates) ? window.__REPORTS_MANIFEST__ : DEFAULT_MANIFEST;
      try {
        const cachedRaw = localStorage.getItem('quant_cached_reports_manifest');
        if (cachedRaw) {
          const cached = JSON.parse(cachedRaw);
          if (cached && cached.all_dates && Array.isArray(cached.all_dates)) {
            const keys = ['all_dates', 'plan', 'crowd', 'mainline'];
            const merged = {};
            keys.forEach(k => {
              const s = new Set([...(cached[k] || []), ...(base[k] || [])]);
              merged[k] = Array.from(s).sort().reverse();
            });
            return merged;
          }
        }
      } catch (e) {
        console.warn('Failed reading cached manifest', e);
      }
      return base;
    }

    async function syncReportsManifestRemote(showToastMsg = false) {
      if (_isSyncingManifest) return;
      _isSyncingManifest = true;
      const endpoints = [
        (customServer ? `${customServer}/mobile_app/reports/manifest.json` : null),
        (customServer ? `${customServer}/reports/manifest.json` : null),
        'https://cdn.jsdelivr.net/gh/mengkai666/quant_factor_tutorial@master/mobile_app/reports/manifest.json',
        'https://raw.githubusercontent.com/mengkai666/quant_factor_tutorial/master/mobile_app/reports/manifest.json',
        './reports/manifest.json'
      ].filter(Boolean);

      for (const url of endpoints) {
        try {
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), 3500);
          const resp = await fetch(url + (url.startsWith('http') ? '?_t=' + Date.now() : ''), {
            signal: controller.signal,
            cache: 'no-store'
          });
          clearTimeout(timer);
          if (resp.ok) {
            const remoteManifest = await resp.json();
            if (remoteManifest && remoteManifest.all_dates && remoteManifest.all_dates.length > 0) {
              const current = getReportsManifest();
              const oldLatest = (current.all_dates && current.all_dates[0]) || '';
              const keys = ['all_dates', 'plan', 'crowd', 'mainline'];
              const merged = {};
              keys.forEach(k => {
                const s = new Set([...(remoteManifest[k] || []), ...(current[k] || [])]);
                merged[k] = Array.from(s).sort().reverse();
              });
              localStorage.setItem('quant_cached_reports_manifest', JSON.stringify(merged));
              window.__REPORTS_MANIFEST__ = merged;
              
              const newLatest = merged.all_dates[0] || '';
              renderReportDatePopupList();
              
              if (showToastMsg || (oldLatest && newLatest && newLatest > oldLatest)) {
                showToast(`✔ 研报清单已从云端同步 (最新: ${newLatest})`);
              }
              _isSyncingManifest = false;
              return merged;
            }
          }
        } catch (e) {
          // Continue to next endpoint
        }
      }
      _isSyncingManifest = false;
      if (showToastMsg) {
        showToast('ℹ️ 研报清单已是最新');
      }
      return null;
    }

    function openReportModal(preferredDate) {
      const manifest = getReportsManifest();
      const latestAvail = (manifest.all_dates && manifest.all_dates.length > 0) ? manifest.all_dates[0] : '2026-10-22';
      const activeDate = preferredDate || currentDate || (appData && appData.meta && appData.meta.date) || latestDataDate || latestAvail;
      
      currentReportDate = activeDate || latestAvail;

      closeReportDatePopup();

      const mask = document.getElementById('modal-report-mask');
      const sheet = document.getElementById('modal-report-sheet');
      if (mask) mask.classList.add('on');
      if (sheet) {
        sheet.style.display = 'flex';
        sheet.classList.add('on');
      }
      switchReportTab(currentReportType);

      // 后台静默探测云端是否有新发布的研报
      setTimeout(() => {
        syncReportsManifestRemote(false);
      }, 200);
    }

    function closeReportModal() {
      const mask = document.getElementById('modal-report-mask');
      const sheet = document.getElementById('modal-report-sheet');
      if (mask) mask.classList.remove('on');
      if (sheet) {
        sheet.classList.remove('on');
        sheet.style.display = 'none';
      }
      closeReportDatePopup();
      const iframe = document.getElementById('report-iframe');
      if (iframe) {
        iframe.removeAttribute('srcdoc');
        iframe.src = 'about:blank';
      }
      if (_reportTipTimer) clearTimeout(_reportTipTimer);
    }

    function toggleReportDatePopup(e) {
      if (e) e.stopPropagation();
      const popup = document.getElementById('report-date-popup');
      if (!popup) return;
      const isShown = popup.style.display === 'block';
      popup.style.display = isShown ? 'none' : 'block';
      if (!isShown) {
        renderReportDatePopupList();
      }
    }

    function closeReportDatePopup() {
      const popup = document.getElementById('report-date-popup');
      if (popup) popup.style.display = 'none';
    }

    function renderReportDatePopupList() {
      const list = document.getElementById('report-date-popup-list');
      if (!list) return;
      const manifest = getReportsManifest();
      const dates = manifest.all_dates || ['2026-10-22', '2026-10-21'];
      
      list.innerHTML = dates.map(d => {
        const isSel = (d === currentReportDate);
        const bg = isSel ? 'rgba(245,158,11,0.2)' : 'transparent';
        const color = isSel ? '#FCD34D' : '#C9D1D9';
        const border = isSel ? '1px solid rgba(245,158,11,0.4)' : '1px solid transparent';
        return `<button type="button" onclick="selectReportDate('${d}', event)" style="width:100%; text-align:left; padding:8px 12px; background:${bg}; color:${color}; border:${border}; border-radius:6px; font-size:13px; font-weight:600; cursor:pointer; display:flex; justify-content:space-between; align-items:center;">
          <span>${d}</span>
          ${isSel ? '<span style="color:#FCD34D;">✓</span>' : ''}
        </button>`;
      }).join('');
    }

    function selectReportDate(dateStr, e) {
      if (e) e.stopPropagation();
      closeReportDatePopup();
      onReportDateSelect(dateStr);
    }

    function onReportDateSelect(dateStr) {
      currentReportDate = dateStr;
      switchReportTab(currentReportType);
    }

    async function switchReportTab(type) {
      currentReportType = type;
      ['plan', 'crowd', 'mainline'].forEach(t => {
        const btn = document.getElementById('btn-rep-' + t);
        if (btn) btn.classList.toggle('active', t === type);
      });

      const manifest = getReportsManifest();
      const availDates = (manifest && manifest[type]) ? manifest[type] : [];
      
      const requestedDate = currentReportDate || ((manifest.all_dates && manifest.all_dates[0]) ? manifest.all_dates[0] : '2026-10-22');
      let dateToUse = requestedDate;
      let isFallback = false;

      // 如果当前类型下无此日期研报，自动寻找最临近的历史日期
      if (!availDates.includes(dateToUse)) {
        const priors = availDates.filter(d => d <= dateToUse);
        if (priors.length > 0) {
          dateToUse = priors[0];
          isFallback = true;
        } else if (availDates.length > 0) {
          dateToUse = availDates[0];
          isFallback = true;
        } else {
          dateToUse = requestedDate;
        }
      }

      currentReportDate = dateToUse;

      // 更新横幅提示（杜绝出现 null）
      const banner = document.getElementById('report-fallback-banner');
      if (banner) {
        if (isFallback && requestedDate && requestedDate !== dateToUse) {
          banner.style.display = 'flex';
          banner.innerHTML = `📢 提示：<b>${requestedDate}</b> 该类研报生成中，已自动载入最近一期 (<b>${dateToUse}</b>) 原篇`;
        } else {
          banner.style.display = 'none';
        }
      }

      // 更新顶部 Badge 与切换按钮文字
      const badge = document.getElementById('report-modal-date');
      if (badge) badge.textContent = dateToUse;
      const btnLabel = document.getElementById('report-date-btn-label');
      if (btnLabel) btnLabel.textContent = `📅 ${dateToUse}`;

      const iframe = document.getElementById('report-iframe');
      const tip = document.getElementById('report-loading-tip');
      const errBox = document.getElementById('report-error-box');

      if (_reportTipTimer) clearTimeout(_reportTipTimer);
      if (tip) {
        tip.style.display = 'flex';
        tip.innerHTML = `<span class="loading-spinner" style="width:12px; height:12px;"></span> 载入中...`;
      }
      if (errBox) errBox.style.display = 'none';

      // 1. 尝试从本地离线持久化缓存读取 (秒开，0ms 延迟)
      const cacheKey = 'quant_rep_cache_' + type + '_' + dateToUse;
      let cachedHtml = null;
      try {
        cachedHtml = localStorage.getItem(cacheKey);
      } catch (e) {}

      if (cachedHtml && cachedHtml.length > 100) {
        if (tip) tip.style.display = 'none';
        iframe.removeAttribute('src');
        iframe.srcdoc = cachedHtml;
        return;
      }

      // 2. 远端动态拉取候选链 (自定义服务/局域网优先 -> CDN -> GitHub Raw -> 本地离线 assets)
      const candidates = [
        (customServer ? `${customServer}/mobile_app/reports/${type}_${dateToUse}.html` : null),
        (customServer ? `${customServer}/reports/${type}_${dateToUse}.html` : null),
        `https://cdn.jsdelivr.net/gh/mengkai666/quant_factor_tutorial@master/mobile_app/reports/${type}_${dateToUse}.html`,
        `https://raw.githubusercontent.com/mengkai666/quant_factor_tutorial/master/mobile_app/reports/${type}_${dateToUse}.html`,
        `./reports/${type}_${dateToUse}.html`
      ].filter(Boolean);

      let loadedViaFetch = false;
      for (const url of candidates) {
        try {
          const controller = new AbortController();
          const toTimer = setTimeout(() => controller.abort(), 4000);
          const resp = await fetch(url + (url.startsWith('http') ? '?_t=' + Date.now() : ''), {
            signal: controller.signal
          });
          clearTimeout(toTimer);
          if (resp.ok) {
            const html = await resp.text();
            if (html && (html.includes('<html') || html.includes('<!DOCTYPE') || html.includes('<body') || html.includes('<div'))) {
              // 存入离线持久化缓存
              try {
                localStorage.setItem(cacheKey, html);
              } catch (e) {
                console.warn('Report localStorage quota reached, skipping cache save');
              }
              if (tip) tip.style.display = 'none';
              iframe.removeAttribute('src');
              iframe.srcdoc = html;
              loadedViaFetch = true;
              break;
            }
          }
        } catch (e) {
          // 继续尝试下一个候选源
        }
      }

      if (loadedViaFetch) return;

      // 3. 若 fetch 受阻（例如断网或文件协议），优雅回退到 iframe 原生直连打包目录
      let targetUrl = `./reports/${type}_${dateToUse}.html`;
      if (window.location.protocol !== 'file:') {
        targetUrl += '?t=' + Date.now();
      }

      _reportTipTimer = setTimeout(() => {
        if (tip) tip.style.display = 'none';
      }, 1000);

      iframe.onload = () => {
        if (_reportTipTimer) clearTimeout(_reportTipTimer);
        if (tip) tip.style.display = 'none';
      };
      iframe.onerror = () => {
        if (_reportTipTimer) clearTimeout(_reportTipTimer);
        if (tip) tip.style.display = 'none';
        if (errBox) {
          errBox.style.display = 'flex';
          const errDateSpan = document.getElementById('report-err-date');
          if (errDateSpan) errDateSpan.textContent = dateToUse;
        }
      };
      iframe.removeAttribute('srcdoc');
      iframe.src = targetUrl;
    }'''

    content = content[:idx_start] + new_logic + content[idx_end:]

    # 2. 在清空缓存处清理研报缓存
    content = content.replace(
        "localStorage.removeItem('quant_bundle_latest');",
        "localStorage.removeItem('quant_bundle_latest');\n      localStorage.removeItem('quant_cached_reports_manifest');"
    )
    content = content.replace(
        "if (k.startsWith('quant_bundle_')) localStorage.removeItem(k);",
        "if (k.startsWith('quant_bundle_') || k.startsWith('quant_rep_')) localStorage.removeItem(k);"
    )

    # 3. 在 report-date-popup 旁边增加云端同步按钮
    old_btn_group = '''        <!-- 36x36 舒适触控大关闭按钮 -->
        <button type="button" class="btn-modal-close" onclick="closeReportModal()"'''
    
    new_btn_group = '''        <!-- 云端/局域网研报清单一键同步按钮 -->
        <button type="button" onclick="syncReportsManifestRemote(true)" title="从云端/局域网同步最新研报清单" style="width:36px; height:36px; min-width:36px; min-height:36px; border-radius:8px; background:#1E2638; border:1px solid #3B4861; color:#38BDF8; font-size:14px; font-weight:700; display:inline-flex; align-items:center; justify-content:center; cursor:pointer; touch-action:manipulation; -webkit-tap-highlight-color:transparent;">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.3"/></svg>
        </button>
        <!-- 36x36 舒适触控大关闭按钮 -->
        <button type="button" class="btn-modal-close" onclick="closeReportModal()"'''

    if old_btn_group in content:
        content = content.replace(old_btn_group, new_btn_group, 1)

    # 4. 优化 report-error-box 内容
    old_err = '''      <div id="report-error-box" style="display:none; position:absolute; inset:0; flex-direction:column; align-items:center; justify-content:center; background:#0B0E14; color:#F87171; font-size:13px; padding:20px; text-align:center; z-index:11;">
        <div style="font-size:32px; margin-bottom:10px;">📑</div>
        <div style="font-weight:700; color:#F0F6FC; margin-bottom:6px;">该日研报尚未出炉或未收录</div>
        <div style="color:var(--muted); font-size:12px; margin-bottom:16px;">请切换上方的其他日期或研报类型</div>
        <button onclick="onReportDateSelect('2026-10-21')" style="padding:6px 14px; background:var(--sky); color:#0B0E14; border:none; border-radius:6px; font-weight:700; font-size:12px; cursor:pointer;">查看最新可用研报 (2026-10-21)</button>
      </div>'''

    new_err = '''      <div id="report-error-box" style="display:none; position:absolute; inset:0; flex-direction:column; align-items:center; justify-content:center; background:#0B0E14; color:#F87171; font-size:13px; padding:20px; text-align:center; z-index:11;">
        <div style="font-size:32px; margin-bottom:10px;">📑</div>
        <div style="font-weight:700; color:#F0F6FC; margin-bottom:6px;">该日研报尚未出炉或未从云端拉取</div>
        <div style="color:var(--muted); font-size:12px; margin-bottom:16px;">请切换上方的其他日期，或点击下方按钮重试</div>
        <div style="display:flex; gap:8px; flex-wrap:wrap; justify-content:center;">
          <button onclick="switchReportTab(currentReportType)" style="padding:8px 16px; background:var(--sky); color:#0B0E14; border:none; border-radius:6px; font-weight:700; font-size:12px; cursor:pointer;">🔄 从云端重新拉取</button>
          <button onclick="openSettingsModal()" style="padding:8px 16px; background:var(--card-sub); color:#C9D1D9; border:1px solid var(--border); border-radius:6px; font-weight:600; font-size:12px; cursor:pointer;">⚙️ 局域网/服务器设置</button>
          <button onclick="onReportDateSelect('2026-10-21')" style="padding:8px 16px; background:#1F2937; color:#9CA3AF; border:none; border-radius:6px; font-weight:600; font-size:12px; cursor:pointer;">查看历史离线研报 (10-21)</button>
        </div>
      </div>'''

    if old_err in content:
        content = content.replace(old_err, new_err, 1)

    INDEX_HTML.write_text(content, encoding="utf-8")
    print("✅ mobile_app/index.html 研报动态云端同步引擎注入成功！")
    return True

if __name__ == "__main__":
    update_mobile_reports_system()
