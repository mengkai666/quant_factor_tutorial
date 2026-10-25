"""Offline, searchable external evidence; no remote scripts or price-pool edits."""
from __future__ import annotations

import html
from urllib.parse import urlparse


def _e(value):
    return html.escape(str(value if value is not None else "—"), quote=True)


def _link(url, label):
    parsed = urlparse(str(url or ""))
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not any(host == d or host.endswith("." + d) for d in ("cls.cn", "kaipanhong.com")):
        return _e(label)
    return f'<a href="{_e(url)}" target="_blank" rel="noopener noreferrer">{_e(label)}</a>'


EXTERNAL_CSS = """
.external-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:18px 0}.external-stat{border-left:3px solid var(--new);padding:12px 16px;background:var(--panel)}.external-stat strong{display:block;font:28px/1.4 Consolas,monospace}.external-stat span{font-size:12px;color:var(--muted)}.external-controls{display:flex;flex-wrap:wrap;gap:12px;align-items:end;margin:18px 0}.external-controls label{display:grid;gap:5px;color:var(--muted);font-size:13px}.external-controls input,.external-controls select{background:var(--bg);color:var(--text);border:1px solid var(--edge);border-radius:5px;padding:8px;min-height:38px;max-width:100%}.external-controls input{min-width:220px}.external-table{max-height:650px;overflow:auto}.external-table th{position:sticky;top:0;background:var(--panel);z-index:1}.external-table td{vertical-align:top;min-width:140px}.external-table td:last-child{min-width:320px;max-width:500px}.external-table [hidden]{display:none}.external-record{border-left:2px solid var(--edge);padding:7px 12px;margin:9px 0;overflow-wrap:anywhere}.external-topics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.external-topic{border:1px solid var(--edge);padding:13px;border-radius:6px}.external-topic p{font-size:12px;color:var(--muted);margin-bottom:0}.external-audit{background:#163b40;color:#b7eee5;border:1px solid #39776b;border-radius:5px;padding:5px 8px;cursor:pointer;margin-top:7px}.external-note{padding:12px 16px;border-left:3px solid var(--risk);background:#262a30}.external-note p{margin:4px 0}.external-provenance{font-size:12px;overflow-wrap:anywhere}@media(max-width:700px){.external-stats{grid-template-columns:repeat(2,minmax(0,1fr))}.external-topics{grid-template-columns:1fr}.external-controls label{width:100%}.external-controls input,.external-controls select{width:100%;min-width:0}}
"""


