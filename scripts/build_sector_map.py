from __future__ import annotations

import csv
import json
import ssl
import sys
from pathlib import Path
from urllib.request import Request, urlopen

import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from screener import io  # noqa: E402


TWSE_BASIC_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_BASIC_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"

TW_INDUSTRY_CODES = {
    "01": "水泥工業",
    "02": "食品工業",
    "03": "塑膠工業",
    "04": "紡織纖維",
    "05": "電機機械",
    "06": "電器電纜",
    "07": "化學生技醫療",
    "08": "玻璃陶瓷",
    "09": "造紙工業",
    "10": "鋼鐵工業",
    "11": "橡膠工業",
    "12": "汽車工業",
    "14": "建材營造",
    "15": "航運業",
    "16": "觀光餐旅",
    "17": "金融保險業",
    "18": "貿易百貨",
    "19": "綜合",
    "20": "其他",
    "21": "化學工業",
    "22": "生技醫療業",
    "23": "油電燃氣業",
    "24": "半導體業",
    "25": "電腦及週邊設備業",
    "26": "光電業",
    "27": "通信網路業",
    "28": "電子零組件業",
    "29": "電子通路業",
    "30": "資訊服務業",
    "31": "其他電子業",
    "32": "文化創意業",
    "33": "農業科技業",
    "35": "綠能環保",
    "36": "數位雲端",
    "37": "運動休閒",
    "38": "居家生活",
}


def _fetch_json(url: str) -> list[dict]:
    req = Request(url, headers={"User-Agent": "stock-screener/sector-map"})
    context = ssl._create_unverified_context()
    with urlopen(req, timeout=30, context=context) as response:
        return json.load(response)


def _tw_symbol(code: str, market: str) -> str:
    suffix = ".TWO" if market == "tpex" else ".TW"
    return f"{code}{suffix}"


def _tw_rows() -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for item in _fetch_json(TWSE_BASIC_URL):
        code = str(item.get("公司代號") or "").strip()
        industry_code = str(item.get("產業別") or "").strip().zfill(2)
        industry = TW_INDUSTRY_CODES.get(industry_code, industry_code)
        if code and industry:
            symbol = _tw_symbol(code, "twse")
            rows[symbol] = {
                "symbol": symbol,
                "market": "tw",
                "sector_official": industry,
                "industry_group": industry,
                "industry": industry,
                "source": "TWSE OpenAPI t187ap03_L",
            }

    for item in _fetch_json(TPEX_BASIC_URL):
        code = str(item.get("SecuritiesCompanyCode") or "").strip()
        industry_code = str(item.get("SecuritiesIndustryCode") or "").strip().zfill(2)
        industry = TW_INDUSTRY_CODES.get(industry_code, industry_code)
        if code and industry:
            symbol = _tw_symbol(code, "tpex")
            rows[symbol] = {
                "symbol": symbol,
                "market": "tw",
                "sector_official": industry,
                "industry_group": industry,
                "industry": industry,
                "source": "TPEx OpenAPI mopsfin_t187ap03_O",
            }
    return rows


def _us_row(symbol: str) -> dict | None:
    try:
        info = yf.Ticker(symbol).get_info()
    except Exception:
        return None
    sector = (info.get("sector") or "").strip()
    industry = (info.get("industry") or "").strip()
    if not sector and not industry:
        return None
    return {
        "symbol": symbol,
        "market": "us",
        "sector_official": sector or industry,
        "industry_group": industry or sector,
        "industry": industry or sector,
        "source": "yfinance quoteSummary",
    }


def main() -> None:
    watchlist = io.load_watchlist()
    by_symbol = _tw_rows()
    for entry in watchlist:
        if entry.market != "us":
            continue
        if row := _us_row(entry.symbol):
            by_symbol[entry.symbol] = row

    watch_symbols = {entry.symbol for entry in watchlist}
    rows = [row for symbol, row in by_symbol.items() if symbol in watch_symbols]
    rows.sort(key=lambda row: (row["market"], row["symbol"]))

    path = io.sector_map_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "symbol",
                "market",
                "sector_official",
                "industry_group",
                "industry",
                "source",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    missing = sorted(watch_symbols - {row["symbol"] for row in rows})
    print(f"wrote={path} rows={len(rows)} missing={len(missing)}")
    if missing:
        print("missing=" + ",".join(missing))


if __name__ == "__main__":
    main()
