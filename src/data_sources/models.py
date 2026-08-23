from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from functools import lru_cache
import re
from typing import Any


_CANONICAL_RE = re.compile(r"^(sh|sz|bj)(\d{6})$", re.IGNORECASE)
_PREFIXED_RE = re.compile(r"^(sh|sz|bj)[.](\d{6})$", re.IGNORECASE)
_SUFFIXED_RE = re.compile(r"^(\d{6})[.](sh|sz|bj)$", re.IGNORECASE)
_COMPACT_SUFFIXED_RE = re.compile(r"^(\d{6})(sh|sz|bj)$", re.IGNORECASE)


@lru_cache(maxsize=1 << 17)
def _canonical_code(text: str) -> tuple[str | None, str | None]:
    """纯文本 → (规范代码, 错误类型); 无法识别时代码为 None, 由调用方带原值抛错。

    ⚠️ 热点函数 (一轮报告 170 万+ 次调用, 实测占 4.3s): 纯字符串函数,
       按输入文本 lru_cache; 全市场只有几千个不同代码, 命中率极高。
       错误分支不在这里抛, 是为了让报错消息里仍保留调用方传入的原始对象
       (``{value!r}``), 与旧行为逐字一致。
    """
    match = _CANONICAL_RE.fullmatch(text) or _PREFIXED_RE.fullmatch(text)
    if match:
        exchange, raw = match.groups()
        return exchange.lower() + raw, None

    match = _SUFFIXED_RE.fullmatch(text) or _COMPACT_SUFFIXED_RE.fullmatch(text)
    if match:
        raw, exchange = match.groups()
        return exchange.lower() + raw, None

    if not re.fullmatch(r"\d{6}", text):
        return None, "unsupported"
    if text.startswith(("4", "8")) or text.startswith("92"):
        exchange = "bj"
    elif text.startswith(("5", "6", "9")):
        exchange = "sh"
    elif text.startswith(("0", "1", "2", "3")):
        exchange = "sz"
    else:
        return None, "exchange"
    return exchange + text, None


def normalize_code(value: object) -> str:
    """Convert supported Shanghai/Shenzhen/Beijing identifiers to one format."""
    if value is None:
        raise ValueError("stock code is required")
    text = str(value).strip()
    if not text:
        raise ValueError("stock code is required")
    code, error = _canonical_code(text)
    if code is not None:
        return code
    if error == "exchange":
        raise ValueError(f"cannot infer exchange for stock code: {value!r}")
    raise ValueError(f"unsupported stock code: {value!r}")


class FetchStatus(str, Enum):
    SUCCESS = "success"
    ZERO = "zero"
    PARTIAL = "partial"
    FAILED = "failed"
    STALE = "stale"
    NOT_AVAILABLE = "not_available"


@dataclass(frozen=True)
class FetchResult:
    dataset: str
    date: str
    source: str
    status: FetchStatus
    expected_count: int = 0
    actual_count: int = 0
    scope: str = "all"
    message: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    run_id: str = ""
    data: Any = None

    @classmethod
    def success(cls, *, dataset: str, date: str, source: str,
                expected_count: int, actual_count: int, scope: str = "all",
                message: str = "", started_at: datetime | None = None,
                finished_at: datetime | None = None, run_id: str = "", data: Any = None):
        return cls(dataset, date, source, FetchStatus.SUCCESS, expected_count,
                   actual_count, scope, message,
                   started_at or datetime.now(timezone.utc),
                   finished_at or datetime.now(timezone.utc), run_id, data)

    @classmethod
    def zero(cls, *, dataset: str, date: str, source: str, scope: str = "all",
             message: str = "", expected_count: int = 0, actual_count: int = 0,
             run_id: str = "", data: Any = None):
        return cls(dataset, date, source, FetchStatus.ZERO, expected_count, actual_count, scope,
                   message, run_id=run_id, data=data)

    @classmethod
    def failed(cls, *, dataset: str, date: str, source: str, message: str,
               scope: str = "all", expected_count: int = 0,
               actual_count: int = 0, run_id: str = "", data: Any = None):
        return cls(dataset, date, source, FetchStatus.FAILED, expected_count,
                   actual_count, scope, message, run_id=run_id, data=data)

    @classmethod
    def partial(cls, *, dataset: str, date: str, source: str,
                expected_count: int, actual_count: int, message: str = "",
                scope: str = "all", run_id: str = "", data: Any = None):
        return cls(dataset, date, source, FetchStatus.PARTIAL, expected_count,
                   actual_count, scope, message, run_id=run_id, data=data)

@dataclass(frozen=True)
class SecurityMasterRecord:
    code: str
    name: str
    market: str
    industry: str = ""
    status: str = "active"
    is_st: bool = False
    tradable: bool = True
    updated_at: str = ""
    source: str = "eastmoney"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PriceRecord:
    code: str
    date: str
    close_raw: float | None = None
    close_qfq: float | None = None
    price_basis: str = "raw"
    source: str = ""
    source_timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ModuleQuality:
    name: str
    status: str
    total: int = 0
    covered: int = 0
    coverage_pct: float = 0.0
    raw_covered: int = 0
    raw_coverage_pct: float = 0.0
    source: str = ""
    source_timestamp: str = ""
    missing_fields: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    lineage: dict[str, Any] = field(default_factory=dict)
    critical: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
