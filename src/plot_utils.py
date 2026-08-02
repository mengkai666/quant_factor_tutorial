# pyright: reportMissingTypeStubs=false
"""matplotlib 出图统一兜底 (单一真源)。

⚠️ 踩过的坑: matplotlib 输出像素 = figsize(英寸) × dpi。
   图片处理管线 (缩略图/预览/多模态读图) 普遍有 2000×2000 上限,
   一旦 figsize×dpi 任一边爆 2000, 生成的 PNG 就无法被读取/预览。
   backtest_v2_1_report (24×16 @140 = 3360×2240) 就栽在这。

用法: 把散落各处的 `fig.savefig(path, dpi=X, ...)` 换成
      `from plot_utils import safe_savefig; safe_savefig(fig, path, dpi=X, ...)`
      dpi 会在超限时被自动压到刚好卡进 MAX_PX, 其余 kwargs 原样透传。

只改这一个文件即可调整全局上限 (MAX_PX)。
"""

# 图片处理管线的像素上限 (宽/高各自不得超过)
MAX_PX = 2000


def fit_dpi(fig, dpi: float, max_px: int = MAX_PX) -> float:
    """给定 figure 与目标 dpi, 返回不会让任一边超过 max_px 的最大可用 dpi。

    未超限则原样返回 dpi; 超限则等比压到刚好卡进 (向下取整留 1px 余量)。
    """
    w_in, h_in = fig.get_size_inches()
    longest_in = max(w_in, h_in)
    if longest_in <= 0:
        return dpi
    max_dpi = (max_px - 1) / longest_in  # -1 留余量, 避免边界四舍五入越界
    return min(dpi, max_dpi)


def safe_savefig(fig, path: str, dpi: float = 100, max_px: int = MAX_PX, **kwargs):
    """fig.savefig 的兜底包装: 超限自动压 dpi 并告警。

    注意 bbox_inches='tight' 会裁掉留白, 实际像素通常略小于 figsize×dpi,
    所以本函数按未裁剪的名义尺寸估算 (偏保守), 保证一定不越界。
    """
    safe = fit_dpi(fig, dpi, max_px)
    if safe < dpi:
        w_in, h_in = fig.get_size_inches()
        import os
        # 纯 ASCII 输出: Windows GBK 控制台无法编码 emoji/全角箭头
        print(f"  [plot_utils] {os.path.basename(path)} dpi {dpi:.0f}->{safe:.0f} "
              f"(figsize {w_in:.0f}x{h_in:.0f} @{dpi:.0f} 会超 {max_px}px 上限, 已自动压缩)")
    fig.savefig(path, dpi=safe, **kwargs)
    return path
