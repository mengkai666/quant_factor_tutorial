# -*- coding: utf-8 -*-
"""前端静态依赖的唯一真源 (目前只有 ECharts)。

为什么要单独成模块:
    ECharts 的 CDN 地址原先在三个渲染器里各写一份, 而且**版本不一致** ——
      · src/主线强度追踪.py    `echarts@5`       ← 浮动标签
      · src/legacy_tracker.py  `echarts@5`       ← 浮动标签
      · src/lianban_analysis.py `echarts@5.5.1`  ← 已锁定
    浮动标签的代价是**归档不可复现**: `output/site/reports/` 里 39 期报告都指向
    `echarts@5`, 上游随便发一个 5.x 补丁, 这些历史报告的渲染就跟着变。一份"历史
    报告"应当定格。这与 `src/price_slices.py` 特意钉死 LF / mtime=0 / 列序是同一条
    原则(可复现), 那里做对了, 这里漏了。同一天的主报告与看板还跑在两个不同版本上,
    默认行为(平滑曲线、坐标轴刻度策略、标签省略规则)可能不一致。

为什么还要一段兜底:
    全文唯一的**外部**资源就是这个 CDN。它不可达时 13 张图会全部空白, 而"空白"和
    "当天没有数据"在页面上长得一模一样 —— 那是一种会误导人的静默失败。所以脚本标签
    后面跟一段极小的检查: 图表库没到位就把这句话**写在画布位置上**。
    它在 <head> 里同步引入的 script 之后运行, 而同步脚本在 DOMContentLoaded 之前
    必然已经加载完成或失败, 因此这个判断不会误报"还没加载完"。

升级方式:
    改动 ECHARTS_VERSION 必须是**一次显式提交**, 而不是上游发版自动生效。
"""
from __future__ import annotations

import re

# 锁到具体补丁号。
ECHARTS_VERSION = "5.5.1"
ECHARTS_CDN = (
    f"https://cdn.jsdelivr.net/npm/echarts@{ECHARTS_VERSION}/dist/echarts.min.js"
)

# 三个渲染器都在用的图表容器类名 (.chart-container 主报告/旧入口, .chart-wrap 看板)。
_CHART_SLOTS = ".chart-container, .chart-wrap"

# 兜底脚本: 只用原生 DOM + ES5, 因为它要能在 ECharts 缺席时工作。
_MISSING_NOTICE_JS = """<script>
(function () {
  function warn() {
    if (window.echarts) { return; }
    var slots = document.querySelectorAll('%(slots)s');
    for (var i = 0; i < slots.length; i++) {
      var box = slots[i];
      if (box.getAttribute('data-charts-unavailable') === '1') { continue; }
      box.setAttribute('data-charts-unavailable', '1');
      var note = document.createElement('div');
      note.style.cssText = 'padding:22px;text-align:center;line-height:1.8;'
        + 'font-size:13px;color:#d29922;border:1px dashed #d29922;'
        + 'border-radius:8px;background:rgba(210,153,34,0.06);';
      note.textContent = '图表库未加载 (ECharts CDN 不可达), 此处只会是空白 —— '
        + '这表示加载失败, 不代表当天没有数据。表格中的数值仍然有效。';
      box.appendChild(note);
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', warn);
  } else {
    warn();
  }
})();
</script>""" % {"slots": _CHART_SLOTS}


def echarts_head_html() -> str:
    """返回渲染器 <head> 里要插入的 ECharts 依赖片段 (锁定版本 + 加载失败兜底)。"""
    return f'<script src="{ECHARTS_CDN}"></script>\n{_MISSING_NOTICE_JS}'


def is_pinned(url: str) -> bool:
    """URL 是否钉到了具体补丁号 (浮动标签会让归档跟着上游漂)。"""
    return bool(re.search(r"@\d+\.\d+\.\d+/", url or ""))
