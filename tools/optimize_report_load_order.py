"""优先本地 Assets 秒开研报，消除 CDN 超时等待 (tools/optimize_report_load_order.py)"""

from pathlib import Path

index_path = Path("mobile_app/index.html")
content = index_path.read_text(encoding="utf-8")

# 1. 优化 switchReportTab 中的 candidates 顺序
old_candidates = """      // 2. 远端动态拉取候选链 (自定义服务/局域网优先 -> CDN -> GitHub Raw -> 本地离线 assets)
      const candidates = [
        (customServer ? `${customServer}/mobile_app/reports/${type}_${dateToUse}.html` : null),
        (customServer ? `${customServer}/reports/${type}_${dateToUse}.html` : null),
        `https://cdn.jsdelivr.net/gh/mengkai666/quant_factor_tutorial@master/mobile_app/reports/${type}_${dateToUse}.html`,
        `https://raw.githubusercontent.com/mengkai666/quant_factor_tutorial/master/mobile_app/reports/${type}_${dateToUse}.html`,
        `./reports/${type}_${dateToUse}.html`
      ].filter(Boolean);"""

new_candidates = """      // 2. 动态拉取候选链 (本地离线 assets 优先秒开 -> 自定义服务 -> CDN -> GitHub Raw)
      const isFileProto = (window.location.protocol === 'file:');
      const candidates = [
        (isFileProto ? `./reports/${type}_${dateToUse}.html` : null),
        (customServer ? `${customServer}/mobile_app/reports/${type}_${dateToUse}.html` : null),
        (customServer ? `${customServer}/reports/${type}_${dateToUse}.html` : null),
        `./reports/${type}_${dateToUse}.html`,
        `https://cdn.jsdelivr.net/gh/mengkai666/quant_factor_tutorial@master/mobile_app/reports/${type}_${dateToUse}.html`,
        `https://raw.githubusercontent.com/mengkai666/quant_factor_tutorial/master/mobile_app/reports/${type}_${dateToUse}.html`
      ].filter(Boolean);"""

if old_candidates in content:
    content = content.replace(old_candidates, new_candidates)
    print("✅ 成功优化 candidates 加载顺序")
else:
    print("⚠️ 未直接匹配 old_candidates")

# 2. 优化 syncReportsManifestRemote endpoints
old_endpoints = """      const endpoints = [
        (customServer ? `${customServer}/mobile_app/reports/manifest.json` : null),
        (customServer ? `${customServer}/reports/manifest.json` : null),
        'https://cdn.jsdelivr.net/gh/mengkai666/quant_factor_tutorial@master/mobile_app/reports/manifest.json',
        'https://raw.githubusercontent.com/mengkai666/quant_factor_tutorial/master/mobile_app/reports/manifest.json',
        './reports/manifest.json'
      ].filter(Boolean);"""

new_endpoints = """      const isFileProto = (window.location.protocol === 'file:');
      const endpoints = [
        (isFileProto ? './reports/manifest.json' : null),
        (customServer ? `${customServer}/mobile_app/reports/manifest.json` : null),
        (customServer ? `${customServer}/reports/manifest.json` : null),
        './reports/manifest.json',
        'https://cdn.jsdelivr.net/gh/mengkai666/quant_factor_tutorial@master/mobile_app/reports/manifest.json',
        'https://raw.githubusercontent.com/mengkai666/quant_factor_tutorial/master/mobile_app/reports/manifest.json'
      ].filter(Boolean);"""

if old_endpoints in content:
    content = content.replace(old_endpoints, new_endpoints)
    print("✅ 成功优化 endpoints 加载顺序")
else:
    print("⚠️ 未直接匹配 old_endpoints")

index_path.write_text(content, encoding="utf-8")
print("✅ 保存 index.html")
