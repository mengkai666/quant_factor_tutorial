# -*- coding: utf-8 -*-
"""数据源公共模型。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


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
