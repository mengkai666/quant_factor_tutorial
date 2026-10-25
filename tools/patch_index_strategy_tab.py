from pathlib import Path

index_path = Path("mobile_app/index.html")
content = index_path.read_text(encoding="utf-8")

# 1. Update HTML structure of pane-strategy
old_pane_start = '<!-- ================= TAB 4: 推演预案 ================= -->'
old_pane_end = '<!-- ================= TAB 5: 盯盘 (财联社盯盘页 1:1 复刻) ================= -->'

new_pane_html = '''<!-- ================= TAB 4: 推演预案 (实战作战室) ================= -->
    <section id="pane-strategy" class="tab-pane" style="display:flex; flex-direction:column; gap:12px; padding-bottom:20px;">
      
      <!-- 1. 防守熔断脉冲警告横幅 -->
      <div id="plan-melt-box" style="display:none; background:rgba(239,68,68,0.15); border:1px solid #EF4444; border-radius:12px; padding:10px 14px; align-items:center; justify-content:space-between; gap:10px; box-shadow:0 0 20px rgba(239,68,68,0.25);">
      </div>

      <!-- 2. 策略总纲与快捷动作卡片 -->
      <div class="card" style="margin-bottom:0;">
        <div class="card-title">
          <div style="display:flex; align-items:center; gap:8px;">
            <span style="font-size:16px;">⚔️</span>
            <span id="plan-title" style="font-size:16px; font-weight:800; color:#F0F6FC;">明日实战推演与预案</span>
          </div>
          <span id="plan-pos-badge" class="badge badge-amber" style="font-size:12px; font-weight:800; padding:4px 10px;">建议仓位: --</span>
        </div>
        <div style="font-size:14px; font-weight:700; color:#FCD34D; line-height:1.5; margin-bottom:12px;" id="plan-slogan">--</div>
        
        <!-- 操盘快捷动作栏 -->
        <div style="display:flex; gap:8px; flex-wrap:wrap;">
          <button type="button" onclick="copyMorningCardText()" style="flex:1; min-width:140px; display:inline-flex; align-items:center; justify-content:center; gap:6px; background:#1E2638; color:#38BDF8; border:1px solid rgba(56,189,248,0.4); border-radius:8px; padding:8px 12px; font-size:12px; font-weight:700; cursor:pointer; touch-action:manipulation;">
            <span>📋 一键复制晨会操盘卡片</span>
          </button>
          <button type="button" onclick="openReportModal()" style="flex:1; min-width:140px; display:inline-flex; align-items:center; justify-content:center; gap:6px; background:#1E2638; color:#FCD34D; border:1px solid rgba(245,158,11,0.4); border-radius:8px; padding:8px 12px; font-size:12px; font-weight:700; cursor:pointer; touch-action:manipulation;">
            <span>📄 阅读完整深度研报 (75KB)</span>
          </button>
        </div>
      </div>

      <!-- 3. 游资两大血泪铁律 -->
      <div class="card" style="margin-bottom:0;">
        <div style="font-size:13px; font-weight:700; color:#F0F6FC; margin-bottom:8px; display:flex; align-items:center; gap:6px;">
          <span>🩸</span> 游资实战战法升维 · 两大血泪铁律
        </div>
        <div id="plan-iron-rules" style="display:flex; flex-direction:column; gap:8px;"></div>
      </div>

      <!-- 4. 核心标的分层作战矩阵 (Priority Matrix) -->
      <div class="card" style="margin-bottom:0;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
          <div style="font-size:13px; font-weight:700; color:#F0F6FC; display:flex; align-items:center; gap:6px;">
            <span>🎯</span> 核心标的关注及优先级作战矩阵 (Priority Matrix)
          </div>
          <span style="font-size:11px; color:var(--muted);">逐级执行 · 绝不越级</span>
        </div>
        <div id="plan-priority-matrix" style="display:flex; flex-direction:column; gap:8px;"></div>
      </div>

      <!-- 5. 三大情景推演与应变方案 -->
      <div class="card" style="margin-bottom:0;">
        <div style="font-size:13px; font-weight:700; color:#F0F6FC; margin-bottom:8px; display:flex; align-items:center; gap:6px;">
          <span>🔀</span> 三大情景推演与路径对策 (Scenario Deductions)
        </div>
        <div id="plan-scenarios" style="display:flex; flex-direction:column; gap:8px;"></div>
      </div>

      <!-- 6. 🚫 严禁追高 / 断板避雷池 -->
      <div class="card" style="margin-bottom:0;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
          <div style="font-size:13px; font-weight:700; color:#F87171; display:flex; align-items:center; gap:6px;">
            <span>🚫</span> 不追名单 · 断板避雷池 (Avoid List)
          </div>
          <span style="font-size:11px; color:#F87171; font-weight:600;">次日反包率仅 7.6% · 严禁接盘</span>
        </div>
        <div id="plan-avoid-list" style="display:flex; flex-direction:column; gap:6px;"></div>
      </div>

      <!-- 7. ⏱️ 分时段操盘执行检查表 -->
      <div class="card" style="margin-bottom:0; overflow-x:auto;">
        <div style="font-size:13px; font-weight:700; color:#F0F6FC; margin-bottom:8px; display:flex; align-items:center; gap:6px;">
          <span>⏱️</span> 分时段操盘执行检查表 (Checklist 09:15 - 15:00)
        </div>
        <table style="width:100%; border-collapse:collapse; min-width:480px;">
          <thead>
            <tr style="background:#181E2B; color:var(--muted); font-size:11px; text-align:left;">
              <th style="padding:6px 8px; border-radius:6px 0 0 6px;">时段</th>
              <th style="padding:6px 8px;">监控焦点</th>
              <th style="padding:6px 8px;">量化通过门槛</th>
              <th style="padding:6px 8px; border-radius:0 6px 6px 0;">一票否决熔断</th>
            </tr>
          </thead>
          <tbody id="plan-checklist-table"></tbody>
        </table>
      </div>

      <!-- 8. 底部风控线 -->
      <div style="background:rgba(244,63,94,0.08); border:1px dashed rgba(244,63,94,0.3); border-radius:10px; padding:10px 14px;">
        <div style="font-size:12px; font-weight:700; color:var(--rose);">🛡️ 止损与量化风控红线：</div>
        <div style="font-size:11.5px; color:var(--text); margin-top:3px;" id="plan-defense">
          单日亏损触及本金 2% 或个股跌破分时均线/EMA20 支撑无条件触发止损纪律，严禁扛单与盲目补仓！
        </div>
      </div>

    </section>

    '''

