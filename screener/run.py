"""Entrypoint: python -m screener.run --market {tw|us}"""

from __future__ import annotations

import argparse
import logging
from dataclasses import asdict
from datetime import datetime, timezone

from . import fetch, indicators, io, score

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run_market(market: str) -> dict:
    entries = io.load_watchlist(market=market)
    prev_data = io.load_latest_signals()
    prev_signals = prev_data.get("signals", {})
    out: dict[str, dict] = {}

    for entry in entries:
        record: dict = {
            "symbol": entry.symbol,
            "market": entry.market,
            "name": entry.name,
            "tradingview_symbol": entry.tradingview_symbol,
        }
        try:
            ohlcv = fetch.fetch_ohlcv(entry.symbol)
        except Exception as exc:
            logger.warning("fetch OHLCV failed for %s: %s", entry.symbol, exc)
            record.update({"status": "fetch_failed", "error": str(exc)})
            out[entry.symbol] = record
            continue

        ind = indicators.compute(ohlcv.df)

        analyst = None
        if entry.market == "us":
            try:
                analyst = fetch.fetch_analyst(entry.symbol)
            except Exception as exc:
                logger.warning("fetch analyst failed for %s: %s", entry.symbol, exc)

        prev_target = None
        prev_record = prev_signals.get(entry.symbol)
        if prev_record and prev_record.get("analyst"):
            prev_target = prev_record["analyst"].get("target_mean")

        result = score.score(
            market=entry.market,
            ind=ind,
            analyst=analyst,
            prev_target_mean=prev_target,
        )

        record.update(
            {
                "status": "ok",
                "score": result.score,
                "max_score": result.max_score,
                "reasons": [asdict(r) for r in result.reasons],
                "indicators": asdict(ind),
                "analyst": asdict(analyst) if analyst else None,
            }
        )
        out[entry.symbol] = record

    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["tw", "us"], required=True)
    args = parser.parse_args()

    new_signals = run_market(args.market)

    existing = io.load_latest_signals()
    signals = existing.get("signals", {})
    signals.update(new_signals)

    now = datetime.now(timezone.utc).isoformat()
    last_run = existing.get("last_run", {}) or {}
    last_run[args.market] = now

    io.write_latest_signals(
        {
            "generated_at": now,
            "last_run": last_run,
            "signals": signals,
        }
    )

    ok = sum(1 for s in new_signals.values() if s.get("status") == "ok")
    fail = len(new_signals) - ok
    logger.info("market=%s done: ok=%d fail=%d", args.market, ok, fail)


if __name__ == "__main__":
    main()
