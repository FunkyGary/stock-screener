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
from dataclasses import asdict, replace
from datetime import datetime, timezone

from . import chip as chip_mod
from . import cnyes
from . import fetch, indicators, intraday_volume, io, score, sectors
from . import market_regime as market_regime_mod
from .chip import ChipSnapshot
from .fetch import AnalystSnapshot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BENCHMARK_SYMBOLS = {
    "tw": "0050.TW",
    "us": "SPY",
}


def _enrich_target_price_events(events: list[dict] | None, close: float) -> list[dict]:
    enriched: list[dict] = []
    for event in events or []:
        item = dict(event)
        target_price = item.get("target_price")
        if target_price is not None and close > 0:
            item["upside_pct"] = float(target_price) / close - 1.0
        enriched.append(item)
    return enriched


def _target_events_for_history(
    symbol: str,
    market: str,
    events: list[dict] | None,
    close: float,
    fetched_at: str,
) -> list[dict]:
    rows: list[dict] = []
    for event in events or []:
        target_price = event.get("target_price")
        row = {
            "symbol": symbol,
            "market": market,
            "event_date": event.get("date")
            or (
                event.get("published_at")[:10]
                if isinstance(event.get("published_at"), str)
                else None
            ),
            "published_at": event.get("published_at"),
            "fetched_at": fetched_at,
            "firm": event.get("firm"),
            "action": "raise",
            "previous_target": event.get("previous_target"),
            "target_price": target_price,
            "raise_pct": event.get("raise_pct"),
            "close_at_fetch": close,
            "upside_pct": (
                float(target_price) / close - 1.0
                if target_price is not None and close > 0
                else None
            ),
            "source": event.get("source"),
            "headline": event.get("headline"),
            "url": event.get("url"),
        }
        rows.append(io.normalize_target_event(row))
    return rows


def _remove_legacy_target_raise_fields(blob: dict) -> dict:
    for key in (
        "target_raise_detected_at",
        "target_raise_valid_until",
        "target_raise_from",
        "target_raise_to",
        "target_raise_pct",
    ):
        blob.pop(key, None)
    return blob


def _manual_target_events_for_symbol(
    events: list[dict], symbol: str, close: float
) -> list[dict]:
    symbol_events = [
        event
        for event in events
        if event.get("symbol") == symbol and (event.get("market") or "tw") == "tw"
    ]
    return _enrich_target_price_events(symbol_events, close)


def _build_analyst_blob_eod(new_snap: AnalystSnapshot, close: float | None = None) -> dict:
    events = new_snap.target_price_events
    if close is not None:
        events = _enrich_target_price_events(events, close)
    return {
        "rating": new_snap.rating,
        "rating_score": new_snap.rating_score,
        "target_price_events": events or [],
    }


