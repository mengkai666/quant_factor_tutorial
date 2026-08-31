"""Cold-start and rebuild the canonical SH/SZ/BJ market-data caches."""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from console_io import enable_utf8_console  # noqa: E402

enable_utf8_console()  # 输出被重定向到文件/管道时, emoji print 不再撞 Windows GBK

from data_sources.calendar_provider import CalendarProvider  # noqa: E402
from data_sources.fetch_status import FetchStatusStore  # noqa: E402
from data_sources.price_provider import PriceProvider  # noqa: E402
from data_sources.quality_gate import DataQualityError, MarketDataQualityGate  # noqa: E402
from data_sources.universe_provider import UniverseProvider  # noqa: E402
from paths import (DATA_DIR, FETCH_STATUS_CACHE, PRICE_CACHE, QUALITY_REPORT,  # noqa: E402
                   UNIVERSE_CACHE)
from pipeline.data_pipeline import DataPipeline  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-11-04")
    parser.add_argument("--end", default=None)
    parser.add_argument("--candidate-only", action="store_true")
    args = parser.parse_args(argv)

    candidate = os.path.join(DATA_DIR, "price_history_cache.candidate.csv")
    status_store = FetchStatusStore(FETCH_STATUS_CACHE)
    pipeline = DataPipeline(
        calendar_provider=CalendarProvider(),
        universe_provider=UniverseProvider(status_store=status_store),
        price_provider=PriceProvider(status_store=status_store, max_workers=4),
        quality_gate=MarketDataQualityGate(),
    )
    try:
        if args.candidate_only:
            target = args.end or pipeline.calendar_provider.latest_closed_day()
            dates = pipeline.calendar_provider.trading_days(args.start, target)
            universe = pipeline.universe_provider.refresh(UNIVERSE_CACHE)
            result = pipeline.price_provider.rebuild(universe, dates, candidate)
            print(f"候选缓存: {candidate} ({result.actual_count}/{result.expected_count})")
            return 0 if result.data is not None and not result.data.empty else 1
        prepared = pipeline.prepare(
            start=args.start, target_date=args.end, universe_path=UNIVERSE_CACHE,
            candidate_path=candidate, official_path=PRICE_CACHE,
            quality_report_path=QUALITY_REPORT,
        )
    except (DataQualityError, RuntimeError, ValueError) as exc:
        print(f"❌ 重建失败: {exc}")
        print(f"候选文件保留: {candidate}")
        return 1
    print(f"✅ 沪深北价格缓存已重建: {len(prepared.prices)} 行, 截止 {prepared.target_date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
