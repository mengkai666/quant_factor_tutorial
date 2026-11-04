"""优化研报日期选择浮层与默认值 (tools/enhance_report_popup_ui.py)"""

from pathlib import Path

index_path = Path("mobile_app/index.html")
content = index_path.read_text(encoding="utf-8")

# 1. 增加 getDayOfWeekLabel 与更新 openReportModal fallback
if "function getDayOfWeekLabel" not in content:
    old_modal = """    function openReportModal(preferredDate) {
      const manifest = getReportsManifest();
      const latestAvail = (manifest.all_dates && manifest.all_dates.length > 0) ? manifest.all_dates[0] : '2026-10-22';"""

    new_modal = """    function getDayOfWeekLabel(dateStr) {
      try {
        const parts = dateStr.split('-');
        if (parts.length === 3) {
          const dt = new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10));
          const wds = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
          return wds[dt.getDay()] || '';
        }
      } catch (e) {}
      return '';
    }

    function openReportModal(preferredDate) {
      const manifest = getReportsManifest();
      const latestAvail = (manifest.all_dates && manifest.all_dates.length > 0) ? manifest.all_dates[0] : '2026-10-29';"""

    if old_modal in content:
        content = content.replace(old_modal, new_modal, 1)
    else:
        content = content.replace("manifest.all_dates[0] : '2026-10-22'", "manifest.all_dates[0] : '2026-10-29'")

# 2. 升级 renderReportDatePopupList
old_render = """    function renderReportDatePopupList() {
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
    }"""

new_render = """    function renderReportDatePopupList() {
      const list = document.getElementById('report-date-popup-list');
      if (!list) return;
      const manifest = getReportsManifest();
      const dates = manifest.all_dates || ['2026-10-29', '2026-10-28', '2026-10-27'];
      const planSet = new Set(manifest.plan || []);
      const crowdSet = new Set(manifest.crowd || []);
      const mainSet = new Set(manifest.mainline || []);

      list.innerHTML = dates.map((d, idx) => {
        const isSel = (d === currentReportDate);
        const isLatest = (idx === 0);
        const wd = getDayOfWeekLabel(d);
        const bg = isSel ? 'rgba(245,158,11,0.22)' : 'rgba(255,255,255,0.02)';
        const color = isSel ? '#FCD34D' : '#E2E8F0';
        const border = isSel ? '1px solid rgba(245,158,11,0.5)' : '1px solid rgba(255,255,255,0.06)';

        const hasPlan = planSet.has(d);
        const hasCrowd = crowdSet.has(d);
        const hasMain = mainSet.has(d);
        const hasAll = hasPlan && hasCrowd && hasMain;

        let badges = '';
        if (isLatest) {
          badges += `<span style="font-size:10px; padding:1px 5px; border-radius:4px; background:#EF4444; color:#fff; font-weight:700; margin-left:6px;">最新</span>`;
        }
        if (hasAll) {
          badges += `<span style="font-size:10px; padding:1px 5px; border-radius:4px; background:rgba(16,185,129,0.18); color:#34D399; border:1px solid rgba(16,185,129,0.3); margin-left:4px;">全篇</span>`;
        }

        return `<button type="button" onclick="selectReportDate('${d}', event)" style="width:100%; text-align:left; padding:9px 12px; background:${bg}; color:${color}; border:${border}; border-radius:8px; font-size:13px; font-weight:600; cursor:pointer; display:flex; justify-content:space-between; align-items:center; margin-bottom:4px; touch-action:manipulation;">
          <div style="display:flex; align-items:center;">
            <span>${d}</span>
            <span style="font-size:11px; color:#8B949E; margin-left:6px; font-weight:normal;">${wd}</span>
            ${badges}
          </div>
          <div style="display:flex; align-items:center; gap:6px;">
            ${isSel ? '<span style="color:#FCD34D; font-weight:900;">✓</span>' : '<span style="color:#4B5563; font-size:11px;">查看</span>'}
          </div>
        </button>`;
      }).join('');
    }"""

if old_render in content:
    content = content.replace(old_render, new_render)
    print("✅ 成功替换 renderReportDatePopupList")
else:
    print("⚠️ 未完全匹配 old_render，正在检查原因")

# 3. 优化弹出框尺寸
content = content.replace(
    'id="report-date-popup" style="display:none; position:absolute; top:calc(100% + 6px); right:0; width:220px; max-height:360px;',
    'id="report-date-popup" style="display:none; position:absolute; top:calc(100% + 6px); right:0; width:260px; max-height:420px;'
)

index_path.write_text(content, encoding="utf-8")
print("✅ 完成 index.html 优化保存")
