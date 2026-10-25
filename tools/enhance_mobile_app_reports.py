"""全面升级移动端：深度融合【抱团周期】、【连板战法】与【短线情绪】三大核心研报产物"""

import os
import re
import glob
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def sync_reports_for_mobile():
    """将最近 15 个交易日的核心研报同步至 mobile_app/reports/ 供移动端无网秒看原篇"""
    out_dir = PROJECT_ROOT / "mobile_app" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)

    site_reports = sorted(glob.glob(str(PROJECT_ROOT / "output" / "site" / "reports" / "2026-*.html")), reverse=True)
    
    for sr in site_reports[:15]:
        fname = Path(sr).name
        m = re.match(r"(\d{4}-\d{2}-\d{2})\.html", fname)
        if not m: continue
        d = m.group(1)
        clean_d = d.replace("-", "")

        # 1. 主线追踪大报告
        shutil.copy2(sr, out_dir / f"mainline_{d}.html")

        # 2. 今日复盘与明日预案
        plan_candidates = [
            PROJECT_ROOT / "output" / f"今日复盘与明日预案_{clean_d}.html",
            PROJECT_ROOT / "output" / f"今日复盘与明日预案_{d}.html"
        ]
        for pc in plan_candidates:
            if pc.exists():
                shutil.copy2(pc, out_dir / f"plan_{d}.html")
                break

        # 3. 抱团周期分析报告
        crowd_candidates = [
            PROJECT_ROOT / "output" / f"抱团周期与新周期试错分析_{d}.html",
            PROJECT_ROOT / "output" / f"抱团周期分析_{d}.html"
        ]
        for cc in crowd_candidates:
            if cc.exists():
                shutil.copy2(cc, out_dir / f"crowd_{d}.html")
                break

    print(f"Synced recent 15 dates of full reports to {out_dir}")