def run_market(market: str, mode: str = "eod") -> dict:
    if mode not in {"intraday", "eod"}:
        raise ValueError(f"invalid mode: {mode}")

    entries = io.load_watchlist(market=market)
    prev_data = io.load_latest_signals()
    prev_signals = prev_data.get("signals", {})
    out: dict[str, dict] = {}
    benchmark_return_20d = None
    benchmark_ohlcv = None
    benchmark_ind = None
    score_regime = None
    benchmark_symbol = BENCHMARK_SYMBOLS.get(market)
    if benchmark_symbol:
        try:
            benchmark_ohlcv = fetch.fetch_ohlcv(
                benchmark_symbol, intraday=mode == "intraday"
            )
            benchmark_ind = indicators.compute(benchmark_ohlcv.df)
            benchmark_return_20d = benchmark_ind.return_20d
            if market == "tw":
                score_regime = market_regime_mod.classify_tw_strategy_from_ohlcv(
                    benchmark_ohlcv.df
                )
            elif market == "us":
                score_regime = market_regime_mod.classify_us_strategy_from_ohlcv(
                    benchmark_ohlcv.df
                )
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

    disposition_codes: set[str] = set()
    if market == "tw":
        try:
            disposition_codes = chip_mod.fetch_disposition_codes()
            logger.info(
                "TWSE disposition codes fetched: %d stocks", len(disposition_codes)
            )
        except Exception as exc:
            logger.warning("TWSE disposition fetch failed: %s", exc)

    if market == "tw" and mode == "eod":
        try:
            cnyes_events = cnyes.fetch_recent_tw_valuation_events(days=2)
            added = io.merge_tw_target_events(cnyes_events)
            logger.info("TW Cnyes target event history merged: added=%d", added)
        except Exception as exc:
            logger.warning("TW Cnyes target event fetch failed: %s", exc)

    manual_target_events = io.load_tw_target_events() if market == "tw" else []
    sector_map = io.load_sector_map()
    ohlcv_by_symbol = {}
    intraday_by_symbol = {}
    indicator_by_symbol = {}

    for entry in entries:
        record: dict = {
            "symbol": entry.symbol,
            "market": entry.market,
            "name": entry.name,
            "tradingview_symbol": entry.tradingview_symbol,
        }

        try:
            ohlcv = fetch.fetch_ohlcv(entry.symbol, intraday=mode == "intraday")
        except Exception as exc:
            logger.warning("fetch OHLCV failed for %s: %s", entry.symbol, exc)
            record.update({"status": "fetch_failed", "error": str(exc)})
            out[entry.symbol] = record
            continue

        ohlcv_by_symbol[entry.symbol] = ohlcv.df
        intraday_by_symbol[entry.symbol] = ohlcv.intraday_df
        indicator_by_symbol[entry.symbol] = indicators.compute(ohlcv.df)

    if mode == "intraday":
        volume_projection_by_symbol = intraday_volume.project_intraday_volumes(
            market=market,
            daily_by_symbol=ohlcv_by_symbol,
            intraday_by_symbol=intraday_by_symbol,
            sector_map=sector_map,
        )
        for symbol, projection in volume_projection_by_symbol.items():
            if symbol in indicator_by_symbol:
                indicator_by_symbol[symbol] = replace(
                    indicator_by_symbol[symbol],
                    projected_volume=projection.projected_volume,
                    projected_vol_ratio=projection.projected_vol_ratio,
                    same_time_vol_ratio=projection.same_time_vol_ratio,
                    volume_projection_source=projection.source,
                    volume_projection_reliable=projection.reliable,
                    volume_projection_capped=projection.capped,
                )

    sector_by_symbol = sectors.build_sector_snapshots(
        market=market,
        sector_map=sector_map,
        ohlcv_by_symbol=ohlcv_by_symbol,
        benchmark_df=benchmark_ohlcv.df if benchmark_ohlcv is not None else None,
    )

    for entry in entries:
        if entry.symbol in out:
            continue

        record: dict = {
            "symbol": entry.symbol,
            "market": entry.market,
            "name": entry.name,
            "tradingview_symbol": entry.tradingview_symbol,
        }
        prev_record = prev_signals.get(entry.symbol, {}) or {}
        ind = indicator_by_symbol[entry.symbol]
        sector_snap = sector_by_symbol.get(entry.symbol)
        sector_blob = asdict(sector_snap) if sector_snap is not None else None

        # Analyst: EOD-only fetch. Intraday inherits the previous blob unchanged.
        analyst_blob: dict | None = None
        if entry.market == "us":
            if mode == "eod":
                try:
                    new_snap = fetch.fetch_analyst(entry.symbol)
                    analyst_blob = _build_analyst_blob_eod(new_snap, close=ind.close)
                except Exception as exc:
                    logger.warning("fetch analyst failed for %s: %s", entry.symbol, exc)
                    analyst_blob = prev_record.get("analyst")  # keep last good
            else:
                analyst_blob = prev_record.get("analyst")
            if analyst_blob is not None:
                analyst_blob = _remove_legacy_target_raise_fields(dict(analyst_blob))
                analyst_blob["target_price_events"] = _enrich_target_price_events(
                    analyst_blob.get("target_price_events"), ind.close
                )
        elif entry.market == "tw":
            target_events = _manual_target_events_for_symbol(
                manual_target_events, entry.symbol, ind.close
            )
            if target_events:
                analyst_blob = {
                    "rating": None,
                    "rating_score": None,
                    "target_price_events": target_events,
                    "source": "tw_target_events_jsonl",
                }

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

        bare_code = entry.symbol[:-3] if entry.symbol.endswith(".TW") else None
        is_disposition = bare_code is not None and bare_code in disposition_codes

        # Reconstruct strongly-typed objects to feed score()
        analyst_for_score: AnalystSnapshot | None = None
        if analyst_blob:
            analyst_for_score = AnalystSnapshot(
                rating=analyst_blob.get("rating"),
                rating_score=analyst_blob.get("rating_score"),
                target_price_events=analyst_blob.get("target_price_events"),
                target_raise_detected_at=analyst_blob.get("target_raise_detected_at"),
                target_raise_valid_until=analyst_blob.get("target_raise_valid_until"),
                target_raise_from=analyst_blob.get("target_raise_from"),
                target_raise_to=analyst_blob.get("target_raise_to"),
                target_raise_pct=analyst_blob.get("target_raise_pct"),
            )

        chip_for_score: ChipSnapshot | None = None
        if chip_blob:
            chip_for_score = ChipSnapshot(
                trust_streak_days=chip_blob.get("trust_streak_days", 0),
                trust_buy_first_day=chip_blob.get("trust_buy_first_day", False),
                foreign_streak_days=chip_blob.get("foreign_streak_days", 0),
                foreign_net_today=chip_blob.get("foreign_net_today"),
                daily_volume_today=chip_blob.get("daily_volume_today"),
                foreign_pct_of_volume=chip_blob.get("foreign_pct_of_volume"),
            )

        result = score.score(
            market=entry.market,
            ind=ind,
            analyst=analyst_for_score,
            chip=chip_for_score,
            benchmark_return_20d=benchmark_return_20d,
            sector=sector_snap,
            strategy=(score_regime or {}).get("strategy"),
            market_below_ma10=(
                benchmark_ind.close < benchmark_ind.ma10
                if benchmark_ind is not None and benchmark_ind.ma10 is not None
                else None
            ),
            is_disposition=is_disposition,
        )

        record.update(
            {
                "status": "ok",
                "score": result.score,
                "max_score": result.max_score,
                "reasons": [_reason_to_dict(r) for r in result.reasons],
                "indicators": asdict(ind),
                "analyst": analyst_blob,
                "chip": chip_blob,
                "sector": sector_blob,
                "score_regime": score_regime,
                "mode": mode,
                "is_disposition": is_disposition,
            }
        )
        out[entry.symbol] = record

    return out


