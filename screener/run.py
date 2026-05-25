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
from datetime import datetime, timedelta, timezone

from . import chip as chip_mod
from . import fetch, indicators, io, score
from .chip import ChipSnapshot
from .fetch import AnalystSnapshot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BENCHMARK_SYMBOLS = {
    "tw": "0050.TW",
    "us": "SPY",
}


def _iso_or_none(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def _build_target_raise_event(
    current_target: float | None,
    prev_target: float | None,
    prev_blob: dict,
    now: datetime,
) -> dict:
    detected_at: datetime | None = None
    valid_until: datetime | None = None
    raise_from: float | None = None
    raise_to: float | None = None
    raise_pct: float | None = None

    if (
        current_target is not None
        and prev_target is not None
        and prev_target > 0
        and current_target / prev_target > score.TARGET_RAISE_THRESHOLD
    ):
        detected_at = now
        valid_until = now + timedelta(days=3)
        raise_from = prev_target
        raise_to = current_target
        raise_pct = current_target / prev_target - 1.0
    else:
        prev_valid_until = _parse_iso_datetime(
            prev_blob.get("target_raise_valid_until")
        )
        prev_raise_to = prev_blob.get("target_raise_to")
        if (
            prev_valid_until is not None
            and now <= prev_valid_until
            and current_target is not None
            and prev_raise_to is not None
            and current_target >= float(prev_raise_to) * 0.99
        ):
            detected_at = _parse_iso_datetime(prev_blob.get("target_raise_detected_at"))
            valid_until = prev_valid_until
            raise_from = prev_blob.get("target_raise_from")
            raise_to = prev_raise_to
            raise_pct = prev_blob.get("target_raise_pct")

    return {
        "target_raise_detected_at": _iso_or_none(detected_at),
        "target_raise_valid_until": _iso_or_none(valid_until),
        "target_raise_from": raise_from,
        "target_raise_to": raise_to,
        "target_raise_pct": raise_pct,
    }


def _build_analyst_blob_eod(
    new_snap: AnalystSnapshot, prev_blob: dict | None, now: datetime | None = None
) -> dict:
    """At EOD: shift the previous run's target_mean into target_mean_prev_eod.

    Intraday runs don't touch the analyst block, so prev['target_mean'] is
    always the value set by the most recent EOD — exactly what 'yesterday's
    EOD' should mean once today's EOD fires.
    """
    prev = prev_blob or {}
    now_dt = now or datetime.now(timezone.utc)
    prev_target = prev.get("target_mean")
    return {
        "target_mean": new_snap.target_mean,
        "rating": new_snap.rating,
        "rating_score": new_snap.rating_score,
        "target_mean_prev_eod": prev_target,
        **_build_target_raise_event(
            current_target=new_snap.target_mean,
            prev_target=prev_target,
            prev_blob=prev,
            now=now_dt,
        ),
    }


def run_market(market: str, mode: str = "eod") -> dict:
    if mode not in {"intraday", "eod"}:
        raise ValueError(f"invalid mode: {mode}")

    entries = io.load_watchlist(market=market)
    prev_data = io.load_latest_signals()
    prev_signals = prev_data.get("signals", {})
    out: dict[str, dict] = {}
    benchmark_return_20d = None
    benchmark_symbol = BENCHMARK_SYMBOLS.get(market)
    if benchmark_symbol:
        try:
            benchmark_return_20d = indicators.compute(
                fetch.fetch_ohlcv(benchmark_symbol).df
            ).return_20d
        except Exception as exc:
            logger.warning("benchmark fetch failed for %s: %s", benchmark_symbol, exc)

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
                target_raise_detected_at=analyst_blob.get(
                    "target_raise_detected_at"
                ),
                target_raise_valid_until=analyst_blob.get(
                    "target_raise_valid_until"
                ),
                target_raise_from=analyst_blob.get("target_raise_from"),
                target_raise_to=analyst_blob.get("target_raise_to"),
                target_raise_pct=analyst_blob.get("target_raise_pct"),
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
            benchmark_return_20d=benchmark_return_20d,
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
