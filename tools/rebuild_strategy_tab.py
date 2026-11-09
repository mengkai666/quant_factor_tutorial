# -*- coding: utf-8 -*-
"""
rebuild_strategy_tab.py
彻底重构 mobile_app/index.html 中的 Tab 4 (预案 Strategy)
打造符合顶尖私募交易室的「金字塔作战信息流」：
1. 首屏作战总指挥：大字建议仓位 + 唯一主线焦点 + 三大纪律药丸 + 晨会一键复制
2. 核心标的 Trader Action Cards：16px大字白底标的名、高对比度买点与止损框、MA5/异动安全垫微型指标条
3. 重点抱团可转债 T+0 作战看板：大字股债联动对账条
4. 分时段实战检查表：09:25 / 09:35 / 10:00 / 14:00 各具差异化量化门槛
5. 不追名单·避雷池：大号警示 Tag Badges
6. 折叠抽屉：严重异动安全雷达 & 速算器（按需展开，不挤占首屏）
7. 折叠抽屉：三大情景推演与游资两大铁律（按需展开）
"""

from pathlib import Path
import re

INDEX_PATH = Path("mobile_app/index.html")

NEW_PANE_STRATEGY_HTML = '''    <section id="pane-strategy" class="tab-pane" style="padding-bottom:24px;">
      
      <!-- 1. 防守熔断/分歧预警横幅（单行大字胶囊） -->
      <div id="plan-melt-box" style="display:none; background:rgba(239,68,68,0.12); border:1px solid rgba(239,68,68,0.4); border-radius:10px; padding:8px 12px; align-items:center; justify-content:space-between; gap:10px; box-shadow:0 4px 16px rgba(0,0,0,0.3); margin-bottom:10px;">
      </div>

      <!-- 2. 今日作战总指挥大盘（首屏视觉焦点：仓位与基调一目了然） -->
      <div class="card" style="margin-bottom:10px; background:linear-gradient(180deg, rgba(30,41,59,0.7) 0%, var(--card) 100%); border:1px solid rgba(56,189,248,0.25);">
        <div class="card-title" style="margin-bottom:6px;">
          <div style="display:flex; align-items:center; gap:8px;">
            <span style="font-size:16px;">⚔️</span>
            <span id="plan-title" style="font-size:16px; font-weight:800; color:#F0F6FC;">明日实战推演与预案</span>
          </div>
          <span id="plan-pos-badge" class="badge badge-amber" style="font-size:13px; font-weight:800; padding:4px 10px;">建议仓位: --</span>
        </div>
        <div style="font-size:14px; font-weight:700; color:#FCD34D; line-height:1.5; margin-bottom:10px;" id="plan-slogan">--</div>
        
        <!-- 一键复制晨会操盘卡片 -->
        <div>
          <button type="button" onclick="copyMorningCardText()" style="width:100%; display:inline-flex; align-items:center; justify-content:center; gap:6px; background:#1E2638; color:#38BDF8; border:1px solid rgba(56,189,248,0.4); border-radius:8px; padding:10px 14px; font-size:13px; font-weight:700; cursor:pointer; touch-action:manipulation;">
            <span>📋 一键复制晨会操盘卡片 (秒发微信/操盘群)</span>
          </button>
        </div>
      </div>

      <!-- 3. 🎯 核心风向标标的 · 明日实战预案 (首屏直接给标的与执行指令) -->
      <div class="card" style="margin-bottom:10px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
          <div style="font-size:14px; font-weight:800; color:#F0F6FC; display:flex; align-items:center; gap:6px;">
            <span>🎯</span> <span id="plan-stocks-heading">核心风向标标的 · 实战作战指令</span>
          </div>
          <span id="plan-stocks-mode-tag" style="font-size:11.5px; color:#38BDF8; font-weight:700;">大字看板 · 结果导向</span>
        </div>
        <div id="plan-baotuan-stocks" style="display:flex; flex-direction:column; gap:12px;"></div>
      </div>

      <!-- 4. ⚡ 重点抱团可转债 · 明日 T+0 作战预案 -->
      <div class="card" style="margin-bottom:10px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; flex-wrap:wrap; gap:6px;">
          <div style="font-size:14px; font-weight:800; color:#F0F6FC; display:flex; align-items:center; gap:6px;">
            <span>⚡</span> <span id="plan-cb-heading">重点抱团可转债 · 明日 T+0 作战预案</span>
          </div>
          <div style="display:flex; align-items:center; gap:6px;">
            <span id="plan-cb-mode-tag" style="font-size:11.5px; color:#A78BFA; font-weight:600;">股债联动对账</span>
            <button type="button" onclick="currentReportType='cb'; openReportModal();" style="background:rgba(167,139,250,0.15); border:1px solid rgba(167,139,250,0.4); color:#C4B5FD; border-radius:6px; padding:3px 9px; font-size:11.5px; font-weight:700; cursor:pointer;">📄 转债原篇 →</button>
          </div>
        </div>
        <div id="plan-baotuan-cb" style="display:flex; flex-direction:column; gap:8px;"></div>
      </div>

      <!-- 5. ⏱️ 关键时刻盘中检查表 (Checklist 09:25 - 15:00) -->
      <div class="card" style="margin-bottom:10px; overflow-x:auto;">
        <div style="font-size:13.5px; font-weight:800; color:#F0F6FC; margin-bottom:8px; display:flex; align-items:center; gap:6px;">
          <span>⏱️</span> 分时段实战检查表 (Checklist 09:25 - 15:00)
        </div>
        <table style="width:100%; border-collapse:collapse; min-width:480px;">
          <thead>
            <tr style="background:#181E2B; color:var(--muted); font-size:11.5px; text-align:left;">
              <th style="padding:7px 8px; border-radius:6px 0 0 6px;">时段</th>
              <th style="padding:7px 8px;">监控焦点</th>
              <th style="padding:7px 8px;">量化通过门槛</th>
              <th style="padding:7px 8px; border-radius:0 6px 6px 0;">一票否决熔断</th>
            </tr>
          </thead>
          <tbody id="plan-checklist-table"></tbody>
        </table>
      </div>

      <!-- 6. 🚫 不追名单 · 断板避雷池（大号警示标签流） -->
      <div class="card" style="margin-bottom:10px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
          <div style="font-size:13.5px; font-weight:800; color:#F87171; display:flex; align-items:center; gap:6px;">
            <span>🚫</span> 不追名单 · 断板避雷池
          </div>
          <span style="font-size:11.5px; color:#FCA5A5; font-weight:700;">次日反包率仅 7.6% · 严禁接飞刀</span>
        </div>
        <div id="plan-avoid-list" style="display:flex; flex-wrap:wrap; gap:8px;"></div>
      </div>

      <!-- 7. ⚡ 折叠工具抽屉：严重异动安全雷达 & 交互速算器（按需展开，不抢占首屏） -->
      <details class="card" style="margin-bottom:10px; border:1px solid rgba(244,63,94,0.25); background:rgba(15,23,42,0.6);">
        <summary style="cursor:pointer; display:flex; justify-content:space-between; align-items:center; font-size:13px; font-weight:700; color:#FDA4AF; padding:2px 0;">
          <div style="display:flex; align-items:center; gap:6px;">
            <span>⚡</span> <span>严重异动安全雷达 (10日100% / 30日200%) & 速算器</span>
          </div>
          <span style="font-size:11.5px; color:#94A3B8; font-weight:normal;">展开测算 ▾</span>
        </summary>
        <div style="margin-top:10px; padding-top:10px; border-top:1px solid rgba(255,255,255,0.06);">
          <div style="font-size:12px; color:var(--muted); line-height:1.5; margin-bottom:8px;">
            交易所对10日偏离达100%或30日达200%的个股执行停牌核查/重点监控。主力资金常在触线前夕<b>主动断板避险</b>：
          </div>
          <div id="plan-abnormal-radar-list" style="display:flex; flex-direction:column; gap:6px;">
            <div style="font-size:12px; color:var(--muted); text-align:center; padding:10px;">正在加载异动数据...</div>
          </div>
          <div style="margin-top:10px; background:rgba(0,0,0,0.25); border:1px solid var(--border); border-radius:8px; padding:8px 10px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
              <span style="font-size:12px; font-weight:700; color:#38BDF8;">🧮 严重异动涨幅安全空间速算器</span>
              <span style="font-size:10.5px; color:var(--muted);">输入次日模拟</span>
            </div>
            <div style="display:flex; gap:6px; margin-bottom:6px;">
              <input type="text" id="calc-stock-input" placeholder="输入10日已涨幅%(如65)或代码" style="flex:1; background:var(--card-sub); border:1px solid var(--border); border-radius:6px; padding:6px 8px; color:#FFF; font-size:12px;">
              <select id="calc-limit-type" style="background:var(--card-sub); border:1px solid var(--border); border-radius:6px; padding:6px 8px; color:#FFF; font-size:12px;">
                <option value="10">主板 (+10%)</option>
                <option value="20">创业/科创 (+20%)</option>
              </select>
              <button onclick="calculateAbnormalQuick()" style="background:var(--sky); color:#0B0E14; border:none; border-radius:6px; padding:0 12px; font-size:12px; font-weight:700; cursor:pointer;">测算</button>
            </div>
            <div id="calc-result-box" style="font-size:11.5px; color:var(--muted); line-height:1.5; display:none; background:rgba(56,189,248,0.08); padding:6px 8px; border-radius:6px; border:1px dashed rgba(56,189,248,0.3);">
            </div>
          </div>
        </div>
      </details>

      <!-- 8. 🔀 折叠抽屉：三大情景推演与应变方案 + 游资铁律（按需展开） -->
      <details class="card" style="margin-bottom:10px;">
        <summary style="cursor:pointer; display:flex; justify-content:space-between; align-items:center; font-size:13px; font-weight:700; color:#F0F6FC; padding:2px 0;">
          <div style="display:flex; align-items:center; gap:6px;">
            <span>🔀</span> <span>三大情景推演与游资两大铁律</span>
          </div>
          <span style="font-size:11.5px; color:#94A3B8; font-weight:normal;">展开推演 ▾</span>
        </summary>
        <div style="margin-top:10px; padding-top:10px; border-top:1px solid rgba(255,255,255,0.06); display:flex; flex-direction:column; gap:10px;">
          <div id="plan-scenarios" style="display:flex; flex-direction:column; gap:8px;"></div>
          <div style="font-size:12.5px; font-weight:700; color:#FCD34D; margin-top:4px;">🩸 游资实战战法升维 · 两大血泪铁律</div>
          <div id="plan-iron-rules" style="display:flex; flex-direction:column; gap:6px;"></div>
        </div>
      </details>

      <!-- 9. 底部风控线 -->
      <div style="background:rgba(244,63,94,0.08); border:1px dashed rgba(244,63,94,0.3); border-radius:10px; padding:10px 14px;">
        <div style="font-size:12.5px; font-weight:700; color:var(--rose);">🛡️ 止损与量化风控红线：</div>
        <div style="font-size:12px; color:var(--text); margin-top:4px; line-height:1.55;" id="plan-defense">
          单日亏损触及本金 2% 或个股跌破分时均线/EMA20 支撑无条件触发止损纪律，严禁扛单与盲目补仓！
        </div>
      </div>

    </section>'''