def _replace_market_signals(
    existing_signals: dict[str, dict], new_signals: dict[str, dict], market: str
) -> dict[str, dict]:
    signals = {
        symbol: signal
        for symbol, signal in existing_signals.items()
        if (signal.get("market") or "").lower() != market
    }
    signals.update(new_signals)
    return signals


def _reason_to_dict(reason: score.Reason) -> dict:
    data = asdict(reason)
    if data.get("score") is None:
        data.pop("score")
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["tw", "us"], required=True)
    parser.add_argument("--mode", choices=["intraday", "eod"], default="eod")
    args = parser.parse_args()

    now = datetime.now(timezone.utc).isoformat()
    new_signals = run_market(args.market, mode=args.mode)

    if args.market == "us" and args.mode == "eod":
        new_target_events: list[dict] = []
        for signal in new_signals.values():
            if signal.get("status") != "ok":
                continue
            analyst = signal.get("analyst") or {}
            indicators_blob = signal.get("indicators") or {}
            close = indicators_blob.get("close")
            if close is None:
                continue
            new_target_events.extend(
                _target_events_for_history(
                    symbol=signal["symbol"],
                    market=signal["market"],
                    events=analyst.get("target_price_events"),
                    close=float(close),
                    fetched_at=now,
                )
            )
        added = io.merge_target_events(new_target_events)
        logger.info("US target event history merged: added=%d", added)

    existing = io.load_latest_signals()
    signals = _replace_market_signals(
        existing.get("signals", {}), new_signals, args.market
    )

    last_run = existing.get("last_run", {}) or {}
    last_run[args.market] = now
    last_run[f"{args.market}_{args.mode}"] = now

    try:
        market_regime = market_regime_mod.build_market_regime()
    except Exception as exc:
        logger.warning("market regime build failed, keeping previous data: %s", exc)
        market_regime = existing.get("market_regime")

    io.write_latest_signals(
        {
            "generated_at": now,
            "last_run": last_run,
            "market_regime": market_regime,
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
