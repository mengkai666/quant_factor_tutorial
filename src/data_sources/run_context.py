"""运行批次上下文，用于让数据抓取状态和报告共享同一个 run_id。"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
import secrets


_RUN_ID: ContextVar[str] = ContextVar("data_source_run_id", default="")


def generate_run_id(now: datetime | None = None) -> str:
    """生成可读且足够唯一的运行批次 ID。"""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{stamp}-{secrets.token_hex(4)}"


def current_run_id() -> str:
    return _RUN_ID.get()


def set_run_id(run_id: str) -> None:
    _RUN_ID.set(str(run_id or ""))


class run_context:
    """为一次同步运行设置批次 ID，并在退出时恢复上层上下文。"""

    def __init__(self, run_id: str | None = None):
        self.run_id = str(run_id or generate_run_id())
        self._token = None

    def __enter__(self) -> str:
        self._token = _RUN_ID.set(self.run_id)
        return self.run_id

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._token is not None:
            _RUN_ID.reset(self._token)
            self._token = None