def render_external_section(value):
    value = value or {}
    summary = value.get("summary", {})
    notes = "".join(f'<p>{_e(x)}</p>' for x in value.get("warnings", []))
    if not value.get("fetched_at"):
        return f'<section id="external-evidence"><h2>外部证据 · 开盘啦 / 财联社</h2><div class="external-note">{notes or "尚未获取外部数据；价格筛选保持独立。"}</div></section>'
    stats = "".join(f'<div class="external-stat"><strong>{summary.get(key, 0)}</strong><span>{label}</span></div>' for key, label in [
        ("matched_stocks", "匹配当前 / 历史观察"), ("current_selected", "其中两月最新价格入选"),
        ("two_source_mentions", "其中两源同代码提及"), ("external_only", "仅外部提及 · 不自动入池")])
    topics = "".join(f'<article class="external-topic"><span class="badge">{_e(x["provider"])} · {_e(x["category"])}</span><h3>{_e(x["title"])}</h3><p>{_e(x["detail"])}</p><p>来源日期 {_e(x["source_date"] or "未标注")}</p></article>' for x in value.get("topics", []))
    rows = []
    for group in ("matches", "external_only"):
        for row in value.get(group, []):
            records = "".join(f'<div class="external-record"><b>{_e(x["provider"])} · {_e(x["category"])}</b><br>{_e(x["theme"])}<p>{_e(x["detail"])}</p><div class="muted tiny">来源日期 {_e(x["source_date"] or "未标注")} · {_e(x["date_status"])}<br>{_e(x["match_basis"])} · {_link(x["url"], "查看来源")}<br>抓取 {_e(x["observed_at"])}</div></div>' for x in row["records"])
            query = " ".join([row["code"], row["name"], *row["external_names"], *row["themes"], *row["categories"]]).lower()
            history = "".join(f'<br><span class="muted tiny">{label}研究：首选 {_e(row.get(first))} · 最近 {_e(row.get(last))}</span>'
                              for label, first, last in [("两月", "first_selected_2m", "last_selected_2m"),
                                                         ("半年", "first_selected_6m", "last_selected_6m")]
                              if row.get(first))
            windows = f'<br><span class="muted tiny">当前价格入选：两月 {"是" if row["price_selected"] else "否"} / 半年 {"是" if row.get("price_selected_6m") else "否"}</span>'
            risk = f'<br><span class="badge risk">含{row["risk_count"]}条下跌类异动</span>' if row["risk_count"] else ""
            rows.append(f'<tr data-external-row data-scope="{group}" data-query="{_e(query)}" data-sources="{_e(" ".join(row["sources"]))}" data-selected="{str(row["price_selected"]).lower()}" {"hidden" if group == "external_only" else ""}><td><strong>{_e(row["name"])}</strong><br><span class="muted tiny">{_e(row["code"])}</span><br><button class="external-audit" type="button" data-external-code="{_e(row["code"])}">核验最新价格</button></td><td>{_e(row["pool_context"])}<br><span class="muted tiny">{_e(row["identity"])} / {_e(row["tracking_status"])}</span>{windows}{history}{risk}</td><td>{_e(" + ".join(row["sources"]))}<br><span class="muted tiny">{_e("、".join(row["categories"]))}<br>{_e("、".join(row["themes"]))}</span></td><td><details><summary>{len(row["records"])}条来源记录 · 日期 / 依据</summary>{records}</details></td></tr>')
    status_rows = "".join(f'<tr><td>{_e(x["provider"])}</td><td>{_e(x["route"])}</td><td>{"成功" if x["success"] else "失败"} · {x["record_count"]}条个股记录</td><td>{_e(x["source_date"] or "未标注")}<br>{_e(x["error"])}</td></tr>' for x in value.get("endpoints", []))
    mode = {"supplement": "补充观察", "stale": "旧缓存 / 降级", "future_snapshot": "未来消息已隔离", "invalid_time": "抓取时点无效"}.get(value.get("mode"), value.get("mode"))
    return f'''<section id="external-evidence"><div class="section-head"><h2>外部证据 · 开盘啦 / 财联社</h2><span class="badge old">{_e(mode)}</span></div>
<p class="muted">主线、风口、当日涨幅榜、区间强势和涨停题材按明确代码对应；对照两月及半年研究历史与正式长期追踪。两源同时提及只说明信息重叠，不等于抱团、可成交或买入确认。</p>
<div class="external-note"><p class="tiny">{_e(value.get("limitation"))}</p>{notes}<p class="tiny">抓取 {_e(value["fetched_at"])} · 报告截止 {_e(value.get("requested_date"))} · 本地价格 {_e(value.get("market_as_of"))} · 隔离未来记录 {value.get("excluded_count", 0)}条</p></div>
<div class="external-stats">{stats}</div><details class="panel"><summary>接口返回的方向 / 风口（不凭题材名自动选股）</summary><div class="external-topics">{topics or "无可用方向记录"}</div></details>
<div class="panel"><div class="external-controls"><label>查看范围<select id="external-scope"><option value="matches">匹配当前 / 历史观察</option><option value="selected">其中两月最新价格入选</option><option value="external_only">仅外部提及 · 未取得价格资格</option><option value="all">全部来源股票</option></select></label><label>数据源<select id="external-provider"><option value="all">两家数据源</option><option value="财联社">财联社</option><option value="开盘啦">开盘啦</option><option value="both">两源同代码提及</option></select></label><label>搜索股票 / 题材 / 类别<input id="external-search" placeholder="名称、代码、主线、风口" aria-label="搜索外部证据"></label></div>
<p id="external-count" class="tiny muted" aria-live="polite">匹配观察股票 {len(value.get("matches", []))}只</p><div class="table-scroll external-table"><table><thead><tr><th>股票 / 价格核验</th><th>本地价格与追踪身份</th><th>来源 / 外部类别</th><th>原始依据与时点</th></tr></thead><tbody id="external-rows">{"".join(rows)}</tbody></table></div><p id="external-empty" class="muted" {"hidden" if value.get("matches") else ""}>当前范围没有匹配记录；可切换范围或清除搜索。不会用外部消息补造价格资格。</p></div>
<details class="panel"><summary>接口状态与覆盖范围</summary><div class="table-scroll external-provenance"><table><thead><tr><th>来源</th><th>实际接口</th><th>结果</th><th>日期 / 错误</th></tr></thead><tbody>{status_rows}</tbody></table></div></details></section>'''


