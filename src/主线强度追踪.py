# -*- coding: utf-8 -*-
"""兼容入口：公开旧模块 API，并把脚本执行委托给分阶段应用。"""

import legacy_tracker as _legacy

# 兼容既有研究脚本和测试：历史模块曾直接暴露若干下划线辅助函数。
globals().update({name: value for name, value in vars(_legacy).items()
                  if not name.startswith("__")})


if __name__ == "__main__":
    from app import main as app_main

    raise SystemExit(app_main())
