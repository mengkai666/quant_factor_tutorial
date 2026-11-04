import re
from pathlib import Path

index_path = Path("mobile_app/index.html")
content = index_path.read_text(encoding="utf-8")

# 1. 提取 pane-mood 与 pane-crowding 内容
mood_match = re.search(r'<section id="pane-mood" class="tab-pane active">([\s\S]+?)</section>', content)
if not mood_match:
    raise RuntimeError("Could not find pane-mood section")
mood_inner = mood_match.group(1).strip()

crowd_match = re.search(r'<section id="pane-crowding" class="tab-pane">([\s\S]+?)</section>', content)
if not crowd_match:
    raise RuntimeError("Could not find pane-crowding section")
crowd_inner = crowd_match.group(1).strip()

# 2. 构造整合后的 pane-recap
recap_section = f'''    <!-- ================= TAB 1: 今日复盘 (大盘情绪 + 主线抱团) ================= -->
    <section id="pane-recap" class="tab-pane active">
      <!-- 复盘二级分段器 -->
      <div style="display:flex; background:rgba(255,255,255,0.05); padding:3px; border-radius:10px; margin-bottom:12px; gap:4px; border:1px solid rgba(255,255,255,0.08);">
        <button id="recap-subtab-mood" class="recap-subtab-btn active" onclick="switchRecapSub('mood')" style="flex:1; padding:8px 0; border:none; border-radius:7px; background:var(--sky); color:#0B0E14; font-size:12px; font-weight:700; cursor:pointer; display:flex; align-items:center; justify-content:center; gap:5px; transition:all 0.2s;">
          <span>📈 大盘与情绪周期</span>
        </button>
        <button id="recap-subtab-crowd" class="recap-subtab-btn" onclick="switchRecapSub('crowd')" style="flex:1; padding:8px 0; border:none; border-radius:7px; background:transparent; color:#94A3B8; font-size:12px; font-weight:700; cursor:pointer; display:flex; align-items:center; justify-content:center; gap:5px; transition:all 0.2s;">
          <span>🌊 题材与主线抱团</span>
        </button>
      </div>

      <!-- 子视图 A: 大盘与情绪周期 -->
      <div id="recap-view-mood">
        {mood_inner}
      </div>

      <!-- 子视图 B: 题材与主线抱团 -->
      <div id="recap-view-crowd" style="display:none;">
        {crowd_inner}
      </div>
    </section>'''

# 替换掉原本的 pane-mood 和 pane-crowding 两个独立 section
target_old_sections = content[mood_match.start():crowd_match.end()]
content = content.replace(target_old_sections, recap_section)

# 3. 移除 pane-strategy 中多余的「阅读完整深度研报 (75KB)」大按钮，使动作栏专注为一键复制晨会卡片
old_action_bar = '''        <!-- 操盘快捷动作栏 -->
        <div style="display:flex; gap:8px; flex-wrap:wrap;">
          <button type="button" onclick="copyMorningCardText()" style="flex:1; min-width:140px; display:inline-flex; align-items:center; justify-content:center; gap:6px; background:#1E2638; color:#38BDF8; border:1px solid rgba(56,189,248,0.4); border-radius:8px; padding:8px 12px; font-size:12px; font-weight:700; cursor:pointer; touch-action:manipulation;">
            <span>📋 一键复制晨会操盘卡片</span>
          </button>
          <button type="button" onclick="openReportModal()" style="flex:1; min-width:140px; display:inline-flex; align-items:center; justify-content:center; gap:6px; background:#1E2638; color:#FCD34D; border:1px solid rgba(245,158,11,0.4); border-radius:8px; padding:8px 12px; font-size:12px; font-weight:700; cursor:pointer; touch-action:manipulation;">
            <span>📄 阅读完整深度研报 (75KB)</span>
          </button>
        </div>'''

