"""Entrypoint: python -m screener.run --market {tw|us} --mode {intraday|eod}

Modes:
- eod: fetch OHLCV + analyst + chip (TWSE T86). Run once per day post-close.
- intraday: fetch OHLCV only; carry analyst + chip blobs from previous run.
  Use during market session (every 30 min) — price-related rules update,
  but chip/analyst rules stay anchored to the previous EOD snapshot.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import asdict
from datetime import datetime, timezone

from . import chip as chip_mod
from . import fetch, indicators, io, score
from .chip import ChipSnapshot
from .fetch import AnalystSnapshot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _build_analyst_blob_eod(
    new_snap: AnalystSnapshot, prev_blob: dict | None
) -> dict:
    """At EOD: shift the previous run's target_mean into target_mean_prev_eod.

    Intraday runs don't touch the analyst block, so prev['target_mean'] is
    always the value set by the most recent EOD — exactly what 'yesterday's
    EOD' should mean once today's EOD fires.
    """
    prev = prev_blob or {}
    return {
        "target_mean": new_snap.target_mean,
        "rating": new_snap.rating,
        "rating_score": new_snap.rating_score,
        "target_mean_prev_eod": prev.get("target_mean"),
    }


def run_market(market: str, mode: str = "eod") -> dict:
    if mode not in {"intraday", "eod"}:
        raise ValueError(f"invalid mode: {mode}")

    entries = io.load_watchlist(market=market)
    prev_data = io.load_latest_signals()
    prev_signals = prev_data.get("signals", {})
    out: dict[str, dict] = {}

    # Chip data (TWSE T86) is end-of-day only — fetch only at EOD.
    chip_by_symbol: dict[str, list[chip_mod.ChipDay]] = {}
    if market == "tw" and mode == "eod" and entries:
        try:
            chip_by_symbol = chip_mod.fetch_recent_chip_data()
        except Exception as exc:
            logger.warning(
                "TWSE chip fetch failed, scoring without chip rules: %s", exc
            )

    for entry in entries:
        record: dict = {
            "symbol": entry.symbol,
            "market": entry.market,
            "name": entry.name,
            "tradingview_symbol": entry.tradingview_symbol,
        }
        prev_record = prev_signals.get(entry.symbol, {}) or {}

        try:
            ohlcv = fetch.fetch_ohlcv(entry.symbol)
        except Exception as exc:
            logger.warning("fetch OHLCV failed for %s: %s", entry.symbol, exc)
            record.update({"status": "fetch_failed", "error": str(exc)})
            out[entry.symbol] = record
            continue

        ind = indicators.compute(ohlcv.df)

        # Analyst: EOD-only fetch. Intraday inherits the previous blob unchanged.
        analyst_blob: dict | None = None
        if entry.market == "us":
            if mode == "eod":
                try:
                    new_snap = fetch.fetch_analyst(entry.symbol)
                    analyst_blob = _build_analyst_blob_eod(
                        new_snap, prev_record.get("analyst")
                    )
                except Exception as exc:
                    logger.warning(
                        "fetch analyst failed for %s: %s", entry.symbol, exc
                    )
                    analyst_blob = prev_record.get("analyst")  # keep last good
            else:
                analyst_blob = prev_record.get("analyst")

        # Chip: EOD-only. Intraday inherits the previous chip block.
        chip_blob: dict | None = None
        if entry.market == "tw":
            if mode == "eod" and chip_by_symbol:
                chip_snap = chip_mod.compute_chip_snapshot(
                    entry.symbol, chip_by_symbol, today_volume=ind.volume
                )
                chip_blob = asdict(chip_snap) if chip_snap else None
            else:
                chip_blob = prev_record.get("chip")

        # Reconstruct strongly-typed objects to feed score()
        analyst_for_score: AnalystSnapshot | None = None
        prev_target_mean = None
        if analyst_blob:
            analyst_for_score = AnalystSnapshot(
                target_mean=analyst_blob.get("target_mean"),
                rating=analyst_blob.get("rating"),
                rating_score=analyst_blob.get("rating_score"),
            )
            prev_target_mean = analyst_blob.get("target_mean_prev_eod")

        chip_for_score: ChipSnapshot | None = None
        if chip_blob:
            chip_for_score = ChipSnapshot(
                trust_streak_days=chip_blob.get("trust_streak_days", 0),
                foreign_streak_days=chip_blob.get("foreign_streak_days", 0),
                foreign_net_today=chip_blob.get("foreign_net_today"),
                daily_volume_today=chip_blob.get("daily_volume_today"),
                foreign_pct_of_volume=chip_blob.get("foreign_pct_of_volume"),
            )

        result = score.score(
            market=entry.market,
            ind=ind,
            analyst=analyst_for_score,
            prev_target_mean=prev_target_mean,
            chip=chip_for_score,
        )

        record.update(
            {
                "status": "ok",
                "score": result.score,
                "max_score": result.max_score,
                "reasons": [asdict(r) for r in result.reasons],
                "indicators": asdict(ind),
                "analyst": analyst_blob,
                "chip": chip_blob,
                "mode": mode,
            }
        )
        out[entry.symbol] = record

    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["tw", "us"], required=True)
    parser.add_argument("--mode", choices=["intraday", "eod"], default="eod")
    args = parser.parse_args()

    new_signals = run_market(args.market, mode=args.mode)

    existing = io.load_latest_signals()
    signals = existing.get("signals", {})
    signals.update(new_signals)

    now = datetime.now(timezone.utc).isoformat()
    last_run = existing.get("last_run", {}) or {}
    last_run[args.market] = now
    last_run[f"{args.market}_{args.mode}"] = now

    io.write_latest_signals(
        {
            "generated_at": now,
            "last_run": last_run,
            "signals": signals,
        }
    )

    ok = sum(1 for s in new_signals.values() if s.get("status") == "ok")
    fail = len(new_signals) - ok
    logger.info(
        "market=%s mode=%s done: ok=%d fail=%d", args.market, args.mode, ok, fail
    )


if __name__ == "__main__":
    main()