idx1 = content.find(old_pane_start)
idx2 = content.find(old_pane_end)
assert idx1 != -1 and idx2 != -1, "old pane markers not found"
content = content[:idx1] + new_pane_html + content[idx2:]

# 2. Update renderStrategy JavaScript function
old_fn_start = 'function renderStrategy(strategy) {'
old_fn_end = 'function formatTime(unixSec) {'

new_fn_code = '''function copyMorningCardText() {
      const card = window.__CURRENT_MORNING_CARD__ || '';
      if (!card) {
        showToast('⚠️ 当前日期暂无晨会卡片文本');
        return;
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(card).then(() => {
          showToast('✅ 晨会操盘内参已复制到剪贴板！');
        }).catch(() => {
          prompt('请手动复制晨会卡片：', card);
        });
      } else {
        prompt('请手动复制晨会卡片：', card);
      }
    }

    function renderStrategy(strategy) {
      if (!strategy) return;
      window.__CURRENT_MORNING_CARD__ = strategy.morning_card || '';

      // 1. 标题与仓位
      const titleEl = document.getElementById('plan-title');
      if (titleEl) titleEl.textContent = strategy.title || '实战推演与预案';
      const posBadge = document.getElementById('plan-pos-badge');
      if (posBadge) {
        posBadge.textContent = `建议仓位: ${strategy.recommended_position || '--'}`;
        if (strategy.is_melt) {
          posBadge.className = 'badge badge-rose';
        } else if ((strategy.recommended_position || '').includes('5') || (strategy.recommended_position || '').includes('7')) {
          posBadge.className = 'badge badge-emerald';
        } else {
          posBadge.className = 'badge badge-amber';
        }
      }

      // 2. 熔断横幅
      const meltBox = document.getElementById('plan-melt-box');
      if (meltBox) {
        if (strategy.is_melt && strategy.melt_text) {
          meltBox.style.display = 'flex';
          meltBox.innerHTML = `
            <div style="display:flex; align-items:flex-start; gap:8px;">
              <span style="font-size:16px; line-height:1.2;">🚨</span>
              <div style="font-size:12px; font-weight:700; color:#FCA5A5; line-height:1.4;">${strategy.melt_text}</div>
            </div>
            <span style="background:rgba(244,63,94,0.25); color:#FDA4AF; border:1px solid #F43F5E; padding:2px 8px; border-radius:12px; font-size:11px; font-weight:700; white-space:nowrap; align-self:flex-start;">熔断状态</span>
          `;
        } else {
          meltBox.style.display = 'none';
        }
      }

      // 3. 核心总纲
      const sloganEl = document.getElementById('plan-slogan');
      if (sloganEl) sloganEl.textContent = `🎯 核心策略: ${strategy.action_slogan || '--'}`;

      // 4. 游资两大铁律
      const ironBox = document.getElementById('plan-iron-rules');
      if (ironBox) {
        const rules = strategy.iron_rules || [
          { tag: '🚨 铁律一 · 断板反包禁令', sub: '次日反包率仅 7.6%，冲高一律诱多！', detail: '严禁低吸、抄底、搏首阴！重点回避高位断板被核标的。' },
          { tag: '⚠️ 铁律二 · 冰点修复陷阱', sub: '无题材梯队共振脉冲多为假冲天炮！', detail: '存量出逃诱多只看不追，早盘 10:00 前严禁盲目开新仓！' }
        ];
        ironBox.innerHTML = rules.map(r => `
          <div style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.08); border-radius:8px; padding:8px 10px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:3px;">
              <span style="font-size:12px; font-weight:700; color:#FCD34D;">${r.tag}</span>
              <span style="font-size:11px; color:var(--rose); font-weight:600;">${r.sub}</span>
            </div>
            <div style="font-size:11px; color:var(--muted); line-height:1.4;">${r.detail}</div>
          </div>
        `).join('');
      }

      // 5. 核心标的分层作战矩阵 (Priority Matrix)
      const matrixBox = document.getElementById('plan-priority-matrix');
      if (matrixBox) {
        const pList = strategy.priority_matrix || [];
        if (pList.length > 0) {
          matrixBox.innerHTML = pList.map(p => {
            const isP0 = p.tier === 'P0';
            const isP3 = p.tier === 'P3';
            const badgeBg = isP0 ? 'rgba(56,189,248,0.2)' : isP3 ? 'rgba(244,63,94,0.2)' : 'rgba(245,158,11,0.2)';
            const badgeColor = isP0 ? '#38BDF8' : isP3 ? '#F87171' : '#FCD34D';
            const borderColor = isP0 ? 'rgba(56,189,248,0.3)' : isP3 ? 'rgba(244,63,94,0.3)' : 'rgba(245,158,11,0.3)';
            return `
              <div style="background:var(--card-sub); border:1px solid ${borderColor}; border-radius:10px; padding:10px 12px; display:flex; flex-direction:column; gap:6px;">
                <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid rgba(255,255,255,0.06); padding-bottom:6px;">
                  <div style="display:flex; align-items:center; gap:6px;">
                    <span style="background:${badgeBg}; color:${badgeColor}; font-weight:800; font-size:12px; padding:2px 8px; border-radius:6px; border:1px solid ${borderColor};">${p.tier}</span>
                    <span style="font-size:13px; font-weight:700; color:#FFF;">${p.title}</span>
                  </div>
                  <span style="font-size:11px; color:${badgeColor}; font-weight:600;">${p.badge}</span>
                </div>
                <div style="font-size:11.5px; line-height:1.5;">
                  <div style="color:#E2E8F0;"><span style="color:#38BDF8; font-weight:700;">🎯 触发：</span>${p.trigger}</div>
                  <div style="color:#94A3B8; margin-top:2px;"><span style="color:#F87171; font-weight:700;">🛡️ 止损：</span>${p.stop}</div>
                </div>
              </div>
            `;
          }).join('');
        } else {
          matrixBox.innerHTML = `<div style="font-size:12px; color:var(--muted); text-align:center; padding:10px;">暂无明确高优先级标的，执行防守观察</div>`;
        }
      }

      // 6. 三大情景推演与应变方案
      const scenarioBox = document.getElementById('plan-scenarios');
      if (scenarioBox) {
        const scList = strategy.scenarios || [];
        if (scList.length > 0) {
          scenarioBox.innerHTML = scList.map(s => {
            const isRose = s.color === 'rose';
            const isRed = s.color === 'red';
            const borderCol = isRed ? '#EF4444' : isRose ? '#F43F5E' : '#F59E0B';
            const bgCol = isRed ? 'rgba(239,68,68,0.08)' : isRose ? 'rgba(244,63,94,0.08)' : 'rgba(245,158,11,0.08)';
            return `
              <div style="background:${bgCol}; border:1px solid ${borderCol}; border-radius:8px; padding:8px 10px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                  <span style="font-size:12px; font-weight:700; color:#FFF;">${s.name}</span>
                  <span style="font-size:11px; font-weight:700; color:${borderCol};">${s.pos}</span>
                </div>
                <div style="font-size:11px; color:var(--text); line-height:1.4;">
                  <div>• <b>前提：</b>${s.premise}</div>
                  <div>• <b>竞价：</b>${s.auction}</div>
                  <div>• <b>动作：</b>${s.action}</div>
                </div>
              </div>
            `;
          }).join('');
        }
      }

      // 7. 🚫 严禁追高 / 断板避雷池
      const avoidBox = document.getElementById('plan-avoid-list');
      if (avoidBox) {
        const avoids = strategy.avoid_list || [];
        avoidBox.innerHTML = avoids.map(a => `
          <div style="background:rgba(239,68,68,0.12); border:1px solid rgba(239,68,68,0.3); border-radius:6px; padding:6px 10px; font-size:11.5px; color:#FCA5A5; line-height:1.4;">
            ⚠️ <b>${a}</b>
          </div>
        `).join('');
      }

      // 8. ⏱️ 分时段操盘执行检查表
      const checkTable = document.getElementById('plan-checklist-table');
      if (checkTable) {
        const cks = strategy.checklist || [];
        checkTable.innerHTML = cks.map(c => `
          <tr style="border-bottom:1px solid rgba(255,255,255,0.06); font-size:11px;">
            <td style="padding:8px 6px; font-weight:700; color:#38BDF8; white-space:nowrap;">${c.time}</td>
            <td style="padding:8px 6px; color:#E2E8F0;">${c.focus}</td>
            <td style="padding:8px 6px; color:#34D399;">${c.pass_cond}</td>
            <td style="padding:8px 6px; color:#F87171; font-weight:600;">${c.fail_cond}</td>
          </tr>
        `).join('');
      }

      // 9. 止损底线
      const defEl = document.getElementById('plan-defense');
      if (defEl) defEl.textContent = strategy.defense_line || '单日亏损触及本金 2% 或个股跌破分时均线/EMA20 支撑无条件触发止损纪律，严禁扛单与盲目补仓！';
    }

    '''

f_idx1 = content.find(old_fn_start)
f_idx2 = content.find(old_fn_end)
assert f_idx1 != -1 and f_idx2 != -1, "old fn markers not found"
content = content[:f_idx1] + new_fn_code + content[f_idx2:]

index_path.write_text(content, encoding="utf-8")
print("✅ mobile_app/index.html updated with rich plan battle room!")