NEW_RENDER_STRATEGY_JS = '''    function renderStrategy(strategy) {
      if (!strategy) return;
      window.__CURRENT_MORNING_CARD__ = strategy.morning_card || '';

      // 1. 标题与建议仓位 (超大字徽章，一目了然)
      const titleEl = document.getElementById('plan-title');
      if (titleEl) titleEl.textContent = strategy.title || '明日实战推演与预案';
      const posBadge = document.getElementById('plan-pos-badge');
      if (posBadge) {
        posBadge.textContent = `建议仓位: ${strategy.recommended_position || '--'}`;
        posBadge.style.fontSize = '13px';
        posBadge.style.fontWeight = '800';
        posBadge.style.padding = '4px 10px';
        if (strategy.is_melt) {
          posBadge.className = 'badge badge-rose';
        } else if ((strategy.recommended_position || '').includes('5') || (strategy.recommended_position || '').includes('7')) {
          posBadge.className = 'badge badge-emerald';
        } else {
          posBadge.className = 'badge badge-amber';
        }
      }

      // 2. 顶部分歧熔断警示 (单行大字胶囊，去除冗长小作文)
      const meltBox = document.getElementById('plan-melt-box');
      if (meltBox) {
        if (strategy.is_melt) {
          meltBox.style.display = 'flex';
          meltBox.style.background = 'rgba(239,68,68,0.15)';
          meltBox.style.border = '1px solid rgba(239,68,68,0.5)';
          meltBox.style.padding = '8px 12px';
          meltBox.innerHTML = `
            <div style="display:flex; align-items:center; gap:8px;">
              <span style="font-size:16px;">⚠️</span>
              <span style="font-size:13px; font-weight:800; color:#FCA5A5; letter-spacing:0.3px;">
                分歧熔断生效：严控 1~3 成仓位 · 聚焦主线活口 · 严禁接飞刀杂毛
              </span>
            </div>
            <span style="background:#EF4444; color:#FFF; padding:2px 8px; border-radius:6px; font-size:11px; font-weight:800; white-space:nowrap;">
              防守状态
            </span>
          `;
        } else {
          meltBox.style.display = 'none';
        }
      }

      // 3. 核心总纲 (大字清晰，突出主线与三大铁律)
      const sloganEl = document.getElementById('plan-slogan');
      if (sloganEl) {
        sloganEl.innerHTML = `
          <div style="display:flex; flex-direction:column; gap:6px;">
            <div style="font-size:14.5px; font-weight:800; color:#FCD34D; display:flex; align-items:center; gap:6px;">
              <span>🎯</span> <span>唯一主线：新能源电网（13只涨停 · 5只连板）</span>
            </div>
            <div style="display:flex; flex-wrap:wrap; gap:6px; margin-top:2px;">
              <span style="background:rgba(56,189,248,0.15); border:1px solid rgba(56,189,248,0.35); color:#7DD3FC; padding:2px 8px; border-radius:5px; font-size:11.5px; font-weight:700;">
                砍杂毛做活口
              </span>
              <span style="background:rgba(245,158,11,0.15); border:1px solid rgba(245,158,11,0.35); color:#FDE68A; padding:2px 8px; border-radius:5px; font-size:11.5px; font-weight:700;">
                回踩 MA5 低吸
              </span>
              <span style="background:rgba(239,68,68,0.15); border:1px solid rgba(239,68,68,0.35); color:#FCA5A5; padding:2px 8px; border-radius:5px; font-size:11.5px; font-weight:700;">
                跌停板一票否决
              </span>
            </div>
          </div>
        `;
      }

      // 4. 重点个股实战作战看板 (Trader Action Cards · 大字、高对比度、无废话)
      const bStocksBox = document.getElementById('plan-baotuan-stocks');
      if (bStocksBox) {
        const sList = strategy.baotuan_stock_targets || [];
        if (sList.length > 0) {
          bStocksBox.innerHTML = sList.map(s => {
            const isBlack = (s.role || '').includes('禁买') || (s.role || '').includes('⛔') || s.tag === 'avoid_black';
            const roleBadgeCol = isBlack ? '#EF4444' : ((s.role || '').includes('空间') ? '#F87171' : ((s.role || '').includes('梯队') ? '#38BDF8' : '#FCD34D'));
            const detailsArr = Array.isArray(s.stock_details) ? s.stock_details : [];

            // 渲染该分类下的逐股交易员大卡片
            const cardsHtml = detailsArr.map(d => {
              const isDanger = d.risk_tag === 'danger' || isBlack;
              const cardBorder = isDanger ? 'rgba(239,68,68,0.45)' : 'rgba(56,189,248,0.32)';
              const cardBg = isDanger ? 'rgba(239,68,68,0.08)' : 'linear-gradient(180deg, rgba(30,41,59,0.7) 0%, rgba(15,23,42,0.85) 100%)';
              
              // 次要对照标的用紧凑条显示，避免反客为主
              if (d.is_primary === false) {
                return `
                  <div style="background:rgba(255,255,255,0.03); border:1px dashed rgba(255,255,255,0.15); border-radius:8px; padding:7px 10px; display:flex; justify-content:space-between; align-items:center; font-size:12px;">
                    <div>
                      <span style="color:#94A3B8;">对照标的：</span>
                      <b style="color:#FFF;">${d.name}</b>
                      <span style="color:var(--muted); font-size:10.5px; font-family:monospace; margin-left:4px;">${d.code || ''}</span>
                      <span style="color:#CBD5E1; margin-left:6px;">${d.badge || ''}</span>
                    </div>
                    <span style="color:#FCD34D; font-size:11px;">${d.buy_trigger || '仅作情绪参照'}</span>
                  </div>
                `;
              }

              return `
                <div style="background:${cardBg}; border:1px solid ${cardBorder}; border-radius:10px; padding:10px 12px; display:flex; flex-direction:column; gap:8px; box-shadow:0 3px 12px rgba(0,0,0,0.25);">
                  
                  <!-- 卡片头部：标的大字、身份标签、现价与涨跌幅 -->
                  <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div style="display:flex; align-items:center; gap:8px;">
                      <b style="font-size:16px; color:#FFFFFF; letter-spacing:0.5px;">${d.name}</b>
                      <span style="font-size:11px; color:#94A3B8; font-family:monospace;">${d.code || ''}</span>
                      <span style="font-size:11px; font-weight:700; color:#FDE68A; background:rgba(245,158,11,0.2); border:1px solid rgba(245,158,11,0.4); padding:1px 6px; border-radius:4px;">
                        ${d.badge || ''}
                      </span>
                    </div>
                    <div style="text-align:right;">
                      <span style="font-size:14px; font-weight:800; color:#38BDF8; font-family:monospace;">${d.latest_close || ''}</span>
                      <span style="font-size:12px; font-weight:700; color:#F87171; margin-left:4px;">${d.r1_pct || ''}</span>
                    </div>
                  </div>

                  <!-- 核心买卖动作框 (大号高对比度字，一目了然) -->
                  <div style="background:rgba(0,0,0,0.35); border-radius:8px; padding:8px 10px; display:flex; flex-direction:column; gap:6px;">
                    <div style="font-size:13px; line-height:1.45; color:#E2E8F0;">
                      <span style="font-size:11px; font-weight:800; background:rgba(56,189,248,0.25); color:#38BDF8; border:1px solid rgba(56,189,248,0.5); padding:1px 6px; border-radius:4px; margin-right:6px; display:inline-block;">
                        ${isDanger ? '风控禁令' : '⚡ 买点触发'}
                      </span>
                      <b style="color:${isDanger ? '#FCA5A5' : '#BAE6FD'}; font-size:13px;">${d.buy_trigger || '--'}</b>
                    </div>
                    <div style="font-size:12.5px; line-height:1.45; color:#E2E8F0;">
                      <span style="font-size:11px; font-weight:800; background:rgba(239,68,68,0.25); color:#F87171; border:1px solid rgba(239,68,68,0.5); padding:1px 6px; border-radius:4px; margin-right:6px; display:inline-block;">
                        🛡️ 止损红线
                      </span>
                      <span style="color:#FDA4AF; font-weight:600;">${d.stop_loss || '--'}</span>
                    </div>
                  </div>

                  <!-- 底部微型数据对账条 -->
                  <div style="display:flex; justify-content:space-between; align-items:center; font-size:11px; color:#94A3B8; padding:0 2px;">
                    <div>
                      <span>MA5: <b style="color:#E2E8F0;">${d.ma5_px || '--'}</b></span>
                      <span style="margin-left:10px;">安全垫: <b style="color:#38BDF8;">${d.boards_left_str || '--'}</b></span>
                    </div>
                    <div style="color:#A78BFA; font-weight:700;">
                      ${d.judgment ? `研判: ${d.judgment}` : ''}
                    </div>
                  </div>

                </div>
              `;
            }).join('');

            return `
              <div style="display:flex; flex-direction:column; gap:6px; margin-bottom:4px;">
                <div style="display:flex; justify-content:space-between; align-items:center; border-left:3px solid ${roleBadgeCol}; padding-left:8px;">
                  <span style="font-size:13.5px; font-weight:800; color:#FFF;">${s.role}</span>
                  <span style="font-size:11.5px; font-weight:800; color:${roleBadgeCol}; background:rgba(255,255,255,0.06); padding:2px 8px; border-radius:4px;">
                    建议仓位: ${s.pos || '--'}
                  </span>
                </div>
                ${cardsHtml}
              </div>
            `;
          }).join('');
        }
      }

      // 5. 重点可转债 T+0 作战看板 (大字对账条)
      const bCbBox = document.getElementById('plan-baotuan-cb');
      if (bCbBox) {
        const cList = strategy.baotuan_cb_targets || [];
        if (cList.length > 0) {
          bCbBox.innerHTML = cList.map(c => {
            const isBlack = (c.role || '').includes('破位') || (c.role || '').includes('禁买');
            const bgCol = isBlack ? 'rgba(239,68,68,0.08)' : 'linear-gradient(180deg, rgba(30,41,59,0.7) 0%, rgba(15,23,42,0.85) 100%)';
            const borderCol = isBlack ? 'rgba(239,68,68,0.35)' : 'rgba(167,139,250,0.3)';
            return `
              <div style="background:${bgCol}; border:1px solid ${borderCol}; border-radius:10px; padding:10px 12px; display:flex; flex-direction:column; gap:6px;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                  <div style="display:flex; align-items:center; gap:6px;">
                    <b style="font-size:15px; color:#FFF;">${c.bond_name}</b>
                    <span style="font-size:11px; color:#94A3B8; font-family:monospace;">${c.bond_code || ''}</span>
                    <span style="font-size:11px; font-weight:700; color:#C4B5FD; background:rgba(167,139,250,0.2); padding:1px 6px; border-radius:4px;">
                      ${c.role.replace(/【|】/g, '')}
                    </span>
                  </div>
                  <div style="font-size:11.5px; color:#38BDF8;">
                    正股: <b style="color:#FFF;">${c.stock_name}</b> <span style="color:#FCD34D;">(${c.stock_pct || '--'})</span>
                  </div>
                </div>

                <div style="background:rgba(0,0,0,0.3); border-radius:6px; padding:6px 8px; font-size:12.5px; line-height:1.45;">
                  <span style="font-size:11px; font-weight:800; background:rgba(167,139,250,0.25); color:#C4B5FD; border:1px solid rgba(167,139,250,0.5); padding:1px 5px; border-radius:3px; margin-right:4px;">
                    ${isBlack ? '风控' : 'T+0 指令'}
                  </span>
                  <b style="color:${isBlack ? '#FCA5A5' : '#E2E8F0'};">${c.action || '--'}</b>
                </div>

                <div style="display:flex; justify-content:space-between; align-items:center; font-size:11px; color:#94A3B8;">
                  <span>${c.bond_metrics || ''}</span>
                  <span style="color:#F87171; font-weight:600;">止损: ${c.stop || '--'}</span>
                </div>
              </div>
            `;
          }).join('');
        }
      }

      // 6. 分时检查表 (大字清晰)
      const checkTable = document.getElementById('plan-checklist-table');
      if (checkTable) {
        const cks = strategy.checklist || [];
        checkTable.innerHTML = cks.map(c => `
          <tr style="border-bottom:1px solid rgba(255,255,255,0.06); font-size:12px;">
            <td style="padding:8px 6px; font-weight:800; color:#38BDF8; white-space:nowrap;">${c.time}</td>
            <td style="padding:8px 6px; color:#E2E8F0;">${c.focus}</td>
            <td style="padding:8px 6px; color:#34D399; font-weight:600;">${c.pass_cond}</td>
            <td style="padding:8px 6px; color:#F87171; font-weight:700;">${c.fail_cond}</td>
          </tr>
        `).join('');
      }

      // 7. 不追名单 · 避雷池 (大号警示标签流)
      const avoidBox = document.getElementById('plan-avoid-list');
      if (avoidBox) {
        const avoids = strategy.avoid_list || [];
        avoidBox.innerHTML = avoids.map(a => {
          const parts = a.split('：');
          const title = parts[0] || a;
          const note = parts[1] || '';
          return `
            <div style="background:rgba(239,68,68,0.12); border:1px solid rgba(239,68,68,0.35); border-radius:6px; padding:4px 9px; font-size:12px; color:#FCA5A5; display:inline-flex; align-items:center; gap:5px;">
              <span>⛔</span> <b style="color:#FFF;">${title}</b> ${note ? `<span style="font-size:11px; color:#FDA4AF; opacity:0.85;">(${note})</span>` : ''}
            </div>
          `;
        }).join('');
      }

      // 8. 严重异动安全雷达专区 (折叠抽屉内)
      const abnormalBox = document.getElementById('plan-abnormal-radar-list');
      if (abnormalBox) {
        const radarList = strategy.abnormal_radar || [];
        if (radarList.length > 0) {
          abnormalBox.innerHTML = radarList.map(item => {
            const isDanger = item.risk_level === 'danger';
            const isWarning = item.risk_level === 'warning';
            const badgeClass = isDanger ? 'badge-rose' : (isWarning ? 'badge-amber' : 'badge-emerald');
            const bgBox = isDanger ? 'rgba(239,68,68,0.1)' : (isWarning ? 'rgba(245,158,11,0.08)' : 'rgba(16,185,129,0.06)');
            const borderBox = isDanger ? 'rgba(239,68,68,0.3)' : (isWarning ? 'rgba(245,158,11,0.25)' : 'rgba(16,185,129,0.2)');
            return `
              <div style="background:${bgBox}; border:1px solid ${borderBox}; border-radius:6px; padding:6px 9px; display:flex; justify-content:space-between; align-items:center;">
                <div>
                  <div style="display:flex; align-items:center; gap:5px;">
                    <b style="color:#FFF; font-size:12px;">${item.name}</b>
                    <span style="font-size:10px; color:var(--muted); font-family:monospace;">${item.code}</span>
                    <span style="font-size:10px; background:rgba(255,255,255,0.06); padding:1px 4px; border-radius:3px; color:#CBD5E1;">${item.sector || '高标'}</span>
                  </div>
                  <div style="font-size:10.5px; color:var(--muted); margin-top:2px;">
                    10日涨幅: <b style="color:var(--text);">${item.ret10}%</b> · 距100%安全垫: <b style="color:${isDanger?'#F87171':'#38BDF8'};">${item.safety10}%</b>
                  </div>
                </div>
                <div style="text-align:right;">
                  <span class="badge ${badgeClass}" style="font-size:9.5px; padding:1px 5px;">${item.risk_text || '正常'}</span>
                  <div style="font-size:10px; color:var(--muted); margin-top:1px;">
                    余量: <b>${item.boards_left != null ? item.boards_left + ' 板' : '--'}</b>
                  </div>
                </div>
              </div>
            `;
          }).join('');
        }
      }

      // 9. 三大情景推演与应变方案 (折叠抽屉内)
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
              <div style="background:${bgCol}; border:1px solid ${borderCol}40; border-radius:6px; padding:7px 9px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:3px;">
                  <span style="font-size:12px; font-weight:700; color:#FFF;">${s.name}</span>
                  <span style="font-size:11.5px; font-weight:700; color:${borderCol};">${s.pos}</span>
                </div>
                <div style="font-size:11.5px; color:var(--text); line-height:1.45;">
                  <div>• <b>前提：</b>${s.premise}</div>
                  <div>• <b>竞价：</b>${s.auction}</div>
                  <div>• <b>动作：</b>${s.action}</div>
                </div>
              </div>
            `;
          }).join('');
        }
      }

      // 10. 游资两大铁律 (折叠抽屉内)
      const ironBox = document.getElementById('plan-iron-rules');
      if (ironBox) {
        const rules = strategy.iron_rules || [
          { tag: '🚨 铁律一 · 断板反包禁令', sub: '次日反包率不足8%，冲高一律诱多！', detail: '严禁低吸、抄底、搏首阴！重点回避高位断板被核标的。' },
          { tag: '⚠️ 铁律二 · 冰点修复陷阱', sub: '无题材梯队共振脉冲多为假冲天炮！', detail: '存量出逃诱多只看不追，早盘 10:00 前严禁盲目开新仓！' }
        ];
        ironBox.innerHTML = rules.map(r => `
          <div style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.07); border-radius:6px; padding:6px 8px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:2px;">
              <span style="font-size:11.5px; font-weight:700; color:#FCD34D;">${r.tag}</span>
              <span style="font-size:10.5px; color:var(--rose); font-weight:600;">${r.sub}</span>
            </div>
            <div style="font-size:11px; color:var(--muted); line-height:1.4;">${r.detail}</div>
          </div>
        `).join('');
      }

      // 11. 止损底线
      const defEl = document.getElementById('plan-defense');
      if (defEl) defEl.textContent = strategy.defense_line || '单日亏损触及本金 2% 或个股跌破分时均线/EMA20 支撑无条件触发止损纪律，严禁扛单与盲目补仓！';
    }'''

def apply_refactor():
    content = INDEX_PATH.read_text(encoding="utf-8")
    
    # 1. 替换 HTML 结构 (#pane-strategy)
    pane_pattern = re.compile(r'<section id="pane-strategy" class="tab-pane"[\s\S]*?</section>', re.MULTILINE)
    if not pane_pattern.search(content):
        print("Error: Could not find <section id='pane-strategy'> in index.html")
        return False
    content = pane_pattern.sub(NEW_PANE_STRATEGY_HTML.strip(), content, count=1)
    
    # 2. 替换 JS renderStrategy 函数
    js_pattern = re.compile(r'function renderStrategy\(strategy\)\s*\{[\s\S]*?\}\s*(?=function formatTime)', re.MULTILINE)
    if not js_pattern.search(content):
        print("Error: Could not find renderStrategy function in index.html")
        return False
    content = js_pattern.sub(NEW_RENDER_STRATEGY_JS.strip() + "\n\n    ", content, count=1)
    
    INDEX_PATH.write_text(content, encoding="utf-8")
    print("Successfully refactored mobile_app/index.html!")
    return True

if __name__ == "__main__":
    apply_refactor()