def update_index_html():
    index_file = PROJECT_ROOT / "mobile_app" / "index.html"
    with open(index_file, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. 顶部 Header 增加【📄 研报原篇】按钮
    if "openReportModal()" not in content:
        content = content.replace(
            '<button class="btn-refresh" onclick="openDateModal()" title="切换历史日期" style="padding:6px 8px;">',
            '''<button class="btn-refresh" onclick="openReportModal()" title="阅读该日完整投研长文" style="padding:6px 8px; color:#FCD34D; border-color:rgba(245,158,11,0.4);">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
        <span>研报原篇</span>
      </button>
      <button class="btn-refresh" onclick="openDateModal()" title="切换历史日期" style="padding:6px 8px;">'''
        )

    # 2. 在 Tab 1 (情绪) 中加入【连板核心战法与实战铁律】卡片
    lianban_laws_card = '''
      <!-- 连板战法与核心铁律 (研报实战提炼) -->
      <div class="card">
        <div class="card-title">
          <span>连板核心战法与实战铁律</span>
          <span class="badge badge-rose">战法研判</span>
        </div>
        <div style="display: flex; flex-direction: column; gap: 8px; font-size: 11.5px; line-height: 1.5;">
          <div style="background:rgba(244,63,94,0.08); border-left:3px solid var(--rose); padding:8px 10px; border-radius:6px;">
            <b style="color:var(--rose);">⚡ 战法 A · 空间板高度突破律</b>
            <div style="color:var(--text); margin-top:2px;">
              锚定全市场连板做多天花板（如当前最高标）。一旦空间龙放量突破事前压力位，即打开题材向上进攻弹性；若高位断板决裂，必须无条件执行中后排防守出清。
            </div>
          </div>
          <div style="background:rgba(245,158,11,0.08); border-left:3px solid var(--amber); padding:8px 10px; border-radius:6px;">
            <b style="color:var(--amber);">⚔️ 铁律 3 · 同身位双子星生死律</b>
            <div style="color:var(--text); margin-top:2px;">
              同梯队连板标的展开白刃卡位战（淘汰率超 50%）。以早盘 9:25 竞价封单金额与分时放量转强决胜负，胜者加速晋级，败者遭资金无情践踏抛弃。
            </div>
          </div>
          <div style="background:rgba(56,189,248,0.08); border-left:3px solid var(--sky); padding:8px 10px; border-radius:6px;">
            <b style="color:var(--sky);">🛡️ 战法 C · 弱转强与低位换手活口</b>
            <div style="color:var(--text); margin-top:2px;">
              高位分歧加剧时，资金向首板或 2 进 3 换手活口避险。重点观察竞价平开或小幅低开后迅速大单抢筹拉升的弱转强标的。
            </div>
          </div>
        </div>
      </div>
'''
    if "连板核心战法与实战铁律" not in content:
        content = content.replace(
            '<!-- 连板天梯 -->',
            lianban_laws_card + '\n      <!-- 连板天梯 -->'
        )

    # 3. 在 Tab 2 (抱团主线) 中加入【抱团周期与新老周期解构】卡片
    crowd_card = '''
      <!-- 本地投研：抱团周期与新老周期解构 -->
      <div class="card">
        <div class="card-title">
          <span>投研核心 · 抱团周期与新老周期解构</span>
          <span class="badge badge-rose">周期阵营</span>
        </div>
        <div id="local-crowding-list" style="display: flex; flex-direction: column; gap: 10px;">
          <!-- 动态加载抱团阵营 -->
        </div>
      </div>
'''
    if 'id="local-crowding-list"' not in content:
        content = content.replace(
            '<!-- 今日风口 -->',
            crowd_card + '\n      <!-- 今日风口 -->'
        )

    # 4. 在 renderMainline 函数中增加对 local_cycles 的渲染逻辑
    old_render_mainline = 'mainBox.innerHTML = mHtml || \'<div style="color:var(--muted);font-size:12px;">暂无主线数据</div>\';'
    new_render_mainline = '''mainBox.innerHTML = mHtml || '<div style="color:var(--muted);font-size:12px;">暂无主线数据</div>';

      // 4. 抱团周期与新老周期解构渲染
      const crowdBox = document.getElementById('local-crowding-list');
      if (crowdBox) {
        let cHtml = '';
        (crowding.local_cycles || []).forEach(g => {
          const members = (g.members || []).map(m => `
            <div style="background:rgba(255,255,255,0.06); padding:4px 8px; border-radius:6px; font-size:11px; display:inline-flex; align-items:center; gap:4px;">
              <span style="color:#FFF; font-weight:600;">${m.name}</span>
              <span style="font-size:9.5px; color:var(--muted); font-family:monospace;">${m.code || ''}</span>
              ${m.max_height ? `<span style="font-size:9.5px; color:var(--rose); font-weight:700;">${m.max_height}B</span>` : ''}
            </div>
          `).join('');

          const isOld = g.title.includes('旧') || g.title.includes('8月');
          const isTrial = g.title.includes('试错') || g.title.includes('新');
          const stageBadge = isOld 
            ? '<span class="badge badge-blue">退潮出清期</span>' 
            : (isTrial ? '<span class="badge badge-rose">新周期试错</span>' : '<span class="badge badge-amber">周期内轮动</span>');

          cHtml += `
            <div style="background:var(--card-sub); padding:10px 12px; border-radius:12px; border:1px solid var(--border);">
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                <span style="font-weight:700; color:#FFF; font-size:13px;">🌪️ ${g.title}</span>
                ${stageBadge}
              </div>
              <div style="font-size:11px; color:var(--muted); margin-bottom:8px;">${g.origin || '抱团阵营与资金承接样本'}</div>
              <div style="display:flex; flex-wrap:wrap; gap:6px;">
                ${members || '<span style="color:var(--muted);font-size:11px;">暂无成分样本</span>'}
              </div>
            </div>
          `;
        });
        crowdBox.innerHTML = cHtml || '<div style="color:var(--muted);font-size:11px;">当前暂无独立抱团阵营标记</div>';
      }'''
    if 'local-crowding-list' not in content or 'g.title.includes' not in content:
        content = content.replace(old_render_mainline, new_render_mainline)

    # 5. 增加【研报原篇精读阅读器】Modal
    report_modal_html = '''
  <!-- 研报原篇沉浸式阅读器 Modal -->
  <div class="modal-mask" id="modal-report-mask" onclick="closeReportModal()"></div>
  <div class="modal-sheet" id="modal-report-sheet" style="max-height:92vh; height:92vh;">
    <div class="modal-h">
      <div style="display:flex; align-items:center; gap:8px;">
        <span class="modal-t">📄 投研研报原篇精读</span>
        <span id="report-modal-date" class="badge badge-amber">--</span>
      </div>
      <div style="display:flex; align-items:center; gap:8px;">
        <button onclick="openReportInNewWindow()" style="background:none; border:none; color:var(--sky); font-size:13px; cursor:pointer;" title="外部打开">↗ 浏览器打开</button>
        <button class="modal-x" onclick="closeReportModal()">×</button>
      </div>
    </div>
    <!-- 报告类别 Tab 栏 -->
    <div style="display:flex; gap:6px; padding:8px 16px; background:#181E2B; border-bottom:1px solid var(--border); overflow-x:auto;">
      <button class="chip-btn active" id="btn-rep-mainline" onclick="switchReportTab('mainline')">📊 主线追踪大报告</button>
      <button class="chip-btn" id="btn-rep-plan" onclick="switchReportTab('plan')">⚔️ 复盘与实战预案</button>
      <button class="chip-btn" id="btn-rep-crowd" onclick="switchReportTab('crowd')">🌪️ 抱团周期深度解构</button>
    </div>
    <div style="flex:1; width:100%; height:100%; position:relative; background:#0B0E14;">
      <iframe id="report-iframe" style="width:100%; height:100%; border:none; background:#0B0E14;" src="about:blank"></iframe>
      <div id="report-loading-tip" style="position:absolute; inset:0; display:flex; align-items:center; justify-content:center; color:var(--muted); font-size:13px; pointer-events:none;">
        正在装载深度研报全文...
      </div>
    </div>
  </div>
'''
    if 'id="modal-report-sheet"' not in content:
        content = content.replace('</body>', report_modal_html + '\n</body>')

    # 6. 注入 Report Modal JS 脚本
    report_js = '''
    // ================= 研报原篇阅读器管理 =================
    let currentReportType = 'mainline'; // 'mainline' | 'plan' | 'crowd'

    function openReportModal() {
      const activeDate = currentDate || (appData && appData.meta ? appData.meta.date : latestDataDate) || '2026-10-21';
      document.getElementById('report-modal-date').textContent = activeDate;
      document.getElementById('modal-report-mask').classList.add('on');
      document.getElementById('modal-report-sheet').classList.add('on');
      switchReportTab(currentReportType);
    }

    function closeReportModal() {
      document.getElementById('modal-report-mask').classList.remove('on');
      document.getElementById('modal-report-sheet').classList.remove('on');
      document.getElementById('report-iframe').src = 'about:blank';
    }

    function switchReportTab(type) {
      currentReportType = type;
      ['mainline', 'plan', 'crowd'].forEach(t => {
        const btn = document.getElementById('btn-rep-' + t);
        if (btn) btn.classList.toggle('active', t === type);
      });

      const activeDate = currentDate || (appData && appData.meta ? appData.meta.date : latestDataDate) || '2026-10-21';
      const cleanDate = activeDate.replace(/-/g, '');
      const iframe = document.getElementById('report-iframe');
      const tip = document.getElementById('report-loading-tip');
      if (tip) tip.style.display = 'flex';

      let targetUrl = '';
      if (type === 'mainline') {
        targetUrl = `./reports/mainline_${activeDate}.html`;
      } else if (type === 'plan') {
        targetUrl = `./reports/plan_${activeDate}.html`;
      } else if (type === 'crowd') {
        targetUrl = `./reports/crowd_${activeDate}.html`;
      }

      iframe.onload = () => {
        if (tip) tip.style.display = 'none';
      };
      iframe.src = targetUrl + '?t=' + Date.now();
    }

    function openReportInNewWindow() {
      const iframe = document.getElementById('report-iframe');
      if (iframe && iframe.src && iframe.src !== 'about:blank') {
        window.open(iframe.src, '_blank');
      }
    }
'''
    if 'function openReportModal()' not in content:
        content = content.replace(
            'function openDateModal() {',
            report_js + '\n    function openDateModal() {'
        )

    with open(index_file, "w", encoding="utf-8") as f:
        f.write(content)

    print("Updated mobile_app/index.html with report integration!")

if __name__ == "__main__":
    sync_reports_for_mobile()
    update_index_html()