EXTERNAL_SCRIPT = r"""
(()=>{
 const root=document.getElementById('external-evidence');
 const scope=document.getElementById('external-scope');if(!scope)return;
 const provider=document.getElementById('external-provider'),search=document.getElementById('external-search');
 const rows=[...root.querySelectorAll('[data-external-row]')];
 function filter(){let count=0;const q=search.value.trim().toLowerCase();rows.forEach(row=>{
  const s=row.dataset.sources,matchScope=scope.value==='all'||row.dataset.scope===scope.value||(scope.value==='selected'&&row.dataset.selected==='true');
  const matchProvider=provider.value==='all'||(provider.value==='both'?s.includes('财联社')&&s.includes('开盘啦'):s.includes(provider.value));
  row.hidden=!(matchScope&&matchProvider&&row.dataset.query.includes(q));if(!row.hidden)count++;
 });document.getElementById('external-count').textContent=`当前显示 ${count} 只 · 不改变价格筛选`;document.getElementById('external-empty').hidden=count>0;}
 scope.addEventListener('change',filter);provider.addEventListener('change',filter);search.addEventListener('input',filter);
 root.addEventListener('click',event=>{const button=event.target.closest('[data-external-code]');if(!button)return;
  const input=document.getElementById('replay-search'),windowSelect=document.getElementById('replay-window'),dates=document.getElementById('replay-date');
  if(!input||!windowSelect||!dates)return;windowSelect.value='2';windowSelect.dispatchEvent(new Event('change'));
  if(dates.options.length){dates.value=dates.options[0].value;dates.dispatchEvent(new Event('change'));}
  input.value=button.dataset.externalCode;input.dispatchEvent(new Event('input'));
  document.getElementById('replay-audit-title').scrollIntoView({block:'center'});
 });filter();
})();
"""


def external_markdown(value):
    if not value:
        return []
    def md(v):
        return str(v if v is not None else "未标注").replace("|", "\\|").replace("\n", " ")
    lines = ["", "## 外部证据 · 开盘啦 / 财联社", "", value["limitation"],
             f"抓取：{value.get('fetched_at') or '尚未获取'}；模式：{value['mode']}。", *value.get("warnings", []), "",
             "| 股票 | 本地身份 | 来源 | 类别 | 来源日期 | 代码匹配依据 |", "|---|---|---|---|---|---|"]
    for row in value.get("matches", []):
        for record in row["records"]:
            lines.append(f"| {md(row['name'])} / {row['code']} | {md(row['pool_context'])} | {record['provider']} | {record['category']} | {md(record['source_date'])} | {md(record['match_basis'])} |")
    lines += ["", f"两月最新价格入选{value.get('summary', {}).get('current_selected', 0)}只；半年最新价格入选{value.get('summary', {}).get('current_selected_6m', 0)}只。", "", f"仅外部提及、未自动入池：{len(value.get('external_only', []))}只；完整记录及接口状态见JSON / HTML。"]
    return lines