new_action_bar = '''        <!-- 操盘快捷动作栏：专注晨会战备卡片 (完整研报统一由右上角常驻按钮呼出，避免重复) -->
        <div>
          <button type="button" onclick="copyMorningCardText()" style="width:100%; display:inline-flex; align-items:center; justify-content:center; gap:6px; background:#1E2638; color:#38BDF8; border:1px solid rgba(56,189,248,0.4); border-radius:8px; padding:10px 14px; font-size:13px; font-weight:700; cursor:pointer; touch-action:manipulation;">
            <span>📋 一键复制晨会操盘卡片 (秒发微信/操盘群)</span>
          </button>
        </div>'''

content = content.replace(old_action_bar, new_action_bar)

# 4. 更新底部导航栏为 4 栏架构
tabbar_old_match = re.search(r'<nav class="tabbar">([\s\S]+?)</nav>', content)
if not tabbar_old_match:
    raise RuntimeError("Could not find tabbar")

new_tabbar = '''  <!-- 底部导航栏 (4 栏专业交易流架构：复盘/预案/盯盘/自选) -->
  <nav class="tabbar">
    <button data-key="recap" class="tab-item active" onclick="switchTab('recap', '今日大盘与主线复盘')">
      <svg viewBox="0 0 24 24"><path d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>
      <span>复盘</span>
    </button>
    <button data-key="strategy" class="tab-item" onclick="switchTab('strategy', '明日实战推演与预案')">
      <svg viewBox="0 0 24 24"><path d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
      <span>预案</span>
    </button>
    <button data-key="dingpan" class="tab-item" onclick="switchTab('dingpan', '实时盯盘')">
      <svg viewBox="0 0 24 24"><path d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
      <span>盯盘</span>
    </button>
    <button data-key="focus" class="tab-item" onclick="switchTab('focus', '核心自选池 (Focus)')">
      <svg viewBox="0 0 24 24"><path d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z"/></svg>
      <span>自选</span>
    </button>
  </nav>'''

content = content[:tabbar_old_match.start()] + new_tabbar + content[tabbar_old_match.end():]

# 5. 更新 switchTab 与注入 switchRecapSub 函数
switch_tab_old = '''    // Tab 切换逻辑
    function switchTab(key, title) {
      document.getElementById('page-heading').textContent = title;
      document.querySelectorAll('.tab-pane').forEach(p => {
        p.classList.toggle('active', p.id === 'pane-' + key);
      });
      document.querySelectorAll('.tab-item').forEach(t => {
        t.classList.toggle('active', t.getAttribute('data-key') === key);
      });
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }'''

switch_tab_new = '''    // 复盘二级分段器切换
    function switchRecapSub(sub) {
      const isMood = sub === 'mood';
      const moodBtn = document.getElementById('recap-subtab-mood');
      const crowdBtn = document.getElementById('recap-subtab-crowd');
      const moodView = document.getElementById('recap-view-mood');
      const crowdView = document.getElementById('recap-view-crowd');
      if (moodBtn && crowdBtn && moodView && crowdView) {
        moodBtn.style.background = isMood ? 'var(--sky)' : 'transparent';
        moodBtn.style.color = isMood ? '#0B0E14' : '#94A3B8';
        crowdBtn.style.background = !isMood ? 'var(--sky)' : 'transparent';
        crowdBtn.style.color = !isMood ? '#0B0E14' : '#94A3B8';
        moodView.style.display = isMood ? 'block' : 'none';
        crowdView.style.display = !isMood ? 'block' : 'none';
      }
    }

    // Tab 切换逻辑 (4 栏架构 + 向后兼容)
    function switchTab(key, title) {
      // 兼容历史调用 key
      if (key === 'mood') {
        key = 'recap';
        switchRecapSub('mood');
      } else if (key === 'crowding') {
        key = 'recap';
        switchRecapSub('crowd');
      }
      document.getElementById('page-heading').textContent = title;
      document.querySelectorAll('.tab-pane').forEach(p => {
        p.classList.toggle('active', p.id === 'pane-' + key);
      });
      document.querySelectorAll('.tab-item').forEach(t => {
        t.classList.toggle('active', t.getAttribute('data-key') === key);
      });
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }'''

content = content.replace(switch_tab_old, switch_tab_new)

index_path.write_text(content, encoding="utf-8")
print("✅ mobile_app/index.html successfully updated to 4-tab architecture!")
